"""Plain household tools: pass a StateStore first; no voice, providers or LLM calls.

All rejected changes are atomic. Mutating meal/pantry facts clears the cached
grocery list; call build_grocery_list again. Attendance changes do not guess portions.
"""

import re
from copy import deepcopy
from functools import wraps
from uuid import uuid4

from sim import instamart

from .schemas import (
    MEAL_STATUSES, PANTRY_STATUSES, Meal, canonicalize, ingredient_record,
    iso_date, quantity, text, validate_restrictions, variant_ingredients,
)
from .state import StateStore, atomic_write_json, timestamp


def _success(summary: str, data: dict) -> dict:
    return {"ok": True, "summary": summary, "data": data}


def _failure(code: str, message: str, retryable: bool = False) -> dict:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


def _tool(function):
    """Translate only expected input/persistence errors; programming errors still surface."""
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ValueError as error:
            return _failure("INVALID_INPUT", str(error))
        except OSError:
            return _failure("PERSISTENCE_ERROR", "Could not write local output. Please retry.", True)
    return call


def _restriction_error(meal: Meal, state: dict, store: StateStore) -> dict | None:
    conflicts = validate_restrictions(meal, state["members"], store.ingredients)
    if not conflicts:
        return None
    names = {member["id"]: member["name"] for member in state["members"]}
    detail = "; ".join(f"{names[entry['member_id']]} must avoid {entry['item']}" for entry in conflicts)
    return _failure("RESTRICTION_CONFLICT", detail + ".")


def _prepare_meal(raw: dict, existing: dict | None, status: str, state: dict, store: StateStore) -> Meal:
    fields = set(Meal.__required_keys__)
    if set(raw) - fields:
        raise ValueError("Meal contains unsupported fields.")
    meal = {"id": f"ml_{uuid4().hex[:12]}", "variants": {}, "notes": "", **(existing or {}), **raw}
    if existing:
        meal["id"] = existing["id"]
    if not {"date", "slot", "dish", "attendees", "ingredients"} <= meal.keys():
        raise ValueError("Meals need date, slot, dish, attendees and ingredients.")
    meal["id"] = text(meal["id"])
    meal["date"] = iso_date(meal["date"])
    meal["slot"] = text(meal["slot"]).casefold()
    if meal["slot"] not in {"breakfast", "lunch", "dinner", "snack"}:
        raise ValueError("Slot must be breakfast, lunch, dinner or snack.")
    meal["dish"] = text(meal["dish"])
    if not isinstance(meal["notes"], str):
        raise ValueError("Meal notes must be text.")
    if not isinstance(meal["attendees"], list) or not isinstance(meal["ingredients"], list):
        raise ValueError("Attendees and ingredients must be lists.")
    meal["attendees"] = list(dict.fromkeys(text(member) for member in meal["attendees"]))
    members = {member["id"] for member in state["members"]}
    if set(meal["attendees"]) - members:
        raise ValueError("Meal includes an unknown household member.")
    meal["ingredients"] = [ingredient_record(entry, store.ingredients) for entry in meal["ingredients"]]
    if not isinstance(meal["variants"], dict) or set(meal["variants"]) - members:
        raise ValueError("Variants must be keyed by known member IDs.")
    for member_id, variant in meal["variants"].items():
        ingredients = variant_ingredients(variant, store.ingredients)
        if isinstance(variant, list):
            meal["variants"][member_id] = ingredients
    meal["status"] = status
    return meal


@_tool
def get_pantry(store: StateStore, items: list[str] | None = None) -> dict:
    state = store.state
    pantry = {entry["item"]: entry for entry in state["pantry"]}
    if items is None:
        selected = list(pantry.values())
    else:
        if not isinstance(items, list):
            raise ValueError("Items must be a list of ingredient names.")
        names = dict.fromkeys(canonicalize(item, store.ingredients) for item in items)
        selected = [pantry.get(name, {"item": name, "qty": None, "unit": None, "status": "unknown"}) for name in names]
    low = sum(entry["status"] == "low" for entry in selected)
    out = sum(entry["status"] == "out" for entry in selected)
    return _success(f"Found {len(selected)} pantry items; {low} low and {out} out.", {"items": selected})


