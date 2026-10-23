"""LiveKit planner with an application-owned latest-turn confirmation gate."""

from datetime import date, timedelta
from calendar import day_name
import json

from livekit.agents import Agent, RunContext, function_tool, llm

from shared import tools
from shared.prompts import planner_instructions
from shared.state import StateStore

class HouseholdPlannerAgent(Agent):
    def __init__(self, store: StateStore, today: date | None = None):
        self.store = store
        self.latest_user_text = ""
        self.latest_user_id = None
        self._offered_confirmation = None
        self._confirmation_for_turn = None
        self.clarification_reply = None
        self.tool_events = []
        super().__init__(instructions=planner_instructions(store.state, today))

    def user_started_speaking(self) -> None:
        # Fail closed as soon as speech starts, before STT finishes the new request.
        self.latest_user_text = ""
        self.latest_user_id = None
        self._confirmation_for_turn = None
        self.clarification_reply = None

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        self.latest_user_text = new_message.text_content or ""
        self.latest_user_id = new_message.id
        self.tool_events = []
        pending = self.store.state["pending_confirmation"]
        normalized = self.latest_user_text.casefold().strip().rstrip(".!?")
        relevant = tools._is_explicit_confirmation(self.latest_user_text) or normalized == "okay"
        self._confirmation_for_turn = (
            pending["id"] if pending and not pending["consumed"] and relevant
            and self._offered_confirmation == pending["id"] else None
        )
        self.clarification_reply = (
            "Shall I place this simulated order? Please say yes."
            if normalized == "okay" and self._confirmation_for_turn else None
        )
        if not relevant:
            self._offered_confirmation = None
            if pending and not pending["consumed"]:
                state = self.store.state
                state["pending_confirmation"] = None
                self.store.save(state, event_type="confirmation_context_changed",
                                summary="A new user request requires a fresh simulated preview.")

    async def llm_node(self, chat_ctx, tools, model_settings):
        # Ambiguous checkout acknowledgments always get one clear question, no tool call.
        if self.clarification_reply:
            return self.clarification_reply
        return Agent.default.llm_node(self, chat_ctx, tools, model_settings)

    def _run_tool(self, name: str, **arguments) -> dict:
        result = getattr(tools, name)(self.store, **arguments)
        self.tool_events.append({"name": name, "arguments": arguments, "result": result})
        return result

    @function_tool
    async def get_pantry(self, items: list[str] | None = None) -> dict:
        """Read current pantry quantities/status; optional canonical item names."""
        return self._run_tool("get_pantry", items=items)

    @function_tool
    async def get_meal_plan(self, date: str | None = None) -> dict:
        """Read current meals, ingredients and member variants; optional YYYY-MM-DD date."""
        return self._run_tool("get_meal_plan", date=date)

    @function_tool
    async def save_meal_plan(self, meal_json: str) -> dict:
        """Save ONE draft meal. meal_json is a JSON STRING containing a meal object:
        {"date":"YYYY-MM-DD","slot":"dinner","dish":"chosen dish","attendees":["m1","m2"],
        "ingredients":[{"item":"paneer","qty":400,"unit":"g"}],
        "variants":{"m1":[{"item":"chicken","qty":150,"unit":"g"}]},"notes":"mild"}.
        Ingredients MUST be objects with item/qty/unit, not strings. For an existing
        meal include its exact "id"; patches may contain only id and changed fields.
        After revising a previewed cart, call preview_instamart_order again.
        """
        # Keep the provider schema flat; the existing application validates the record.
        try:
            meal = json.loads(meal_json)
        except (ValueError, TypeError):
            result = {"ok": False, "error": {"code": "INVALID_INPUT",
                      "message": "Provide one valid JSON meal object with ingredient records.", "retryable": False}}
            self.tool_events.append({"name": "save_meal_plan", "arguments": {"meal_json": meal_json}, "result": result})
            return result
        return self._run_tool("save_meal_plan", meals=[meal], status="draft")

    @function_tool
    async def move_meal(self, meal_id: str, new_date: str) -> dict:
        """Move an existing meal to YYYY-MM-DD, preserving its ID and avoiding duplicates."""
        # Validate explicit weekday corrections against the calendar, not model arithmetic.
        requested = [index for index, name in enumerate(day_name) if name.casefold() in self.latest_user_text.casefold()]
        try:
            target = date.fromisoformat(new_date)
        except ValueError:
            target = None
        meal = next((meal for meal in self.store.state["meal_plan"] if meal["id"] == meal_id), None)
        message = None
        if len(requested) == 1 and target:
            if target.weekday() != requested[0]:
                message = f"{new_date} is {target:%A}, not {day_name[requested[0]]}. Use the supplied calendar."
            elif meal and new_date == meal["date"]:
                message = f"The meal is already on that date. The following {target:%A} is {target + timedelta(days=7)}."
        if message:
            result = {"ok": False, "error": {"code": "WEEKDAY_MISMATCH", "message": message, "retryable": False}}
            self.tool_events.append({"name": "move_meal", "arguments": {"meal_id": meal_id, "new_date": new_date}, "result": result})
            return result
        return self._run_tool("move_meal", meal_id=meal_id, new_date=new_date)

    @function_tool
    async def update_pantry_item(self, item: str, qty: float | None = None, status: str | None = None) -> dict:
        """Record user-stated pantry facts. Status available/low/out; quantity in catalog units."""
        return self._run_tool("update_pantry_item", item=item, qty=qty, status=status)

    @function_tool
    async def build_grocery_list(self) -> dict:
        """Build and save grocery gaps from active meals, variants and current pantry."""
        return self._run_tool("build_grocery_list")

    @function_tool
    async def preview_instamart_order(self) -> dict:
        """Refresh grocery gaps, then preview a SIMULATED available-items cart with total, ETA,
        exclusions and substitute suggestions. Return a new confirmation ID; no order.
        """
        # Meal/pantry edits clear the cache. Never mistake that empty cache for no needs.
        result = self._run_tool("build_grocery_list")
        if not result["ok"]:
            result["error"]["simulated"] = True
            return result
        result = self._run_tool("preview_instamart_order")
        if result["ok"]:
            self._offered_confirmation = result["data"]["confirmation_id"]
        return result

    @function_tool
    async def place_instamart_order(self, context: RunContext, confirmation_id: str) -> dict:
        """Place a SIMULATED cart only after a new explicit user yes to that preview.
        Supply only its confirmation ID. The application supplies the real latest user text.
        """
        latest = next((item for item in reversed(context.session.history.items)
                       if isinstance(item, llm.ChatMessage) and item.role == "user"), None)
        current_speech = context.session.current_speech
        if (context.speech_handle.interrupted or latest is None
                or (current_speech is not None and current_speech.id != context.speech_handle.id)
                or latest.id != self.latest_user_id or not self.latest_user_text
                or self._confirmation_for_turn != confirmation_id):
            result = {"ok": False, "error": {"code": "FRESH_CONFIRMATION_REQUIRED",
                      "message": "Show a fresh simulated preview and ask for a new explicit confirmation.",
                      "retryable": False, "simulated": True}}
            self.tool_events.append({"name": "place_instamart_order", "arguments": {"confirmation_id": confirmation_id}, "result": result})
            return result
        return self._run_tool("place_instamart_order", confirmation_id=confirmation_id,
                              user_quote=self.latest_user_text)

    @function_tool
    async def set_attendance(self, meal_id: str, member_id: str, attending: bool) -> dict:
        """Set an attendee's presence for an existing meal; portions remain unchanged."""
        return self._run_tool("set_attendance", meal_id=meal_id, member_id=member_id, attending=attending)

    @function_tool
    async def create_handoff_summary(self, reason: str, severity: str, pending_action: str | None = None) -> dict:
        """Export explicit household context locally for human review; severity low/medium/high/urgent.
        Does not transfer a call or contact anyone.
        """
        return self._run_tool("create_handoff_summary", reason=reason, severity=severity, pending_action=pending_action)
