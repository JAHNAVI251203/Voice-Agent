"""Provider-free Phase 1/2 checks. Run: python -m unittest discover -s tests -v"""

import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from shared import tools
from shared.state import PROJECT_ROOT, StateStore
from sim import instamart


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "out/state.json"
        self.seed = PROJECT_ROOT / "data/household.json"
        self.original_seed = self.seed.read_bytes()
        self.store = StateStore(runtime_path=self.path)
        self.addCleanup(lambda: self.assertEqual(self.seed.read_bytes(), self.original_seed))

    def meal(self, **changes):
        meal = deepcopy(self.store.state["meal_plan"][0])
        meal.update(changes)
        return meal

    def success(self, result):
        self.assertEqual(set(result), {"ok", "summary", "data"}, result)
        self.assertIs(result["ok"], True, result)
        self.assertIsInstance(result["summary"], str)
        self.assertIsInstance(result["data"], dict)
        return result["data"]

    def failure(self, result, code):
        self.assertEqual(set(result), {"ok", "error"}, result)
        self.assertIs(result["ok"], False)
        self.assertEqual(set(result["error"]), {"code", "message", "retryable"})
        self.assertEqual(result["error"]["code"], code, result)
        return result["error"]

    def test_seed_loads_household_members_cook_and_pending_state(self):
        state = self.store.state
        self.assertEqual(state["household"], {"id": "hh_001", "city": "Bengaluru", "preferred_store": "instamart"})
        self.assertEqual([member["name"] for member in state["members"]], ["Rohan", "Meera", "Appa", "Aarav"])
        self.assertFalse(state["members"][2]["resident"])
        self.assertEqual(state["cook"]["languages"], ["Kannada", "Hindi"])
        self.assertIsNone(state["pending_confirmation"])
        self.assertFalse(self.path.exists())

    def test_existing_pantry_quantity_is_returned(self):
        items = self.success(tools.get_pantry(self.store, ["paneer"]))["items"]
        self.assertEqual(items[0]["qty"], 200)
        self.assertEqual(items[0]["unit"], "g")

    def test_exact_aliases_case_and_whitespace_are_normalized(self):
        items = self.success(tools.get_pantry(self.store, [" PALAK ", "ಪಾಲಕ್", "soppu"]))["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["item"], "spinach")
        self.assertEqual(items[0]["qty"], 0)
        unknown = self.success(tools.get_pantry(self.store, ["pala"]))["items"][0]
        self.assertEqual(unknown["item"], "pala")
        self.assertIsNone(unknown["qty"])
        self.assertEqual(unknown["status"], "unknown")

    def test_full_pantry_and_empty_filter(self):
        self.assertEqual(len(self.success(tools.get_pantry(self.store))["items"]), 9)
        self.assertEqual(self.success(tools.get_pantry(self.store, []))["items"], [])

    def test_update_pantry_records_fact_and_persists(self):
        item = self.success(tools.update_pantry_item(self.store, " EGG ", qty=2, status="low"))["item"]
        self.assertEqual(item, {"item": "eggs", "qty": 2, "unit": "count", "status": "low"})
        self.assertIsNone(self.store.state["pending_confirmation"])
        self.assertEqual(StateStore(runtime_path=self.path).state, self.store.state)

    def test_new_pantry_item_uses_catalog_unit_without_guessing_quantity(self):
        item = self.success(tools.update_pantry_item(self.store, "tofu", qty=200))["item"]
        self.assertEqual(item["unit"], "g")
        self.assertEqual(item["qty"], 200)
        unknown = self.success(tools.update_pantry_item(self.store, "garlic", status="low"))["item"]
        self.assertIsNone(unknown["qty"])
        self.assertIsNone(unknown["unit"])

    def test_out_of_stock_and_restock(self):
        out = self.success(tools.update_pantry_item(self.store, "paneer", status="out"))["item"]
        self.assertEqual(out["qty"], 0)
        item = self.success(tools.update_pantry_item(self.store, "paneer", qty=100))["item"]
        self.assertEqual(item["status"], "available")

    def test_invalid_pantry_updates_leave_state_unchanged(self):
        before = self.store.state
        for qty in [-1, True, float("nan"), float("inf"), "four"]:
            with self.subTest(qty=qty):
                self.failure(tools.update_pantry_item(self.store, "eggs", qty=qty), "INVALID_INPUT")
        self.failure(tools.update_pantry_item(self.store, "eggs"), "INVALID_INPUT")
        self.failure(tools.update_pantry_item(self.store, "eggs", qty=4, status="out"), "INVALID_INPUT")
        self.failure(tools.update_pantry_item(self.store, "eggs", qty=0, status="available"), "INVALID_INPUT")
        self.assertEqual(self.store.state, before)
        self.assertFalse(self.path.exists())

    def test_status_only_restock_clears_stale_zero_without_inventing_stock(self):
        item = self.success(tools.update_pantry_item(self.store, "spinach", status="low"))["item"]
        self.assertEqual(item["status"], "low")
        self.assertIsNone(item["qty"])
        self.success(tools.update_pantry_item(self.store, "spinach", status="out"))
        self.success(tools.report_ingredient_issue(self.store, "ml_1", "spinach", "low"))
        item = self.success(tools.get_pantry(self.store, ["spinach"]))["items"][0]
        self.assertIsNone(item["qty"])

    def test_state_and_tool_results_are_snapshots(self):
        snapshot = self.store.state
        snapshot["pantry"][0]["qty"] = 99999
        items = self.success(tools.get_pantry(self.store))["items"]
        items[0]["qty"] = 99999
        self.assertEqual(self.store.state["pantry"][0]["qty"], 1000)

    def test_runtime_must_not_overwrite_seed_or_catalog(self):
        for path in (self.seed, PROJECT_ROOT / "data/ingredients.json"):
            with self.assertRaises(ValueError):
                StateStore(runtime_path=path)

    def test_corrupt_runtime_does_not_silently_reset_to_seed(self):
        self.path.parent.mkdir()
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            StateStore(runtime_path=self.path)
        self.assertEqual(self.path.read_text(), "{broken")

    def test_failed_atomic_replace_keeps_memory_and_disk_unchanged(self):
        self.success(tools.update_pantry_item(self.store, "paneer", qty=300))
        before, disk = self.store.state, self.path.read_bytes()
        with patch("shared.state.os.replace", side_effect=PermissionError("private disk detail")):
            error = self.failure(tools.update_pantry_item(self.store, "paneer", qty=500), "PERSISTENCE_ERROR")
        self.assertTrue(error["retryable"])
        self.assertNotIn("private disk detail", error["message"])
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_new_meal_is_added_with_generated_id(self):
        new = self.meal(date="2026-10-25", slot="lunch")
        del new["id"]
        saved = self.success(tools.save_meal_plan(self.store, [new]))["meals"][0]
        self.assertTrue(saved["id"].startswith("ml_"))
        self.assertNotEqual(saved["id"], "ml_1")
        self.assertEqual(len(self.store.state["meal_plan"]), 2)

    def test_same_meal_upserts_and_id_patch_keeps_other_fields(self):
        self.success(tools.save_meal_plan(self.store, [self.meal(dish="Methi paneer with dosa")]))
        saved = self.success(tools.save_meal_plan(self.store, [{"id": "ml_1", "notes": "Mild food"}]))["meals"][0]
        self.assertEqual(len(self.store.state["meal_plan"]), 1)
        self.assertEqual(saved["dish"], "Methi paneer with dosa")
        self.assertEqual(saved["date"], "2026-10-24")
        self.assertEqual(saved["notes"], "Mild food")

    def test_same_date_slot_upsert_preserves_existing_id(self):
        saved = self.success(tools.save_meal_plan(self.store, [self.meal(id="new_incoming_id", dish="Updated dinner")]))["meals"][0]
        self.assertEqual(saved["id"], "ml_1")
        self.assertEqual(len(self.store.state["meal_plan"]), 1)

    def test_duplicate_slot_in_one_batch_does_not_append(self):
        data = self.success(tools.save_meal_plan(self.store, [
            self.meal(id="ml_2", date="2026-10-25"),
            self.meal(id="ml_3", date="2026-10-25", dish="Last dinner revision"),
        ]))
        self.assertEqual(len(data["meals"]), 1)
        self.assertEqual(data["meals"][0]["id"], "ml_2")
        self.assertEqual(data["meals"][0]["dish"], "Last dinner revision")
        self.assertEqual(len(self.store.state["meal_plan"]), 2)

    def test_status_argument_applies_to_saved_meals(self):
        saved = self.success(tools.save_meal_plan(self.store, [self.meal()], status="confirmed"))["meals"][0]
        self.assertEqual(saved["status"], "confirmed")

    def test_batch_failure_is_atomic(self):
        before = self.store.state
        unsafe = self.meal(id="ml_3", date="2026-10-26", ingredients=[{"item": "chicken", "qty": 300, "unit": "g"}])
        self.failure(tools.save_meal_plan(self.store, [self.meal(id="ml_2", date="2026-10-25"), unsafe]), "RESTRICTION_CONFLICT")
        self.assertEqual(self.store.state, before)
        self.assertFalse(self.path.exists())

    def test_existing_id_takes_priority_and_slot_collision_is_rejected(self):
        self.success(tools.save_meal_plan(self.store, [self.meal(id="ml_2", date="2026-10-25")]))
        before = self.store.state
        self.failure(tools.save_meal_plan(self.store, [self.meal(date="2026-10-25")]), "SLOT_CONFLICT")
        self.assertEqual(self.store.state, before)

    def test_invalid_meal_inputs_do_not_write(self):
        for update in ({"date": "20261025"}, {"attendees": ["stranger"]}, {"date": "2026-02-30"},
                       {"ingredients": [{"item": "duck", "qty": 1, "unit": "g"}]},
                       {"ingredients": [{"item": "paneer", "qty": -1, "unit": "g"}]},
                       {"slot": "midnight"}, {"internal_reasoning": "do not store"}):
            with self.subTest(update=update):
                self.failure(tools.save_meal_plan(self.store, [self.meal(**update)]), "INVALID_INPUT")
        self.assertFalse(self.path.exists())

    def test_shared_chicken_and_fish_conflict_for_meera(self):
        before = self.store.state
        for item in ("chicken", "ಮೀನು"):
            with self.subTest(item=item):
                error = self.failure(tools.save_meal_plan(self.store, [self.meal(ingredients=[{"item": item, "qty": 100, "unit": "g"}])]), "RESTRICTION_CONFLICT")
                self.assertIn("Meera", error["message"])
        self.assertEqual(self.store.state, before)

    def test_vegetarian_base_and_rohan_only_chicken_are_allowed(self):
        self.success(tools.save_meal_plan(self.store, [self.meal(variants={"m1": "add grilled chicken"})]))
        self.success(tools.save_meal_plan(self.store, [self.meal()]))
        self.assertEqual(len(self.store.state["meal_plan"]), 1)

    def test_meera_specific_meat_variant_is_rejected(self):
        self.failure(tools.save_meal_plan(self.store, [self.meal(variants={"m2": "add grilled chicken"})]), "RESTRICTION_CONFLICT")

    def test_move_keeps_id_and_removes_old_date(self):
        data = self.success(tools.move_meal(self.store, "ml_1", "2026-10-25"))
        self.assertEqual(data, {"meal_id": "ml_1", "old_date": "2026-10-24", "new_date": "2026-10-25"})
        self.assertEqual(self.success(tools.get_meal_plan(self.store, "2026-10-24"))["meals"], [])
        self.assertEqual(self.success(tools.get_meal_plan(self.store, "2026-10-25"))["meals"][0]["id"], "ml_1")
        self.assertEqual(len(self.success(tools.get_meal_plan(self.store))["meals"]), 1)

    def test_move_to_occupied_slot_and_unknown_id_fail(self):
        self.success(tools.save_meal_plan(self.store, [self.meal(id="ml_2", date="2026-10-25")]))
        before = self.store.state
        self.failure(tools.move_meal(self.store, "ml_1", "2026-10-25"), "SLOT_CONFLICT")
        self.failure(tools.move_meal(self.store, "unknown", "2026-10-25"), "MEAL_NOT_FOUND")
        self.assertEqual(self.store.state, before)

    def test_grocery_gap_uses_stock_includes_missing_and_saves(self):
        data = self.success(tools.build_grocery_list(self.store))
        items = {entry["item"]: entry for entry in data["items"]}
        self.assertEqual(set(items), {"spinach", "paneer", "chicken"})
        self.assertEqual(items["paneer"]["qty"], 200)
        self.assertEqual(items["spinach"]["qty"], 2)
        self.assertEqual(items["chicken"]["qty"], 150)
        self.assertEqual(self.store.state["grocery_list"], data["items"])
        self.assertEqual(StateStore(runtime_path=self.path).state["grocery_list"], data["items"])
        self.assertEqual(self.store.state["pantry"], json.loads(self.original_seed)["pantry"])

    def test_grocery_aggregates_meals_and_converts_kg_without_double_subtraction(self):
        self.success(tools.save_meal_plan(self.store, [
            self.meal(ingredients=[{"item": "paneer", "qty": 200, "unit": "g"}], variants={}),
            self.meal(id="ml_2", date="2026-10-25", ingredients=[{"item": "paneer", "qty": 0.2, "unit": "kg"}], variants={}),
        ]))
        items = self.success(tools.build_grocery_list(self.store))["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["qty"], items[0]["unit"]), (200, "g"))

    def test_grocery_handles_unknown_and_incompatible_stock_without_guessing(self):
        state = self.store.state
        paneer = next(entry for entry in state["pantry"] if entry["item"] == "paneer")
        paneer["qty"] = None
        self.store.save(state, event_type="pantry_update", summary="Stock quantity unknown.")
        items = {entry["item"]: entry for entry in self.success(tools.build_grocery_list(self.store))["items"]}
        self.assertEqual(items["paneer"]["qty"], 400)
        self.assertIn("unknown", items["paneer"]["reason"])
        state = self.store.state
        paneer = next(entry for entry in state["pantry"] if entry["item"] == "paneer")
        paneer.update(qty=2, unit="count")
        self.store.save(state, event_type="pantry_update", summary="Stock in pieces.")
        items = {entry["item"]: entry for entry in self.success(tools.build_grocery_list(self.store))["items"]}
        self.assertEqual(items["paneer"]["qty"], 400)
        self.assertIn("unit differs", items["paneer"]["reason"])

    def test_text_variant_has_unknown_quantity_in_groceries(self):
        self.success(tools.save_meal_plan(self.store, [self.meal(variants={"m1": "add grilled chicken"})]))
        items = {entry["item"]: entry for entry in self.success(tools.build_grocery_list(self.store))["items"]}
        self.assertIsNone(items["chicken"]["qty"])
        self.assertIn("confirm", items["chicken"]["reason"])

    def test_completed_meals_and_zero_attendees_need_no_groceries(self):
        self.success(tools.save_meal_plan(self.store, [self.meal()], status="completed"))
        self.assertEqual(self.success(tools.build_grocery_list(self.store))["items"], [])
        self.success(tools.save_meal_plan(self.store, [self.meal(attendees=[])]))
        self.assertEqual(self.success(tools.build_grocery_list(self.store))["items"], [])

    def test_fact_changes_invalidate_old_grocery_list(self):
        self.success(tools.build_grocery_list(self.store))
        self.assertTrue(self.store.state["grocery_list"])
        self.success(tools.update_pantry_item(self.store, "paneer", qty=500))
        self.assertEqual(self.store.state["grocery_list"], [])

    def test_missing_spinach_records_canonical_fact_and_cook_history(self):
        self.success(tools.update_pantry_item(self.store, "spinach", qty=2))
        self.success(tools.report_ingredient_issue(self.store, "ml_1", "ಪಾಲಕ್", "missing"))
        item = self.success(tools.get_pantry(self.store, ["spinach"]))["items"][0]
        self.assertEqual((item["qty"], item["status"]), (0, "out"))
        event = self.store.state["history"][-1]
        self.assertEqual((event["actor"], event["type"], event["entity_id"]), ("cook", "ingredient_issue", "ml_1"))

    def test_safe_substitution_replaces_meal_ingredient(self):
        meal = self.success(tools.report_ingredient_issue(self.store, "ml_1", "palak", "missing", "ಮೆಂತ್ಯ"))["meal"]
        self.assertEqual(meal["ingredients"][0], {"item": "methi", "qty": 2, "unit": "bunch"})
        self.assertEqual(self.store.state["history"][-1]["type"], "substitution")

    def test_unsafe_substitution_leaves_meal_and_pantry_unchanged(self):
        self.success(tools.update_pantry_item(self.store, "spinach", qty=2))
        before, disk = self.store.state, self.path.read_bytes()
        self.failure(tools.report_ingredient_issue(self.store, "ml_1", "palak", "missing", "chicken"), "RESTRICTION_CONFLICT")
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.path.read_bytes(), disk)

    def test_substitution_does_not_turn_bunches_into_grams(self):
        meal = self.success(tools.report_ingredient_issue(self.store, "ml_1", "spinach", "missing", "tofu"))["meal"]
        self.assertEqual(meal["ingredients"][0], {"item": "tofu", "qty": None, "unit": "g"})
        tofu = next(entry for entry in self.success(tools.build_grocery_list(self.store))["items"] if entry["item"] == "tofu")
        self.assertIsNone(tofu["qty"])

    def test_explicit_substitute_quantity_is_preserved(self):
        meal = self.success(tools.report_ingredient_issue(self.store, "ml_1", "spinach", "missing", {"item": "tofu", "qty": 200, "unit": "g"}))["meal"]
        self.assertEqual(meal["ingredients"][0]["qty"], 200)

    def test_issue_for_unrelated_ingredient_or_missing_meal_fails(self):
        before = self.store.state
        self.failure(tools.report_ingredient_issue(self.store, "ml_1", "eggs", "missing"), "INGREDIENT_NOT_IN_MEAL")
        self.failure(tools.report_ingredient_issue(self.store, "unknown", "palak", "missing"), "MEAL_NOT_FOUND")
        self.assertEqual(self.store.state, before)

    def test_attendance_removes_appa_updates_count_without_guessing_portions(self):
        ingredients = self.store.state["meal_plan"][0]["ingredients"]
        data = self.success(tools.set_attendance(self.store, "ml_1", "m3", False))
        self.assertEqual(data["count"], 3)
        self.assertNotIn("m3", data["attendees"])
        self.assertFalse(data["quantities_changed"])
        self.assertEqual(self.store.state["meal_plan"][0]["ingredients"], ingredients)

    def test_attendance_is_deduplicated_and_validates_new_attendee_restrictions(self):
        self.success(tools.set_attendance(self.store, "ml_1", "m1", True))
        self.assertEqual(self.store.state["meal_plan"][0]["attendees"].count("m1"), 1)
        self.success(tools.save_meal_plan(self.store, [self.meal(attendees=["m1"], ingredients=[{"item": "chicken", "qty": 150, "unit": "g"}], variants={})]))
        before = self.store.state
        self.failure(tools.set_attendance(self.store, "ml_1", "m2", True), "RESTRICTION_CONFLICT")
        self.assertEqual(self.store.state, before)

    def test_bad_attendance_inputs_are_safe_failures(self):
        self.failure(tools.set_attendance(self.store, "ml_1", "m3", "false"), "INVALID_INPUT")
        self.failure(tools.set_attendance(self.store, "ml_1", "stranger", True), "MEMBER_NOT_FOUND")
        self.failure(tools.set_attendance(self.store, "unknown", "m1", True), "MEAL_NOT_FOUND")

    def test_history_has_useful_fields_and_caps_at_fifty(self):
        self.success(tools.move_meal(self.store, "ml_1", "2026-10-25"))
        event = self.store.state["history"][-1]
        self.assertEqual(set(event), {"timestamp", "actor", "type", "summary", "entity_id"})
        self.assertIn("2026-10-24", event["summary"])
        self.assertEqual(datetime.fromisoformat(event["timestamp"]).utcoffset().total_seconds(), 0)
        for index in range(55):
            self.store.save(self.store.state, event_type="test_update", summary=str(index))
        events = self.store.state["history"]
        self.assertEqual(len(events), 50)
        self.assertEqual((events[0]["summary"], events[-1]["summary"]), ("5", "54"))
        self.assertEqual(len(StateStore(runtime_path=self.path).state["history"]), 50)

    def test_handoff_file_contains_relevant_context_and_no_internals(self):
        state = self.store.state
        state["internal_reasoning"] = "PRIVATE_SENTINEL"
        state["meal_plan"][0]["system_prompt"] = "PRIVATE_SENTINEL"
        self.store.save(state, event_type="pantry_update", summary="Spinach unavailable.", actor="cook")
        state = self.store.state
        state["history"][-1]["hidden_reasoning"] = "PRIVATE_SENTINEL"
        self.store.save(state, event_type="meal_update", summary="Waiting for a substitution.")
        pending = {"type": "substitution", "meal_id": "ml_1", "item": "spinach", "substitute": "methi"}
        handoff = self.success(tools.create_handoff_summary(self.store, "Cook needs help with substitution", "high", pending))["handoff"]
        path = self.path.parent / "handoff.json"
        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), handoff)
        self.assertEqual(handoff["household_id"], "hh_001")
        self.assertEqual(handoff["current_meal"]["id"], "ml_1")
        self.assertEqual(handoff["pending_action"], pending)
        self.assertEqual(len(handoff["recent_history"]), 2)
        self.assertNotIn("PRIVATE_SENTINEL", path.read_text(encoding="utf-8"))
        self.assertNotIn("transcript", handoff)
        self.assertNotIn("system_prompt", handoff)

    def test_handoff_without_active_meal_has_null_context(self):
        self.success(tools.save_meal_plan(self.store, [self.meal()], status="completed"))
        handoff = self.success(tools.create_handoff_summary(self.store, "General household question", "low"))["handoff"]
        self.assertIsNone(handoff["current_meal"])

    def test_handoff_rejects_private_pending_fields_and_unknown_meals(self):
        self.failure(tools.create_handoff_summary(self.store, "Help", "high", {"internal_reasoning": "private"}), "INVALID_INPUT")
        self.failure(tools.create_handoff_summary(self.store, "Help", "high", {"meal_id": "missing"}), "MEAL_NOT_FOUND")
        self.assertFalse((self.path.parent / "handoff.json").exists())

    def preview(self):
        self.success(tools.build_grocery_list(self.store))
        return self.success(tools.preview_instamart_order(self.store))

    def simulated_failure(self, result, code):
        self.assertIs(result["error"]["simulated"], True)
        return self.failure({"ok": result["ok"], "error": {
            key: value for key, value in result["error"].items() if key != "simulated"
        }}, code)

    def test_simulated_catalog_has_one_deterministic_unavailable_item(self):
        catalog = instamart.load_catalog()
        self.assertEqual([product["canonical_item"] for product in catalog if not product["in_stock"]], ["spinach"])
        self.assertEqual(len({product["id"] for product in catalog}), len(catalog))
        self.assertEqual(len({product["canonical_item"] for product in catalog}), len(catalog))
        for product in catalog:
            self.assertTrue({"id", "canonical_item", "display_name", "pack_size", "price_inr",
                             "in_stock", "substitute_item"} <= product.keys())
        self.assertEqual(next(product for product in catalog if product["canonical_item"] == "spinach")["substitute_item"], "methi")

    def test_preview_builds_simulated_cart_and_persists_confirmation(self):
        preview = self.preview()
        self.assertIs(preview["simulated"], True)
        self.assertIs(preview["prices_illustrative"], True)
        self.assertTrue(preview["confirmation_id"].startswith("confirm_"))
        self.assertCountEqual([line["canonical_item"] for line in preview["available_items"]], ["paneer", "chicken"])
        self.assertEqual([line["item"] for line in preview["unavailable_items"]], ["spinach"])
        self.assertEqual(preview["suggested_substitutions"][0]["substitute_item"], "methi")
        self.assertNotIn("methi", [line["canonical_item"] for line in preview["available_items"]])
        self.assertEqual((preview["subtotal_inr"], preview["delivery_fee_inr"], preview["total_inr"]), (280, 29, 309))
        self.assertEqual(preview["eta"], "18-25 min")
        pending = self.store.state["pending_confirmation"]
        self.assertEqual((pending["id"], pending["type"], pending["consumed"]),
                         (preview["confirmation_id"], "instamart_order", False))
        self.assertEqual(len(pending["cart_hash"]), 64)
        self.assertIsNone(pending["created_turn"])
        self.assertIsNone(pending["last_user_turn"])
        self.assertEqual(datetime.fromisoformat(pending["created_at"]).utcoffset().total_seconds(), 0)
        self.assertEqual(StateStore(runtime_path=self.path).state, self.store.state)

    def test_repeated_previews_have_stable_totals_hash_and_unique_ids(self):
        first = self.preview()
        digest = self.store.state["pending_confirmation"]["cart_hash"]
        second = self.success(tools.preview_instamart_order(self.store))
        self.assertNotEqual(first["confirmation_id"], second["confirmation_id"])
        self.assertEqual(digest, self.store.state["pending_confirmation"]["cart_hash"])
        self.assertEqual({key: value for key, value in first.items() if key != "confirmation_id"},
                         {key: value for key, value in second.items() if key != "confirmation_id"})

    def test_pack_rounding_and_simple_unit_conversion(self):
        groceries = [{"item": "paneer", "qty": 0.45, "unit": "kg"},
                     {"item": "eggs", "qty": 7, "unit": "pieces"}]
        cart = instamart.build_cart(groceries)
        self.assertEqual([line["packs"] for line in cart["available_items"]], [3, 2])
        self.assertEqual((cart["subtotal_inr"], cart["total_inr"]), (420, 449))

    def test_unknown_quantity_unit_and_missing_product_are_not_guessed(self):
        groceries = [{"item": "paneer", "qty": None, "unit": "g"},
                     {"item": "tofu", "qty": 2, "unit": "bunch"},
                     {"item": "dragon fruit", "qty": 1, "unit": "count"},
                     {"item": "eggs", "qty": 0, "unit": "count"}]
        cart = instamart.build_cart(groceries)
        self.assertEqual(len(cart["unavailable_items"]), 3)
        self.assertEqual(cart["available_items"], [])
        self.assertEqual((cart["subtotal_inr"], cart["delivery_fee_inr"], cart["total_inr"]), (0, 0, 0))
        self.assertIsNone(cart["eta"])

    def test_cart_hash_includes_source_quantities_even_with_same_pack_count(self):
        first = [{"item": "paneer", "qty": 199, "unit": "g"}]
        second = [{"item": "paneer", "qty": 200, "unit": "g"}]
        first_cart, second_cart = instamart.build_cart(first), instamart.build_cart(second)
        self.assertEqual(first_cart, second_cart)
        self.assertNotEqual(instamart.cart_hash(first, first_cart), instamart.cart_hash(second, second_cart))
        self.assertEqual(instamart.cart_hash(first, first_cart),
                         instamart.cart_hash(deepcopy(first), deepcopy(first_cart)))

    def test_order_without_preview_is_rejected(self):
        self.simulated_failure(tools.place_instamart_order(self.store, "confirm_missing", "yes"), "NO_PENDING_CONFIRMATION")
        self.assertFalse(self.path.exists())

    def test_wrong_confirmation_id_is_rejected_without_consuming(self):
        self.preview()
        before, disk = self.store.state, self.path.read_bytes()
        self.simulated_failure(tools.place_instamart_order(self.store, "confirm_wrong", "yes"), "CONFIRMATION_MISMATCH")
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.path.read_bytes(), disk)

    def test_empty_and_ambiguous_quotes_cannot_order(self):
        preview = self.preview()
        before, disk = self.store.state, self.path.read_bytes()
        for quote in ("", "  ", None, 123, "maybe", "maybe add chicken", "okay", "hmm",
                      "let's see", "should we", "can you add chicken", "yes but add chicken", "not yes"):
            with self.subTest(quote=quote):
                self.simulated_failure(tools.place_instamart_order(self.store, preview["confirmation_id"], quote), "CONFIRMATION_REQUIRED")
                self.assertEqual(self.store.state, before)
                self.assertEqual(self.path.read_bytes(), disk)

    def test_explicit_quotes_place_only_simulated_orders(self):
        for quote in ("yes", "yes place it", "go ahead", "place it", "order it", "haan", "haan kar do", "yes, order it", " YES  "):
            with self.subTest(quote=quote):
                preview = self.preview()
                result = tools.place_instamart_order(self.store, preview["confirmation_id"], quote)
                order = self.success(result)
                self.assertEqual(result["summary"], "Simulated Instamart order placed.")
                self.assertTrue(order["order_id"].startswith("SIM-"))
                self.assertIs(order["simulated"], True)
                self.assertEqual((order["total_inr"], order["eta"]), (309, "18-25 min"))
                self.assertTrue(self.store.state["pending_confirmation"]["consumed"])
                event = self.store.state["history"][-1]
                self.assertEqual((event["type"], event["entity_id"]), ("simulated_order_placed", order["order_id"]))
                self.assertIn("Simulated Instamart order", event["summary"])
                self.assertIn("309", event["summary"])
                self.assertIn("Unavailable items excluded", event["summary"])

    def test_same_confirmation_cannot_order_twice_even_after_reload(self):
        preview = self.preview()
        self.success(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"))
        before, disk = self.store.state, self.path.read_bytes()
        for store in (self.store, StateStore(runtime_path=self.path)):
            self.simulated_failure(tools.place_instamart_order(store, preview["confirmation_id"], "yes"), "CONFIRMATION_CONSUMED")
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(sum(event["type"] == "simulated_order_placed" for event in before["history"]), 1)

    def test_grocery_add_remove_quantity_changes_and_return_to_old_cart_invalidate(self):
        for change in ("add", "remove", "quantity"):
            with self.subTest(change=change):
                preview = self.preview()
                original = self.store.state["grocery_list"]
                state = self.store.state
                if change == "add":
                    state["grocery_list"].append({"item": "eggs", "qty": 6, "unit": "count"})
                elif change == "remove":
                    state["grocery_list"].pop()
                else:
                    state["grocery_list"][1]["qty"] = 199
                self.store.save(state, event_type="grocery_update", summary="Changed groceries.")
                self.assertIsNone(self.store.state["pending_confirmation"])
                state = self.store.state
                state["grocery_list"] = original
                self.store.save(state, event_type="grocery_update", summary="Restored old groceries.")
                self.simulated_failure(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"), "NO_PENDING_CONFIRMATION")

    def test_pantry_meal_attendance_and_substitution_changes_invalidate(self):
        changes = (
            lambda: tools.update_pantry_item(self.store, "paneer", qty=201),
            lambda: tools.move_meal(self.store, "ml_1", "2026-10-25"),
            lambda: tools.set_attendance(self.store, "ml_1", "m3", False),
            lambda: tools.report_ingredient_issue(self.store, "ml_1", "spinach", "missing", "methi"),
        )
        for change in changes:
            preview = self.preview()
            self.success(change())
            self.assertIsNone(self.store.state["pending_confirmation"])
            self.simulated_failure(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"), "NO_PENDING_CONFIRMATION")

    def test_new_preview_replaces_old_confirmation(self):
        first = self.preview()
        second = self.success(tools.preview_instamart_order(self.store))
        self.simulated_failure(tools.place_instamart_order(self.store, first["confirmation_id"], "yes"), "CONFIRMATION_MISMATCH")
        self.success(tools.place_instamart_order(self.store, second["confirmation_id"], "yes"))

    def test_catalog_price_stock_and_substitute_changes_fail_hash_gate(self):
        for field, value in (("price_inr", 999), ("in_stock", False), ("substitute_item", "methi")):
            with self.subTest(field=field):
                preview = self.preview()
                catalog = instamart.load_catalog()
                paneer = next(product for product in catalog if product["canonical_item"] == "paneer")
                paneer[field] = value
                with patch("sim.instamart.load_catalog", return_value=catalog):
                    self.simulated_failure(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"), "STALE_CONFIRMATION")
                self.assertIsNone(self.store.state["pending_confirmation"])
                # Restoring catalog contents must not resurrect the rejected token.
                self.simulated_failure(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"), "NO_PENDING_CONFIRMATION")

    def test_accepted_substitute_requires_new_preview_and_new_confirmation(self):
        old = self.preview()
        self.success(tools.report_ingredient_issue(self.store, "ml_1", "spinach", "missing", "methi"))
        new = self.preview()
        self.assertEqual(new["unavailable_items"], [])
        self.assertEqual(new["total_inr"], 379)
        self.assertIn("methi", [line["canonical_item"] for line in new["available_items"]])
        self.simulated_failure(tools.place_instamart_order(self.store, old["confirmation_id"], "yes"), "CONFIRMATION_MISMATCH")
        self.assertEqual(self.success(tools.place_instamart_order(self.store, new["confirmation_id"], "yes"))["total_inr"], 379)

    def test_empty_cart_cannot_create_order(self):
        preview = self.success(tools.preview_instamart_order(self.store))
        self.assertEqual(preview["available_items"], [])
        self.simulated_failure(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"), "EMPTY_CART")
        self.assertFalse(self.store.state["pending_confirmation"]["consumed"])

    def test_failed_preview_or_placement_persistence_keeps_confirmation_usable(self):
        preview = self.preview()
        before, disk = self.store.state, self.path.read_bytes()
        with patch("shared.state.os.replace", side_effect=PermissionError("private disk detail")):
            self.simulated_failure(tools.preview_instamart_order(self.store), "PERSISTENCE_ERROR")
            self.simulated_failure(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"), "PERSISTENCE_ERROR")
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.path.read_bytes(), disk)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.success(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"))

    def test_failed_grocery_mutation_preserves_previous_confirmation(self):
        preview = self.preview()
        before = self.store.state
        with patch("shared.state.os.replace", side_effect=PermissionError("write failed")):
            self.failure(tools.update_pantry_item(self.store, "paneer", qty=500), "PERSISTENCE_ERROR")
        self.assertEqual(self.store.state, before)
        self.success(tools.place_instamart_order(self.store, preview["confirmation_id"], "yes"))


if __name__ == "__main__":
    unittest.main()
