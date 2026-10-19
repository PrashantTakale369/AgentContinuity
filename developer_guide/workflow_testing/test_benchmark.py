import unittest

from benchmarks.selection import SCENARIOS, run


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run(repeats=1, budget=6000)

    def test_all_scenarios_cover_both_directions(self):
        cases = self.report["cases"]
        self.assertEqual(2 * len(SCENARIOS), len(cases))
        self.assertEqual(set(SCENARIOS), {case["scenario"] for case in cases})
        for scenario in SCENARIOS:
            directions = {(case["source"], case["target"]) for case in cases if case["scenario"] == scenario}
            self.assertEqual({("codex", "claude"), ("claude", "codex")}, directions)

    def test_packets_use_exact_equal_byte_limits(self):
        for case in self.report["cases"]:
            with self.subTest(case=case["id"]):
                self.assertTrue(all(case["checks"].values()), case["checks"])
                if case["outcome"] == "packet_built":
                    self.assertLessEqual(case["prompt_bytes"], 6000)
                    self.assertLessEqual(case["recency_prompt_bytes"], 6000)
                    self.assertTrue(case["checks"]["exact_byte_count"])

    def test_long_output_exposes_retention_limit_and_remains_retrievable(self):
        for case in self.report["cases"]:
            if case["scenario"] == "long_tool_output":
                self.assertFalse(case["all_action_clues_retained"])
                self.assertFalse(case["all_recency_clues_retained"])
                self.assertTrue(case["checks"]["evidence_retrievable"])

    def test_recent_evidence_is_retained_by_both_selectors(self):
        for case in self.report["cases"]:
            if case["scenario"] == "recent_failure":
                self.assertTrue(case["all_action_clues_retained"])
                self.assertTrue(case["all_recency_clues_retained"])

    def test_oversized_requirements_are_refused_without_a_partial_packet(self):
        cases = [case for case in self.report["cases"] if case["scenario"] == "oversized_requirements"]
        self.assertEqual(2, len(cases))
        for case in cases:
            self.assertEqual("refused_oversized_requirements", case["outcome"])
            self.assertNotIn("prompt_bytes", case)
            self.assertTrue(case["checks"]["safe_refusal"])

    def test_verification_and_assistant_claims_are_distinguished(self):
        for case in self.report["cases"]:
            if case["scenario"] == "stale_verification":
                self.assertTrue(case["checks"]["stale_verification_identified"])
            elif case["scenario"] == "unverified_claim":
                self.assertTrue(case["checks"]["claim_remains_unverified"])
                self.assertTrue(case["checks"]["no_invented_verification"])