@_tool
def get_meal_plan(store: StateStore, date: str | None = None) -> dict:
    target = iso_date(date) if date is not None else None
    meals = [meal for meal in store.state["meal_plan"] if target is None or meal["date"] == target]
    return _success(f"Found {len(meals)} meals" + (f" for {target}." if target else "."), {"meals": meals})


@_tool
def save_meal_plan(store: StateStore, meals: list[dict], status: str = "draft") -> dict:
    """Upsert by existing ID, then date/slot. The status argument applies to every submitted meal."""
    if not isinstance(meals, list) or not meals:
        raise ValueError("Provide a non-empty list of meals.")
    if not isinstance(status, str) or status not in MEAL_STATUSES:
        raise ValueError("Unsupported meal status.")
    state = store.state
    saved_ids = []
    for raw in deepcopy(meals):
        if not isinstance(raw, dict):
            raise ValueError("Each meal must be an object.")
        supplied_id = text(raw["id"]) if "id" in raw else None
        existing = next((meal for meal in state["meal_plan"] if meal["id"] == supplied_id), None)
        if existing is None and "date" in raw and "slot" in raw:
            key = (iso_date(raw["date"]), text(raw["slot"]).casefold())
            existing = next((meal for meal in state["meal_plan"] if (meal["date"], meal["slot"]) == key), None)
        meal = _prepare_meal(raw, existing, status, state, store)
        if any(other["id"] != meal["id"] and (other["date"], other["slot"]) == (meal["date"], meal["slot"])
               for other in state["meal_plan"]):
            return _failure("SLOT_CONFLICT", "Another meal already occupies that date and slot.")
        error = _restriction_error(meal, state, store)
        if error:
            return error
        if existing is None:
            state["meal_plan"].append(meal)
        else:
            state["meal_plan"][state["meal_plan"].index(existing)] = meal
        saved_ids.append(meal["id"])
    state["grocery_list"] = []
    store.save(state, event_type="meal_update", summary=f"Saved {len(set(saved_ids))} meals as {status}.")
    saved = [meal for meal in state["meal_plan"] if meal["id"] in saved_ids]
    return _success(f"Saved {len(saved)} meals as {status}.", {"meals": saved})


@_tool
def move_meal(store: StateStore, meal_id: str, new_date: str) -> dict:
    meal_id, new_date = text(meal_id), iso_date(new_date)
    state = store.state
    meal = next((meal for meal in state["meal_plan"] if meal["id"] == meal_id), None)
    if meal is None:
        return _failure("MEAL_NOT_FOUND", "Meal was not found.")
    if any(other["id"] != meal_id and other["date"] == new_date and other["slot"] == meal["slot"]
           for other in state["meal_plan"]):
        return _failure("SLOT_CONFLICT", "Another meal already occupies that date and slot.")
    old_date = meal["date"]
    meal["date"] = new_date
    state["grocery_list"] = []
    store.save(state, event_type="meal_move", summary=f"Moved {meal['dish']} from {old_date} to {new_date}.", entity_id=meal_id)
    return _success("Meal moved.", {"meal_id": meal_id, "old_date": old_date, "new_date": new_date})


def _update_stock(state: dict, store: StateStore, item: str, qty: float | None, status: str | None) -> dict:
    item = canonicalize(item, store.ingredients)
    qty = quantity(qty)
    if status is not None and (not isinstance(status, str) or status not in PANTRY_STATUSES):
        raise ValueError("Pantry status must be available, low or out.")
    if qty is None and status is None:
        raise ValueError("Provide a quantity or status to update.")
    if status == "out" and qty not in (None, 0):
        raise ValueError("An out-of-stock item cannot have a positive quantity.")
    if qty == 0 and status in {"available", "low"}:
        raise ValueError("Zero stock must have status out.")
    entry = next((entry for entry in state["pantry"] if entry["item"] == item), None)
    if entry is None:
        unit = next((entry["unit"] for entry in store.ingredients if entry["canonical"] == item), None)
        entry = {"item": item, "qty": None, "unit": unit, "status": "available"}
        state["pantry"].append(entry)
    if status == "out":
        entry["qty"] = 0
    elif qty is not None:
        entry["qty"] = qty
    elif status in {"available", "low"} and entry["qty"] == 0:
        entry["qty"] = None
    if status is not None:
        entry["status"] = status
    elif qty == 0:
        entry["status"] = "out"
    elif entry["status"] == "out":
        entry["status"] = "available"
    return entry


