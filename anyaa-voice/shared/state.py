"""Single-process local state. Persist a complete JSON snapshot before publishing it in memory."""

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .schemas import HouseholdState

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class StateStore:
    def __init__(
        self,
        runtime_path: str | Path = PROJECT_ROOT / "out/state.json",
        seed_path: str | Path = PROJECT_ROOT / "data/household.json",
        ingredients_path: str | Path = PROJECT_ROOT / "data/ingredients.json",
    ) -> None:
        self.runtime_path = Path(runtime_path).resolve()
        self.seed_path = Path(seed_path).resolve()
        ingredients_path = Path(ingredients_path).resolve()
        if self.runtime_path in (self.seed_path, ingredients_path):
            raise ValueError("Runtime output must not overwrite seed data.")
        source = self.runtime_path if self.runtime_path.exists() else self.seed_path
        self._state: HouseholdState = json.loads(source.read_text(encoding="utf-8"))
        required = set(HouseholdState.__required_keys__)
        if not isinstance(self._state, dict) or not required <= self._state.keys():
            raise ValueError("State file is missing required household fields.")
        self.ingredients = json.loads(ingredients_path.read_text(encoding="utf-8"))

    @property
    def state(self) -> HouseholdState:
        """Return a snapshot so readers cannot accidentally mutate live state."""
        return deepcopy(self._state)

    def save(self, state: HouseholdState, *, event_type: str, summary: str,
             actor: str = "user", entity_id: str | None = None) -> None:
        candidate = deepcopy(state)
        # Any grocery change invalidates even a later change back to the old cart.
        # Meal/pantry changes also invalidate previews before groceries are rebuilt.
        if any(candidate[key] != self._state[key] for key in ("grocery_list", "meal_plan", "pantry")):
            candidate["pending_confirmation"] = None
        candidate["history"].append({
            "timestamp": timestamp(), "actor": actor, "type": event_type,
            "summary": summary, "entity_id": entity_id,
        })
        candidate["history"] = candidate["history"][-50:]
        atomic_write_json(self.runtime_path, candidate)
        self._state = candidate
