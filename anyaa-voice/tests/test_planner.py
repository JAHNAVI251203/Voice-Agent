"""Offline checks of real LiveKit wrappers and confirmation transcript provenance."""

import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from livekit.agents import AgentSession, RunContext, llm
from livekit.agents.llm.utils import build_strict_openai_schema, validated_arguments
from livekit.agents.voice import SpeechHandle

from agents.household_planner import HouseholdPlannerAgent
from shared import tools
from shared.state import StateStore


class PlannerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = StateStore(runtime_path=Path(self.directory.name) / "state.json")
        self.agent = HouseholdPlannerAgent(self.store)
        self.session = AgentSession()
        self.addAsyncCleanup(self.session.aclose)
        tools.build_grocery_list(self.store)

    async def turn(self, text):
        message = self.session.history.add_message(role="user", content=text)
        await self.agent.on_user_turn_completed(self.session.history, message)
        return RunContext(session=self.session, speech_handle=SpeechHandle.create(),
                          function_call=llm.FunctionCall(name="place_instamart_order", call_id="test", arguments="{}"))

    async def preview(self):
        await self.turn("Preview the simulated cart")
        return (await self.agent.preview_instamart_order())["data"]["confirmation_id"]

    async def test_offline_planner_delivery_harness_never_constructs_provider(self):
        from harness import planner_main
        with patch("shared.providers.create_llm", side_effect=AssertionError("No provider calls allowed")), patch("builtins.print"):
            await planner_main(offline=True)

    def test_tool_subset_and_confirmation_schema(self):
        names = {tool.info.name for tool in self.agent.tools}
        self.assertEqual(names, {"get_pantry", "get_meal_plan", "save_meal_plan", "move_meal",
                                "update_pantry_item", "build_grocery_list", "preview_instamart_order",
                                "place_instamart_order", "set_attendance", "create_handoff_summary"})
        for tool in self.agent.tools:
            schema = build_strict_openai_schema(tool)
            if tool.info.name == "place_instamart_order":
                self.assertEqual(set(schema["function"]["parameters"]["properties"]), {"confirmation_id"})

    async def test_meal_json_is_validated_and_supports_member_variant_patch(self):
        tool = next(tool for tool in self.agent.tools if tool.info.name == "save_meal_plan")
        args = validated_arguments(tool, {"meal_json": json.dumps({"id": "ml_1", "notes": "Mild shared dinner",
                                    "variants": {"m1": [{"item": "chicken", "qty": 180, "unit": "g"}]}})})
        result = await tool(**args)
        self.assertTrue(result["ok"])
        self.assertEqual(self.store.state["meal_plan"][0]["variants"]["m1"][0]["qty"], 180)
        self.assertEqual(self.store.state["meal_plan"][0]["notes"], "Mild shared dinner")
        before = self.store.state
        self.assertFalse((await tool(meal_json="not JSON"))["ok"])
        self.assertFalse((await tool(meal_json=json.dumps({"id": "ml_1", "ingredients": ["chicken"]})))["ok"])
        self.assertEqual(self.store.state, before)

    async def test_weekday_correction_rejects_wrong_or_unchanged_date(self):
        await self.turn("Move it to Saturday.")
        for target in ("2026-10-28", "2026-10-24"):
            self.assertFalse((await self.agent.move_meal("ml_1", target))["ok"])
        self.assertTrue((await self.agent.move_meal("ml_1", "2026-10-31"))["ok"])
        self.assertEqual(len(self.store.state["meal_plan"]), 1)

    async def test_exact_latest_transcript_used_and_order_not_repeated(self):
        token = await self.preview()
        context = await self.turn("Yes, place it.")
        with patch("shared.tools.place_instamart_order", wraps=tools.place_instamart_order) as place:
            result = await self.agent.place_instamart_order(context, token)
            self.assertTrue(result["ok"])
            self.assertEqual(place.call_args.kwargs["user_quote"], "Yes, place it.")
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])
        context = await self.turn("yes")
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])

    async def test_clear_compound_voice_confirmation_preserves_exact_transcript(self):
        token = await self.preview()
        context = await self.turn("Okay. Go ahead.")
        with patch("shared.tools.place_instamart_order", wraps=tools.place_instamart_order) as place:
            self.assertTrue((await self.agent.place_instamart_order(context, token))["ok"])
            self.assertEqual(place.call_args.kwargs["user_quote"], "Okay. Go ahead.")

    async def test_exploration_clears_pending_and_older_yes_cannot_be_reused(self):
        token = await self.preview()
        await self.turn("yes")
        context = await self.turn("Maybe add chicken too.")
        self.assertIsNone(self.store.state["pending_confirmation"])
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])

    async def test_new_preview_cannot_use_yes_from_before_preview(self):
        await self.turn("yes")
        token = (await self.agent.preview_instamart_order())["data"]["confirmation_id"]
        context = RunContext(session=self.session, speech_handle=SpeechHandle.create(),
                             function_call=llm.FunctionCall(name="place_instamart_order", call_id="test", arguments="{}"))
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])

    async def test_okay_rejected_then_new_clear_yes_can_confirm(self):
        token = await self.preview()
        context = await self.turn("Okay.")
        self.assertEqual(await self.agent.llm_node(None, [], None), "Shall I place this simulated order? Please say yes.")
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])
        context = await self.turn("Haan kar do.")
        self.assertTrue((await self.agent.place_instamart_order(context, token))["ok"])

    async def test_interruption_and_unfinished_speech_fail_closed(self):
        token = await self.preview()
        context = await self.turn("yes")
        context.speech_handle.interrupt()
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])
        context = await self.turn("yes")
        self.agent.user_started_speaking()
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])

    async def test_other_latest_message_and_changed_cart_cannot_order(self):
        token = await self.preview()
        context = await self.turn("yes")
        self.session.history.add_message(role="user", content="cancel")
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])
        context = await self.turn("yes")
        tools.update_pantry_item(self.store, "paneer", qty=500)
        self.assertFalse((await self.agent.place_instamart_order(context, token))["ok"])

    async def test_preview_refreshes_cleared_grocery_cache_after_meal_edit(self):
        token = await self.preview()
        meal = self.store.state["meal_plan"][0]
        meal["variants"]["m1"][0]["qty"] = 600
        tools.save_meal_plan(self.store, [meal])
        self.assertEqual(self.store.state["grocery_list"], [])
        result = await self.agent.preview_instamart_order()
        self.assertTrue(result["ok"])
        self.assertNotEqual(token, result["data"]["confirmation_id"])
        chicken = next(item for item in result["data"]["available_items"] if item["canonical_item"] == "chicken")
        self.assertEqual(chicken["packs"], 3)

    def test_small_affirmative_allowlist_handles_transcript_punctuation_and_hindi(self):
        for quote in ("Yes, place it.", "Go ahead!", "हाँ कर दो।", "Okay. Go ahead.", "Okay, place the order."):
            self.assertTrue(tools._is_explicit_confirmation(quote))
        for quote in ("yes but add chicken", "maybe", "okay", "should we", "yes? no", "don't order it", "maybe okay go ahead", "okay add chicken"):
            self.assertFalse(tools._is_explicit_confirmation(quote))
