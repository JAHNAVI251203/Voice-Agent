"""Offline cook integration checks using real wrappers, shared state and SDK schemas."""

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from livekit.agents import llm
from livekit.agents.llm.utils import build_strict_openai_schema, validated_arguments

from agents.cook_assistant import CookAssistantAgent
from shared import tools
from shared.state import StateStore


class CookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = StateStore(runtime_path=Path(self.directory.name) / "state.json")
        self.today = date(2026, 10, 23)
        self.assertTrue(tools.save_meal_plan(self.store, [{"id": "ml_1", "date": self.today.isoformat()}], status="confirmed")["ok"])
        self.assertTrue(tools.update_pantry_item(self.store, "methi", qty=3)["ok"])
        self.agent = CookAssistantAgent(self.store, self.today)
        self.context = llm.ChatContext.empty()

    async def turn(self, text, confidence=None):
        message = self.context.add_message(role="user", content=text)
        message.transcript_confidence = confidence
        await self.agent.on_user_turn_completed(self.context, message)

    async def propose(self, substitute="ಮೆಂತ್ಯ"):
        await self.turn("ಪಾಲಕ್ ಇಲ್ಲ.")
        return await self.agent.report_ingredient_issue("ml_1", "ಪಾಲಕ್", "missing", substitute)

    def test_exact_tool_subset_and_valid_schemas(self):
        self.assertEqual({tool.info.name for tool in self.agent.tools}, {
            "get_pantry", "get_meal_plan", "update_pantry_item", "report_ingredient_issue",
            "set_attendance", "create_handoff_summary"})
        for tool in self.agent.tools:
            build_strict_openai_schema(tool)
        report = next(tool for tool in self.agent.tools if tool.info.name == "report_ingredient_issue")
        self.assertEqual(validated_arguments(report, {"meal_id": "ml_1", "item": "ಪಾಲಕ್", "issue": "missing"})["item"], "ಪಾಲಕ್")
        self.assertNotIn("HouseholdPlannerAgent", type(self.agent).__name__)

    async def test_current_menu_only_and_no_cooking_unconfirmed_or_other_day(self):
        self.assertTrue(tools.save_meal_plan(self.store, [{"date": "2026-10-24", "slot": "dinner", "dish": "Other dinner",
            "attendees": ["m1"], "ingredients": []}], status="confirmed")["ok"])
        result = await self.agent.get_meal_plan()
        self.assertEqual([meal["id"] for meal in result["data"]["meals"]], ["ml_1"])
        self.assertIn(self.today.isoformat(), self.agent.instructions)
        self.assertTrue(tools.save_meal_plan(self.store, [{"id": "ml_1"}], status="draft")["ok"])
        before = self.store.state
        self.assertFalse((await self.agent.report_ingredient_issue("ml_1", "spinach", "missing"))["ok"])
        self.assertFalse((await self.agent.set_attendance("ml_1", "m3", False))["ok"])
        self.assertEqual(self.store.state, before)
        missing = CookAssistantAgent(self.store, date(2026, 10, 25))
        self.assertEqual((await missing.get_meal_plan())["data"]["meals"], [])

    async def test_kannada_issue_then_confirmed_substitution_and_shared_persistence(self):
        original = self.store.state["meal_plan"][0]
        result = await self.propose()
        self.assertTrue(result["ok"])
        self.assertFalse(result["data"]["applied"])
        self.assertEqual(self.store.state["meal_plan"][0], original)
        self.assertEqual((await self.agent.get_pantry(["ಪಾಲಕ್"]))["data"]["items"][0]["status"], "out")
        await self.turn("ಹೌದು, ಮೆಂತ್ಯ ಹಾಕಿ.")
        result = await self.agent.report_ingredient_issue("ml_1", "spinach", "missing", "methi")
        self.assertTrue(result["data"]["applied"])
        self.assertEqual(result["data"]["meal"]["ingredients"][0], {"item": "methi", "qty": 2, "unit": "bunch"})
        self.assertEqual(self.store.state["history"][-1]["type"], "substitution")
        self.assertEqual(StateStore(runtime_path=self.store.runtime_path).state, self.store.state)

    async def test_substitution_needs_new_explicit_turn_and_unchanged_meal(self):
        await self.propose()
        await self.agent.report_ingredient_issue("ml_1", "spinach", "missing", "methi")
        self.assertEqual(self.store.state["meal_plan"][0]["ingredients"][0]["item"], "spinach")
        await self.turn("okay")
        self.assertFalse((await self.agent.report_ingredient_issue("ml_1", "spinach", "missing", "methi"))["data"]["applied"])
        await self.turn("yes")
        self.agent.user_started_speaking()
        self.assertFalse((await self.agent.report_ingredient_issue("ml_1", "spinach", "missing", "methi"))["data"]["applied"])
        await self.turn("yes")
        tools.set_attendance(self.store, "ml_1", "m3", False)
        self.assertFalse((await self.agent.report_ingredient_issue("ml_1", "spinach", "missing", "methi"))["data"]["applied"])

    async def test_unsafe_substitution_is_atomic_even_after_confirmation(self):
        await self.propose("chicken")
        await self.turn("yes")
        before = self.store.state
        disk = self.store.runtime_path.read_bytes()
        result = await self.agent.report_ingredient_issue("ml_1", "spinach", "missing", "chicken")
        self.assertEqual(result["error"]["code"], "RESTRICTION_CONFLICT")
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.store.runtime_path.read_bytes(), disk)

    async def test_hindi_attendance_updates_count_without_changing_portions(self):
        await self.turn("Appa aaj raat nahi khayenge.")
        ingredients = self.store.state["meal_plan"][0]["ingredients"]
        result = await self.agent.set_attendance("ml_1", "m3", False)
        self.assertEqual(result["data"]["count"], 3)
        self.assertNotIn("m3", result["data"]["attendees"])
        self.assertEqual(self.store.state["meal_plan"][0]["ingredients"], ingredients)
        self.assertEqual(self.store.state["history"][-1]["type"], "attendance_update")
        self.assertIn("mirror", self.agent.instructions)

    async def test_three_uncertain_transcripts_preserve_pending_issue_in_one_handoff(self):
        await self.propose()
        before = self.store.state
        with patch("shared.tools.create_handoff_summary", wraps=tools.create_handoff_summary) as handoff:
            for count in range(1, 5):
                await self.turn("ಹೌದು", confidence=0.2)
                self.assertEqual(self.agent.misunderstandings, min(count, 3))
                self.assertEqual(await self.agent.llm_node(None, [], None), self.agent.clarification_reply)
                self.assertFalse((await self.agent.update_pantry_item("paneer", qty=999))["ok"])
                if count < 3:
                    self.assertFalse((Path(self.directory.name) / "handoff.json").exists())
            self.assertEqual(handoff.call_count, 1)
        self.assertIsNone(self.agent.pending_substitution)
        payload = json.loads((Path(self.directory.name) / "handoff.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["pending_action"]["substitute"], "methi")
        self.assertEqual(payload["current_meal"]["id"], "ml_1")
        self.assertEqual(payload["severity"], "high")
        self.assertEqual(self.store.state, before)
        self.assertNotIn("system_prompt", payload)

    async def test_model_uncertainty_counter_resets_after_successful_understood_action(self):
        for phrase in ("???", "???"):
            await self.turn(phrase)
            result = await self.agent.create_handoff_summary("Unclear cook request", "high", misunderstood=True)
            self.assertFalse(result["data"]["handoff_saved"])
            count = self.agent.misunderstandings
            await self.agent.create_handoff_summary("Still unclear in the same turn", "high", misunderstood=True)
            self.assertEqual(self.agent.misunderstandings, count)
        self.assertEqual(self.agent.misunderstandings, 2)
        await self.turn("Show today's dinner")
        self.assertTrue((await self.agent.get_meal_plan())["ok"])
        self.assertEqual(self.agent.misunderstandings, 0)

    async def test_handoff_failure_does_not_claim_saved_or_change_meal(self):
        with patch("shared.tools.create_handoff_summary", return_value={"ok": False, "error": {
            "code": "PERSISTENCE_ERROR", "message": "Unavailable", "retryable": True}}):
            for _ in range(3):
                self.agent.record_misunderstanding()
        self.assertEqual(self.agent.clarification_reply, "ಇನ್ನೊಂದು ಸತಿ ನಿಧಾನವಾಗಿ ಹೇಳಿ.")
        self.assertFalse((Path(self.directory.name) / "handoff.json").exists())

    async def test_emergency_handoff_stops_cooking_actions(self):
        await self.turn("Severe allergic reaction; difficulty breathing")
        result = await self.agent.create_handoff_summary("Reported severe allergic reaction", "urgent")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["handoff"]["severity"], "urgent")
        self.assertIn("emergency medical help", self.agent.clarification_reply)
        before = self.store.state
        await self.turn("What should I cook now?")
        self.assertFalse((await self.agent.report_ingredient_issue("ml_1", "spinach", "missing", "methi"))["ok"])
        self.assertFalse((await self.agent.set_attendance("ml_1", "m3", False))["ok"])
        self.assertEqual(self.store.state, before)

    async def test_voice_modes_use_verified_locales_and_preserve_planner_without_provider_calls(self):
        import main
        from agents.household_planner import HouseholdPlannerAgent
        for mode, language in (("cook", "kn"), ("cook", "hi"), ("cook", "en"), ("planner", "en")):
            callbacks = {}
            session = Mock()
            session.start = AsyncMock()
            def register(event):
                def decorator(callback):
                    callbacks[event] = callback
                    return callback
                return decorator
            session.on.side_effect = register
            session.llm.on.side_effect = lambda event: register("llm_" + event)
            session.tts.on.side_effect = lambda event: register("tts_" + event)
            with patch.dict(os.environ, {"VOICE_AGENT_MODE": mode, "COOK_LANGUAGE": language,
                "DEEPGRAM_API_KEY": "test", "OPENROUTER_API_KEY": "test", "SARVAM_API_KEY": "test"}), \
                patch.object(main, "AgentSession", return_value=session) as session_type, \
                patch.object(main, "StateStore", return_value=self.store), \
                patch.object(main, "create_llm"), patch.object(main.deepgram, "STT") as stt, \
                patch.object(main.sarvam, "TTS") as tts, patch.object(main.silero.VAD, "load"), \
                patch.object(main.inference, "TurnDetector") as semantic:
                await main.entrypoint(SimpleNamespace(room="offline"))
                stt.assert_called_once_with(model="nova-3", language=language)
                tts.assert_called_once_with(model="bulbul:v3", target_language_code=f"{language}-IN", speaker="simran")
                handling = session_type.call_args.kwargs["turn_handling"]
                self.assertEqual(handling["endpointing"]["min_delay"], 0.5)
                self.assertFalse(handling["preemptive_generation"]["enabled"])
                agent = session.start.call_args.kwargs["agent"]
                before_timing = self.store.state
                agent.latest_user_text = "old confirmation"
                with self.assertLogs(main.logger, level="INFO"):
                    callbacks["user_state_changed"](SimpleNamespace(new_state="speaking"))
                    callbacks["user_state_changed"](SimpleNamespace(old_state="speaking", new_state="listening", created_at=100.0))
                    callbacks["agent_state_changed"](SimpleNamespace(new_state="speaking", created_at=103.0))
                    callbacks["llm_metrics_collected"](SimpleNamespace(request_id="offline", duration=0.8, ttft=0.2, cancelled=False))
                    callbacks["tts_metrics_collected"](SimpleNamespace(request_id="offline", timestamp=104.0, duration=1.0, ttfb=0.3, cancelled=False))
                    callbacks["conversation_item_added"](SimpleNamespace(item=llm.ChatMessage(
                        role="user", content=["test"], metrics={"transcription_delay": 0.4})))
                    callbacks["conversation_item_added"](SimpleNamespace(item=llm.ChatMessage(
                        role="assistant", content=["test"], metrics={"tts_node_ttfb": 0.3})))
                    callbacks["function_tools_executed"](main.agents.FunctionToolsExecutedEvent(
                        function_calls=[], function_call_outputs=[]))
                self.assertEqual(self.store.state, before_timing)
                self.assertEqual(agent.latest_user_text, "")
                callbacks["user_transcription_timeout"](None)
                if mode == "cook":
                    self.assertEqual(session_type.call_args.kwargs["turn_handling"]["turn_detection"], "vad")
                    semantic.assert_not_called()
                    self.assertIsInstance(agent, CookAssistantAgent)
                    session.say.assert_called_once_with(agent.clarification_reply)
                else:
                    semantic.assert_called_once_with(version="v1-mini")
                    self.assertIsInstance(agent, HouseholdPlannerAgent)
                    session.say.assert_not_called()

    async def test_scripted_cook_harness_is_offline_and_passes(self):
        from harness import cook_main
        with patch("harness.create_llm", side_effect=AssertionError("No provider calls allowed")), patch("builtins.print"):
            await cook_main()
