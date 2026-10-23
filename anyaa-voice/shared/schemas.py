"""Small JSON-compatible records and exact ingredient aliases; no SDK dependencies."""

import math
import re
from datetime import date
from typing import Literal, TypedDict


class Ingredient(TypedDict):
    item: str
    qty: float | None
    unit: str | None


class PantryItem(Ingredient):
    status: Literal["available", "low", "out"]


class Meal(TypedDict):
    id: str
    date: str
    slot: str
    dish: str
    attendees: list[str]
    ingredients: list[Ingredient]
    variants: dict[str, str | list[Ingredient]]
    notes: str
    status: str


class HistoryEvent(TypedDict):
    timestamp: str
    actor: str
    type: str
    summary: str
    entity_id: str | None


class HouseholdState(TypedDict):
    household: dict
    members: list[dict]
    cook: dict
    pantry: list[PantryItem]
    meal_plan: list[Meal]
    grocery_list: list[dict]
    pending_confirmation: dict | None
    history: list[HistoryEvent]


MEAL_STATUSES = {"draft", "planned", "confirmed", "completed", "cancelled"}
PANTRY_STATUSES = {"available", "low", "out"}


def text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a non-empty string.")
    return value.strip()


def canonicalize(item: str, catalog: list[dict]) -> str:
    """Resolve exact normalized aliases; unknown names remain normalized names."""
    name = text(item).casefold()
    for ingredient in catalog:
        aliases = [ingredient["canonical"], *ingredient.get("aliases", [])]
        if name in [alias.strip().casefold() for alias in aliases]:
            return ingredient["canonical"]
    return name


def quantity(value: float | None) -> float | None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float))
        or not math.isfinite(value) or value < 0
    ):
        raise ValueError("Quantity must be a finite non-negative number or null.")
    return value


def iso_date(value: str) -> str:
    value = text(value)
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("Date must use YYYY-MM-DD.")
    return value


def ingredient_record(value: dict, catalog: list[dict]) -> Ingredient:
    if not isinstance(value, dict) or set(value) - {"item", "qty", "unit"}:
        raise ValueError("Ingredients accept item, qty and unit only.")
    item = canonicalize(value.get("item"), catalog)
    known = next((entry for entry in catalog if entry["canonical"] == item), None)
    if known is None:
        raise ValueError(f"Unknown meal ingredient: {item}. Add it to the demo catalog first.")
    qty = quantity(value.get("qty"))
    unit = value.get("unit", known["unit"])
    if unit is not None:
        unit = text(unit).casefold()
    if qty is not None and unit is None:
        raise ValueError("A known ingredient quantity needs a unit.")
    return {"item": item, "qty": qty, "unit": unit}


def variant_ingredients(variant: str | list[Ingredient], catalog: list[dict]) -> list[Ingredient]:
    """Lists carry quantities. Text variants match catalog words, never infer quantities."""
    if isinstance(variant, list):
        return [ingredient_record(entry, catalog) for entry in variant]
    phrase = text(variant).casefold()
    found = []
    for entry in catalog:
        names = [entry["canonical"], *entry.get("aliases", [])]
        if any(re.search(r"(?<!\w)" + re.escape(name.casefold()) + r"(?!\w)", phrase) for name in names):
            found.append({"item": entry["canonical"], "qty": None, "unit": entry["unit"]})
    if not found:
        raise ValueError("Text variants must name a catalog ingredient; put cooking guidance in notes.")
    return found


def validate_restrictions(meal: Meal, members: list[dict], catalog: list[dict]) -> list[dict]:
    """Check shared ingredients for everyone, and additions only for their named member."""
    tags = {entry["canonical"]: set(entry.get("tags", [])) for entry in catalog}
    conflicts = []
    for member in members:
        if member["id"] not in meal["attendees"]:
            continue
        ingredients = list(meal["ingredients"])
        variant = meal["variants"].get(member["id"])
        if variant is not None:
            ingredients.extend(variant_ingredients(variant, catalog))
        for ingredient in ingredients:
            blocked = set(member.get("hard_avoid", [])) & tags[ingredient["item"]]
            if blocked:
                conflicts.append({"member_id": member["id"], "item": ingredient["item"], "tags": sorted(blocked)})
    return conflicts
