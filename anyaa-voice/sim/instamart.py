"""Deterministic demo prices/stock, never live Instamart data. No network calls."""

import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

from shared.schemas import quantity, text

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data/instamart_catalog.json"


def load_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def build_cart(groceries: list[dict]) -> dict:
    """Round up to whole demo packs; never add substitutes or guess unknown amounts."""
    catalog = {product["canonical_item"]: product for product in load_catalog()}
    available, unavailable, substitutions = [], [], []
    for grocery in groceries:
        item = text(grocery["item"]).casefold()
        qty = quantity(grocery.get("qty"))
        if qty == 0:
            continue
        product = catalog.get(item)
        reason = None
        if product is None:
            reason = "Not in the simulated catalog."
        elif not product["in_stock"]:
            reason = "Out of stock in the simulated catalog."
        elif qty is None:
            reason = "Confirm quantity before ordering."
        else:
            pack_qty, pack_unit = product["pack_size"].split()
            unit = text(grocery.get("unit")).casefold()
            factor, unit = {"kg": (1000, "g"), "grams": (1, "g"), "gram": (1, "g"),
                            "pieces": (1, "count"), "piece": (1, "count"),
                            "pcs": (1, "count"), "bunches": (1, "bunch")}.get(unit, (1, unit))
            if unit != pack_unit:
                reason = "Quantity unit does not match the demo pack; review before ordering."
            else:
                packs = math.ceil(qty * factor / float(pack_qty))
                available.append({**product, "packs": packs, "line_total_inr": packs * product["price_inr"]})
        if reason:
            unavailable.append({**grocery, "item": item, "reason": reason})
            substitute = catalog.get(product["substitute_item"]) if product else None
            if substitute and substitute["in_stock"]:
                substitutions.append({"item": item, "substitute_item": substitute["canonical_item"],
                                      "product": substitute})
    subtotal = sum(line["line_total_inr"] for line in available)
    delivery = 29 if available else 0
    return {
        "available_items": available, "unavailable_items": unavailable,
        "suggested_substitutions": substitutions, "subtotal_inr": subtotal,
        "delivery_fee_inr": delivery, "total_inr": subtotal + delivery,
        "eta": "18-25 min" if available else None,
        "prices_illustrative": True, "simulated": True,
    }


def cart_hash(groceries: list[dict], cart: dict) -> str:
    """Include source quantities and exclusions as well as priced packs in the binding."""
    payload = json.dumps({"groceries": groceries, "cart": cart}, sort_keys=True,
                         ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def confirmation_id() -> str:
    return f"confirm_{uuid4().hex}"


def order_id() -> str:
    return f"SIM-{uuid4().hex[:16].upper()}"