@_tool
def update_pantry_item(store: StateStore, item: str, qty: float | None = None, status: str | None = None) -> dict:
    state = store.state
    entry = _update_stock(state, store, item, qty, status)
    state["grocery_list"] = []
    store.save(state, event_type="pantry_update", summary=f"Recorded {entry['item']} as {entry['status']}.", entity_id=entry["item"])
    return _success(f"Updated {entry['item']}.", {"item": entry})


def _base_quantity(qty: float, unit: str) -> tuple[float, str]:
    aliases = {"gram": "g", "grams": "g", "pieces": "count", "piece": "count", "pcs": "count", "bunches": "bunch"}
    unit = aliases.get(unit, unit)
    base, factor = {"kg": ("g", 1000), "l": ("ml", 1000)}.get(unit, (unit, 1))
    return qty * factor, base


@_tool
def build_grocery_list(store: StateStore) -> dict:
    """Aggregate before subtracting stock once; incompatible/unknown quantities require review."""
    state = store.state
    requirements = {}
    for meal in state["meal_plan"]:
        if meal["status"] in {"cancelled", "completed"} or not meal["attendees"]:
            continue
        ingredients = list(meal["ingredients"])
        for member_id, variant in meal["variants"].items():
            if member_id in meal["attendees"]:
                ingredients.extend(variant_ingredients(variant, store.ingredients))
        for ingredient in ingredients:
            qty, unit = _base_quantity(ingredient["qty"] or 0, ingredient["unit"])
            key = (ingredient["item"], unit)
            total = requirements.setdefault(key, {"qty": 0, "unknown": False})
            total["qty"] += qty
            total["unknown"] |= ingredient["qty"] is None
    pantry = {entry["item"]: entry for entry in state["pantry"]}
    groceries = []
    for (item, unit), required in sorted(requirements.items()):
        entry = pantry.get(item)
        usable = 0
        reason = "No usable pantry stock."
        if entry and entry["status"] != "out":
            if entry["qty"] is None or entry["unit"] is None:
                reason = "Pantry quantity or unit is unknown; review stock."
            else:
                available, stock_unit = _base_quantity(entry["qty"], entry["unit"])
                if stock_unit == unit:
                    usable = available
                    reason = f"Planned {required['qty']:g} {unit}; pantry has {usable:g} {unit}."
                else:
                    reason = "Pantry unit differs from the meal unit; review stock."
        gap = round(max(0, required["qty"] - usable), 6)
        if required["unknown"]:
            groceries.append({"item": item, "qty": None, "unit": unit, "reason": "Meal or text variant quantity unspecified; confirm the amount."})
        elif gap > 0:
            groceries.append({"item": item, "qty": gap, "unit": unit, "reason": reason})
    state["grocery_list"] = groceries
    store.save(state, event_type="grocery_update", summary=f"Built grocery gaps for {len(groceries)} items.", actor="agent")
    return _success(f"Need {len(groceries)} grocery items.", {"items": groceries})


@_tool
def report_ingredient_issue(store: StateStore, meal_id: str, item: str, issue: str,
                            substitute: str | dict | None = None) -> dict:
    meal_id = text(meal_id)
    item = canonicalize(item, store.ingredients)
    issue = text(issue).casefold()
    if issue not in {"missing", "out", "unavailable", "spoiled", "low"}:
        raise ValueError("Issue must be missing, out, unavailable, spoiled or low.")
    state = store.state
    meal = next((meal for meal in state["meal_plan"] if meal["id"] == meal_id), None)
    if meal is None:
        return _failure("MEAL_NOT_FOUND", "Meal was not found.")
    all_ingredients = list(meal["ingredients"])
    for variant in meal["variants"].values():
        all_ingredients.extend(variant_ingredients(variant, store.ingredients))
    if not any(entry["item"] == item for entry in all_ingredients):
        return _failure("INGREDIENT_NOT_IN_MEAL", "That ingredient is not part of this meal.")
    replacement = None
    if substitute is not None:
        replacement = ingredient_record({"item": substitute} if isinstance(substitute, str) else substitute, store.ingredients)

        def replace(entry):
            if entry["item"] != item:
                return entry
            new = dict(replacement)
            if isinstance(substitute, str) and _base_quantity(0, entry["unit"])[1] == _base_quantity(0, new["unit"])[1]:
                new["qty"], new["unit"] = entry["qty"], entry["unit"]
            return new

        meal["ingredients"] = [replace(entry) for entry in meal["ingredients"]]
        source = next(entry for entry in store.ingredients if entry["canonical"] == item)
        for member_id, variant in meal["variants"].items():
            if isinstance(variant, list):
                meal["variants"][member_id] = [replace(entry) for entry in variant]
            else:
                names = [source["canonical"], *source.get("aliases", [])]
                pattern = r"(?<!\w)(?:" + "|".join(re.escape(name) for name in names) + r")(?!\w)"
                meal["variants"][member_id] = re.sub(pattern, lambda _: replacement["item"], variant, flags=re.IGNORECASE)
        error = _restriction_error(meal, state, store)
        if error:
            return error
    pantry_status = "low" if issue == "low" else "out"
    _update_stock(state, store, item, None, pantry_status)
    state["grocery_list"] = []
    summary = f"Cook reported {item} as {issue}."
    if replacement:
        summary += f" Replaced it with {replacement['item']} in {meal['dish']}."
    store.save(state, event_type="substitution" if replacement else "ingredient_issue", summary=summary, actor="cook", entity_id=meal_id)
    return _success(summary, {"meal": meal, "item": item, "issue": issue})


