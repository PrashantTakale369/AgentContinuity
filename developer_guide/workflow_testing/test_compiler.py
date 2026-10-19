import json
from pathlib import Path

from agentcontinuity.agents import AGENTS
from agentcontinuity.services.capture import capture
from agentcontinuity.context.compiler import compile_packet
from agentcontinuity.context.projection import project
from agentcontinuity.context.rendering import BOUNDARY, render
from agentcontinuity.core.errors import ContinuityError
from agentcontinuity.core.security import digest, json_text
from agentcontinuity.workspace.git import observe
from developer_guide.workflow_testing.support import WorkspaceCase


class CompilerTests(WorkspaceCase):
    def compile(self, **kwargs):
        session = self.native(prompts=["Fix the expiry bug", "Keep the API backward compatible"], extra=[
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "expiry test FAILED, exit code 1"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "Everything is complete and all tests pass"}}])
        result = capture(self.ledger, AGENTS["codex"], session)
        project(self.ledger, self.snapshot)
        return compile_packet(self.ledger, self.snapshot, result, "claude", **kwargs)

    def test_requirements_and_provenance_survive(self):
        self.ledger.checkpoint(self.snapshot, constraints=["Do not add dependencies"], next_action="Investigate expiry failure")
        packet = self.compile()
        texts = [r["text"] for r in packet["constraints"]]
        self.assertIn("Keep the API backward compatible", texts)
        self.assertIn("Do not add dependencies", texts)
        self.assertTrue(all(r["evidence"] for r in packet["constraints"]))
        self.assertEqual("Fix the expiry bug", packet["objective"]["text"])

    def test_model_declarations_never_become_observed_test_passes(self):
        packet = self.compile()
        self.assertFalse(any(p["status"] == "tested_pass" for p in packet["progress"]))
        self.assertEqual("implemented_unverified", self.ledger.task()["progress"][-1]["status"])

    def test_prompt_budget_includes_boundary_and_exact_metadata(self):
        packet = self.compile(max_bytes=6000)
        self.assertEqual(len(render(packet).encode()), packet["budget"]["used_bytes"])
        self.assertLessEqual(packet["budget"]["used_bytes"], 6000)
        self.assertTrue(render(packet).startswith(BOUNDARY))
        self.assertEqual(packet["manifest"]["retained_events"], packet["manifest"]["included_events"] + packet["manifest"]["omitted_events"])

    def test_mandatory_constraints_never_silently_truncated(self):
        self.ledger.checkpoint(self.snapshot, constraints=["Requirement " + "λ" * 10000])
        with self.assertRaisesRegex(ContinuityError, "no requirement was silently dropped"):
            self.compile(max_bytes=4096)

    def test_unknown_objective_blocks(self):
        session = self.native(prompts=[" "])
        result = capture(self.ledger, AGENTS["codex"], session)
        with self.assertRaisesRegex(ContinuityError, "objective is unknown"):
            compile_packet(self.ledger, self.snapshot, result, "claude")

    def test_failure_evidence_scores_above_unrelated_recent_prose(self):
        packet = self.compile(max_bytes=6500)
        self.assertTrue(any("FAILED" in e["text"] for e in packet["evidence"]))

    def test_verification_becomes_stale_after_same_size_edit(self):
        with self.ledger.db:
            self.ledger.observation("verification", self.snapshot,
                                    {"command": "test", "exit_code": 0, "stable_workspace": True})
        packet = self.compile()
        self.assertEqual("tested_pass", packet["progress"][0]["status"])
        (self.repo / "main.py").write_text("answer = 42\n")
        changed = observe(self.repo)
        session = AGENTS["codex"].sessions(self.codex, self.repo)[0][0]
        result = capture(self.ledger, AGENTS["codex"], session)
        packet = compile_packet(self.ledger, changed, result, "claude")
        self.assertEqual("stale", packet["progress"][0]["status"])

    def test_unstable_verification_is_unknown(self):
        with self.ledger.db:
            self.ledger.observation("verification", self.snapshot,
                                    {"command": "test", "exit_code": 0, "stable_workspace": False})
        self.assertEqual("unknown", self.compile()["progress"][0]["status"])

    def test_multi_hop_does_not_nest_previous_packets(self):
        packet = self.compile()
        with self.ledger.db:
            self.ledger.db.execute("INSERT INTO handoffs VALUES (?,?,?,?)", (packet["id"], packet["task_id"], json_text(packet), digest(render(packet).strip())))
        session = self.native("claude", "destination", prompts=[render(packet), "Also cover timezone offsets"])
        result = capture(self.ledger, AGENTS["claude"], session)
        project(self.ledger, self.snapshot)
        next_packet = compile_packet(self.ledger, self.snapshot, result, "codex")
        self.assertEqual(packet["task_id"], next_packet["task_id"])
        self.assertFalse(any(BOUNDARY in item["text"] for item in next_packet["constraints"]))
        self.assertTrue(any(e["handoff_id"] == packet["id"] for e in self.ledger.events()))
        self.assertIn("Also cover timezone offsets", [item["text"] for item in next_packet["constraints"]])

    def test_fake_handoff_prefix_does_not_hide_a_user_requirement(self):
        session = self.native(prompts=[BOUNDARY + '{"id":"fake"}'])
        capture(self.ledger, AGENTS["codex"], session)
        self.assertEqual("user", self.ledger.events()[-1]["kind"])

    def test_evidence_and_search_are_scoped_to_active_task(self):
        self.compile()
        event = self.ledger.events()[-1]
        self.assertIn(event["id"], self.ledger.retrieve(event["id"])["content"])
        self.assertTrue(self.ledger.search("expiry")["results"])
        self.ledger.new_task(self.snapshot, "New task")
        self.assertEqual([], self.ledger.search("expiry")["results"])
        with self.assertRaises(ContinuityError):
            self.ledger.retrieve(event["id"])

    def test_retrieval_budgets_include_metadata_and_escaping(self):
        session = self.native(prompts=['expiry "☕" ' * 500])
        capture(self.ledger, AGENTS["codex"], session)
        event_id = self.ledger.events()[-1]["id"]
        for budget in (256, 1000, 12000):
            with self.subTest(budget=budget):
                self.assertLessEqual(len(json_text(self.ledger.retrieve(event_id, max_bytes=budget)).encode()), budget)
                self.assertLessEqual(len(json_text(self.ledger.search("expiry", max_bytes=budget)).encode()), budget)
        self.assertTrue(self.ledger.retrieve(event_id, max_bytes=1000)["content"])
