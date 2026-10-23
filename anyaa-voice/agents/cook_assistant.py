"""Daily cooking surface over the existing household tools and state."""

from datetime import date

from livekit.agents import Agent, function_tool, llm

from shared import tools
from shared.prompts import cook_instructions
from shared.schemas import canonicalize
from shared.state import StateStore


def _affirmative(text: str) -> bool:
    phrase = " ".join(text.casefold().translate(str.maketrans("", "", ",.!?।")).split())
    return phrase in {"yes", "yes use it", "go ahead", "ಹೌದು", "ಹೌದು ಮೆಂತ್ಯ ಹಾಕಿ",
                      "ಮೆಂತ್ಯ ಹಾಕಿ", "ಹಾಂ", "हाँ", "हां", "हाँ कर दो", "हां कर दो", "haan", "haan kar do"}


class CookAssistantAgent(Agent):
    def __init__(self, store: StateStore, today: date | None = None, language: str = "kn"):
        if language not in {"kn", "hi", "en"}:
            raise ValueError("Cook language must be kn, hi or en.")
        self.store = store
        self.today = today or date.today()
        self.language = language
        self.misunderstandings = 0
        self._misunderstanding_turn = None
        self.emergency = False
        self.clarification_reply = None
        self.latest_user_text = ""
        self.latest_user_id = None
        self.pending_substitution = None
        self._pending_issue = None
        self._proposal_turn = None
        self._proposal_meal = None
        self._confirmation_for_turn = False
        self._uncertain = False
        self._last_handoff = None
        self.tool_events = []
        super().__init__(instructions=cook_instructions(store.state, self.today, language))

    def user_started_speaking(self) -> None:
        self.latest_user_text = ""
        self.latest_user_id = None
        self._confirmation_for_turn = False
        self.clarification_reply = None

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage) -> None:
        self.latest_user_text = new_message.text_content or ""
        self.latest_user_id = new_message.id
        self.tool_events = []
        self.clarification_reply = None
        if any("\u0c80" <= char <= "\u0cff" for char in self.latest_user_text):
            self.language = "kn"
        elif any("\u0900" <= char <= "\u097f" for char in self.latest_user_text):
            self.language = "hi"
        elif self.latest_user_text.isascii() and any(char.isalpha() for char in self.latest_user_text):
            self.language = "en"
        self._uncertain = not self.latest_user_text.strip() or (
            new_message.transcript_confidence is not None and new_message.transcript_confidence < 0.65)
        is_confirmation = _affirmative(self.latest_user_text)
        self._confirmation_for_turn = bool(
            self.pending_substitution and not self._uncertain
            and is_confirmation and new_message.id != self._proposal_turn)
        if not is_confirmation or self._uncertain:
            self.pending_substitution = None
        if self.emergency:
            self.clarification_reply = self._emergency_reply()
        elif self._uncertain:
            self.record_misunderstanding()

    async def llm_node(self, chat_ctx, tools, model_settings):
        if self.clarification_reply:
            return self.clarification_reply
        return Agent.default.llm_node(self, chat_ctx, tools, model_settings)

    def _run_tool(self, name: str, **arguments) -> dict:
        if name != "create_handoff_summary" and (self.emergency or self._uncertain):
            return self._failure("CLARIFICATION_REQUIRED", "Get medical help or clarify the cook's request before continuing.")
        result = getattr(tools, name)(self.store, **arguments)
        self.tool_events.append({"name": name, "arguments": arguments, "result": result})
        if result["ok"] and name != "create_handoff_summary":
            self.misunderstandings = 0
            self._misunderstanding_turn = None
            self._last_handoff = None
        return result

    @staticmethod
    def _failure(code: str, message: str) -> dict:
        return {"ok": False, "error": {"code": code, "message": message, "retryable": False}}

    def _meal(self, meal_id: str | None = None) -> dict | None:
        return next((meal for meal in self.store.state["meal_plan"] if (meal_id is None or meal["id"] == meal_id)
                     and meal["date"] == self.today.isoformat() and meal["status"] in {"planned", "confirmed"}), None)

    def _handoff(self, reason: str, severity: str, pending_action: str | None = None) -> dict:
        pending = self.pending_substitution or self._pending_issue
        if pending is None:
            meal = self._meal()
            pending = {"meal_id": meal["id"]} if meal else {}
        action = {key: pending[key] for key in ("meal_id", "item", "substitute") if key in pending}
        action.update(type="cook_assistance", summary=pending_action or reason)
        return self._run_tool("create_handoff_summary", reason=reason, severity=severity, pending_action=action)

    def record_misunderstanding(self) -> dict:
        # Repeated model tool calls within one user turn are only one misunderstanding.
        if self.latest_user_id is None or self._misunderstanding_turn != self.latest_user_id:
            self.misunderstandings = min(self.misunderstandings + 1, 3)
            self._misunderstanding_turn = self.latest_user_id
        self._uncertain = True
        self._confirmation_for_turn = False
        self.pending_substitution = None
        replies = {
            "kn": ("ಇನ್ನೊಂದು ಸತಿ ನಿಧಾನವಾಗಿ ಹೇಳಿ.", "ಹಿಂದಿ ಅಥವಾ Englishನಲ್ಲಿ ಹೇಳಬಹುದಾ?", "ಮನೆಯವರ ಸಹಾಯ ಬೇಕು. ವಿವರಗಳನ್ನು ಉಳಿಸಿದ್ದೀನಿ."),
            "hi": ("एक बार धीरे से फिर कहिए।", "क्या आप हिंदी या अंग्रेज़ी में बता सकते हैं?", "घर के किसी सदस्य की मदद चाहिए। जानकारी सेव कर दी है।"),
            "en": ("Please say that again slowly.", "Could you try Hindi or English?", "A household member needs to help. I saved the details."),
        }
        result = {"ok": True, "summary": "Ask for clarification.", "data": {"handoff_saved": False}}
        if self.misunderstandings == 3:
            if self._last_handoff is None or not self._last_handoff["ok"]:
                self._last_handoff = self._handoff("Repeated difficulty understanding the cook", "high")
            result = self._last_handoff
        self.clarification_reply = replies[self.language][self.misunderstandings - 1] if result["ok"] else replies[self.language][0]
        result["data" if result["ok"] else "error"]["clarification"] = self.clarification_reply
        return result

    def _emergency_reply(self) -> str:
        return {"kn": "ತಕ್ಷಣ ತುರ್ತು ವೈದ್ಯಕೀಯ ಸಹಾಯ ಪಡೆಯಿರಿ.",
                "hi": "तुरंत आपातकालीन चिकित्सा सहायता लें।",
                "en": "Please seek emergency medical help now."}[self.language]

    @function_tool
    async def get_pantry(self, items: list[str] | None = None) -> dict:
        """Read actual pantry stock. Unknown quantity or missing item is not available stock."""
        return self._run_tool("get_pantry", items=items)

    @function_tool
    async def get_meal_plan(self) -> dict:
        """Retrieve today's meals and their real quantities, variants, attendees and status."""
        return self._run_tool("get_meal_plan", date=self.today.isoformat())

    @function_tool
    async def update_pantry_item(self, item: str, qty: float | None = None, status: str | None = None) -> dict:
        """Record only the cook's explicit stock facts; never guess quantities. Status available/low/out."""
        return self._run_tool("update_pantry_item", item=item, qty=qty, status=status)

    @function_tool
    async def report_ingredient_issue(self, meal_id: str, item: str, issue: str, substitute: str | None = None) -> dict:
        """Report today's missing/out/unavailable/spoiled/low ingredient. A substitute name
        first PROPOSES it, recording the missing stock fact without changing the meal.
        After a separate explicit cook confirmation, repeat the identical arguments to
        APPLY it. Check applied in the result. Restrictions are checked by the shared tool.
        """
        meal = self._meal(meal_id)
        if meal is None:
            return self._failure("NO_CURRENT_MEAL", "Choose a planned or confirmed meal for today first.")
        proposal = {"meal_id": meal_id, "item": canonicalize(item, self.store.ingredients), "issue": issue}
        if substitute is not None:
            proposal["substitute"] = canonicalize(substitute, self.store.ingredients)
            if (self._confirmation_for_turn and self.pending_substitution == proposal
                    and meal == self._proposal_meal):
                self.pending_substitution = None
                self._confirmation_for_turn = False
                result = self._run_tool("report_ingredient_issue", **proposal)
                if result["ok"]:
                    result["data"]["applied"] = True
                    self._pending_issue = None
                return result
        result = self._run_tool("report_ingredient_issue", meal_id=meal_id, item=item, issue=issue)
        if result["ok"]:
            self._pending_issue = proposal
            if substitute is not None:
                self.pending_substitution = proposal
                self._proposal_turn = self.latest_user_id
                self._proposal_meal = result["data"]["meal"]
                result["data"].update(applied=False, proposed_substitute=proposal["substitute"], confirmation_required=True)
        return result

    @function_tool
    async def set_attendance(self, meal_id: str, member_id: str, attending: bool) -> dict:
        """Update today's planned/confirmed meal attendance. Portions are not recalculated."""
        if self._meal(meal_id) is None:
            return self._failure("NO_CURRENT_MEAL", "Choose a planned or confirmed meal for today first.")
        return self._run_tool("set_attendance", meal_id=meal_id, member_id=member_id, attending=attending)

    @function_tool
    async def create_handoff_summary(self, reason: str, severity: str,
                                     pending_action: str | None = None, misunderstood: bool = False) -> dict:
        """Save explicit cooking context locally, without contacting anyone. Severity
        low/medium/high/urgent. Set misunderstood=true whenever the cook's meaning is
        unclear: first/second calls ask for clarification; third saves a handoff.
        For severe allergy or medical emergency use urgent, misunderstood=false.
        """
        severity = severity.casefold().strip()
        if severity == "urgent":
            self.emergency = True
            self._confirmation_for_turn = False
            self.clarification_reply = self._emergency_reply()
        elif misunderstood:
            return self.record_misunderstanding()
        return self._handoff(reason, severity, pending_action)