@_tool
def set_attendance(store: StateStore, meal_id: str, member_id: str, attending: bool) -> dict:
    """Update membership only; retain quantities until a person supplies revised portions."""
    meal_id, member_id = text(meal_id), text(member_id)
    if not isinstance(attending, bool):
        raise ValueError("Attending must be true or false.")
    state = store.state
    if member_id not in {member["id"] for member in state["members"]}:
        return _failure("MEMBER_NOT_FOUND", "Household member was not found.")
    meal = next((meal for meal in state["meal_plan"] if meal["id"] == meal_id), None)
    if meal is None:
        return _failure("MEAL_NOT_FOUND", "Meal was not found.")
    if attending and member_id not in meal["attendees"]:
        meal["attendees"].append(member_id)
    elif not attending and member_id in meal["attendees"]:
        meal["attendees"].remove(member_id)
    error = _restriction_error(meal, state, store)
    if error:
        return error
    state["grocery_list"] = []
    store.save(state, event_type="attendance_update", summary=f"Set attendance for {member_id} to {attending}.", entity_id=meal_id)
    return _success(f"Meal now has {len(meal['attendees'])} attendees; quantities unchanged.",
                    {"meal_id": meal_id, "attendees": meal["attendees"], "count": len(meal["attendees"]), "quantities_changed": False})


@_tool
def create_handoff_summary(store: StateStore, reason: str, severity: str,
                           pending_action: str | dict | None = None) -> dict:
    """Export only explicit household context; no transcript, prompt or model internals."""
    reason, severity = text(reason), text(severity).casefold()
    if severity not in {"low", "medium", "high", "urgent"}:
        raise ValueError("Severity must be low, medium, high or urgent.")
    if pending_action is not None:
        if isinstance(pending_action, str):
            pending_action = text(pending_action)
        elif isinstance(pending_action, dict):
            if set(pending_action) - {"type", "meal_id", "item", "substitute", "summary"}:
                raise ValueError("Pending action accepts only type, meal_id, item, substitute and summary.")
            pending_action = {key: text(value) for key, value in pending_action.items()}
        else:
            raise ValueError("Pending action must be text, an action object or null.")
    state = store.state
    meal_id = pending_action.get("meal_id") if isinstance(pending_action, dict) else None
    active = [meal for meal in state["meal_plan"] if meal["status"] not in {"completed", "cancelled"}]
    if meal_id:
        meal = next((meal for meal in active if meal["id"] == meal_id), None)
        if meal is None:
            return _failure("MEAL_NOT_FOUND", "The pending action's active meal was not found.")
    else:
        meal = min(active, key=lambda meal: (meal["date"], meal["slot"])) if active else None
    # Whitelist fields instead of dumping active state or conversation context.
    context = {key: meal[key] for key in Meal.__required_keys__} if meal else None
    history_keys = ("timestamp", "actor", "type", "summary", "entity_id")
    handoff = {
        "handoff_id": f"ho_{uuid4().hex[:12]}", "timestamp": timestamp(),
        "reason": reason, "severity": severity, "household_id": state["household"]["id"],
        "current_meal": context, "pending_action": pending_action,
        "recent_history": [{key: event.get(key) for key in history_keys} for event in state["history"][-10:]],
        "summary": f"Household {state['household']['id']} needs help: {reason}",
        "suggested_next_step": "A household member should review the issue and pending action before proceeding.",
    }
    path = store.runtime_path.parent / "handoff.json"
    if path.resolve() in {store.seed_path, store.runtime_path}:
        raise ValueError("Handoff output must not overwrite seed or runtime state.")
    atomic_write_json(path, handoff)
    return _success("Handoff context saved locally; no transfer was initiated.", {"handoff": handoff})


def _simulated_tool(function):
    """Keep simulation explicit on both successful and rejected simulator calls."""
    checked = _tool(function)

    @wraps(function)
    def call(*args, **kwargs):
        result = checked(*args, **kwargs)
        result["data" if result["ok"] else "error"]["simulated"] = True
        return result
    return call


def _is_explicit_confirmation(user_quote: str) -> bool:
    # Phrase check only: the planner separately verifies source/turn/preview relevance.
    # Keep the exact raw transcript for placement; normalize only this allowlist check.
    normalized = user_quote.casefold().translate(str.maketrans("", "", ",.!?।")) if isinstance(user_quote, str) else ""
    return " ".join(normalized.split()) in {
        "yes", "yes place it", "go ahead", "place it", "order it", "haan",
        "haan kar do", "yes order it", "हाँ", "हां", "हाँ कर दो", "हां कर दो",
        "okay go ahead", "ok go ahead", "okay place it", "okay place the order",
        "place the order", "yes place the order",
    }


@_simulated_tool
def preview_instamart_order(store: StateStore) -> dict:
    state = store.state
    cart = instamart.build_cart(state["grocery_list"])
    confirmation_id = instamart.confirmation_id()
    state["pending_confirmation"] = {
        "id": confirmation_id, "type": "instamart_order",
        "cart_hash": instamart.cart_hash(state["grocery_list"], cart),
        "created_at": timestamp(), "created_turn": None, "last_user_turn": None,
        "consumed": False,
    }
    store.save(state, event_type="simulated_order_preview", actor="agent",
               summary="Previewed simulated Instamart cart; available items only.", entity_id=confirmation_id)
    return _success("Simulated Instamart preview: available items only; substitutions are suggestions, not added.",
                    {**cart, "confirmation_id": confirmation_id})


@_simulated_tool
def place_instamart_order(store: StateStore, confirmation_id: str, user_quote: str) -> dict:
    """Phase 2 application gate; Phase 3 must supply verified conversation context."""
    state = store.state
    pending = state["pending_confirmation"]
    if pending is None:
        return _failure("NO_PENDING_CONFIRMATION", "Preview a simulated cart first.")
    if pending["type"] != "instamart_order" or pending["id"] != confirmation_id:
        return _failure("CONFIRMATION_MISMATCH", "Confirmation does not match the current simulated preview.")
    if pending["consumed"]:
        return _failure("CONFIRMATION_CONSUMED", "This simulated confirmation has already been used.")
    cart = instamart.build_cart(state["grocery_list"])
    if pending["cart_hash"] != instamart.cart_hash(state["grocery_list"], cart):
        state["pending_confirmation"] = None
        store.save(state, event_type="simulated_preview_invalidated", actor="agent",
                   summary="Simulated cart changed; a new preview is required.", entity_id=confirmation_id)
        return _failure("STALE_CONFIRMATION", "The simulated cart changed; preview it again.")
    if not _is_explicit_confirmation(user_quote):
        return _failure("CONFIRMATION_REQUIRED", "A clear affirmative is required for this simulated preview.")
    if not cart["available_items"]:
        return _failure("EMPTY_CART", "No available items to order in this simulation.")
    order_id = instamart.order_id()
    pending["consumed"] = True
    store.save(state, event_type="simulated_order_placed", actor="user", entity_id=order_id,
               summary=f"Simulated Instamart order {order_id}: INR {cart['total_inr']}; ETA {cart['eta']}; "
                       f"{len(cart['available_items'])} available lines. Unavailable items excluded.")
    return _success("Simulated Instamart order placed.", {
        "order_id": order_id, "total_inr": cart["total_inr"], "eta": cart["eta"], "simulated": True,
    })
