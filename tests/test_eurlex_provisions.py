from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import Mock

from eurlex_provision.evidence import branch_batches
from eurlex_provision.screening import ModelError, combine, score_batch, score_verified_batch, validate_answer
from eurlex_provision.thresholds import numeric_checks
from lr_ai.qwen_client import ChatResult
from screen_eurlex_provisions import load_exports, write_relevance_export


def payload():
    return {"document": {"celex": "32024R3110", "version_id": "pinned"},
            "target": "Article 2", "candidate_anchors": {"precast": {}},
            "branches": [{"locator": "Article 2(1)"}],
            "context": {"passages": []}, "footnotes": [], "retrieved_context": [],
            "retrieval_log": [], "can_request_context": True}


def decision():
    return {
        "celex": "32024R3110", "source_version_id": "pinned", "target": "Article 2",
        "provision_function": "SCOPE", "level": "DIRECT",
        "reason": "The supplied activity is within the target scope.", "area": ["precast"],
        "evidence_refs": ["Article 2(1)"], "review_status": "COMPLETE", "missing_evidence": [],
        "verification_notes": [],
        "branch_results": [{"locator": "Article 2(1)", "status": "ASSESSED",
                            "reason": "Applies to the supplied scenario.",
                            "evidence_refs": ["Article 2(1)"]}],
    }


class ProvisionTests(unittest.TestCase):
    def test_truncation_splits_and_scores_each_branch_without_repeating(self):
        inputs = payload()
        other = "Article 2(2)"
        inputs["branches"].append({"locator": other})
        final = decision()
        second = {**final, "evidence_refs": [other], "branch_results": [
            {**final["branch_results"][0], "locator": other, "evidence_refs": [other]}
        ]}
        client = Mock()
        accepted = ChatResult(ok=True, finish_reason="stop", content='{"issues":[]}')
        client.chat.side_effect = [
            ChatResult(ok=True, finish_reason="length", content='{"result":', usage={"completion_tokens": 1400}),
            ChatResult(ok=True, finish_reason="stop", content=json.dumps({"result": final, "context_requests": []})),
            accepted,
            ChatResult(ok=True, finish_reason="stop", content=json.dumps({"result": second, "context_requests": []})),
            accepted,
        ]
        result, _, review = score_verified_batch(client, "score", "review", Mock(), inputs)
        self.assertEqual(result["level"], "DIRECT")
        self.assertEqual(len(review["split_after_truncation"]), 2)
        self.assertEqual([len(a["branches"]) for a in inputs["_attempts"] if a["phase"] == "scoring"], [2, 1, 1])
        self.assertEqual(inputs["_attempts"][0]["finish_reason"], "length")
        self.assertEqual(inputs["_attempts"][0]["raw_reply"], '{"result":')

    def test_threshold_conflict_rejects_assessed_branch(self):
        inputs = payload()
        inputs["branches"][0]["text"] = "Rated thermal input of less than or equal to 5 MW."
        inputs["numeric_checks"] = numeric_checks(inputs["branches"], ["A boiler with 8 MW rated thermal input."])
        self.assertEqual(inputs["numeric_checks"][0]["status"], "NOT_MET")
        with self.assertRaisesRegex(ValueError, "does not meet the threshold"):
            validate_answer({"result": decision(), "context_requests": []}, inputs)

    def test_verification_note_allows_complete_indirect_and_review_can_be_held(self):
        corrected = {**decision(), "level": "INDIRECT", "verification_notes": ["Check supplier permit."]}
        value = {"result": corrected, "context_requests": []}
        validate_answer(value, payload())
        client = Mock()
        client.chat.side_effect = [
            ChatResult(ok=True, finish_reason="stop", content=json.dumps(value)),
            ChatResult(ok=True, finish_reason="length", content='{"issues":'),
        ]
        result, _, review = score_verified_batch(client, "score", "review", Mock(), payload())
        self.assertIsNone(result["level"])
        self.assertIn("Invalid Qwen scenario check", review["validation_error"])

    def test_invalid_branch_reply_retries_and_is_held(self):
        inputs = payload()
        inputs["branches"].append({"locator": "Annex table, row 2"})
        reply = Mock(ok=True, reasoning="")
        reply.parse_json.return_value = {"result": decision(), "context_requests": []}, None
        client = Mock()
        client.chat.return_value = reply
        result, _, review = score_verified_batch(client, "score", "review", Mock(), inputs)
        self.assertEqual(client.chat.call_count, 2)
        self.assertIsNone(result["level"])
        self.assertEqual(result["review_status"], "NEEDS_REVIEW")
        self.assertIn("Missing branch results for Annex table, row 2", review["validation_error"])
        feedback = json.loads(client.chat.call_args.args[0][1]["content"])
        self.assertIn("Annex table, row 2", feedback["response_validation_feedback"])
        self.assertEqual(len(result["branch_results"]), 2)
        branch_schema = client.chat.call_args.kwargs["schema"]["properties"]["result"]["anyOf"][0]
        branches = branch_schema["properties"]["branch_results"]
        self.assertEqual((branches["minItems"], branches["maxItems"]), (2, 2))
        self.assertEqual(branches["items"]["properties"]["locator"]["enum"],
                         ["Article 2(1)", "Annex table, row 2"])

    def test_branch_and_overall_review_status_mismatch_is_identified(self):
        result = decision()
        result["branch_results"][0]["status"] = "NEEDS_REVIEW"
        with self.assertRaisesRegex(ValueError, "Set overall review_status to NEEDS_REVIEW"):
            validate_answer({"result": result, "context_requests": []}, payload())

    def test_api_failure_still_stops_for_resume(self):
        client = Mock()
        reply = Mock(ok=False, reasoning="", error_kind="timeout", error="Timed out")
        reply.parse_json.return_value = None, "Timed out"
        client.chat.return_value = reply
        with self.assertRaisesRegex(ModelError, "Qwen request failed"):
            score_verified_batch(client, "score", "review", Mock(), payload())

    def test_exports_have_only_three_fields_and_exclude_review(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            direct = decision()
            indirect = {**direct, "target": "Article 3", "level": "INDIRECT"}
            ignored = {**direct, "target": "Article 4", "level": "IGNORE", "area": []}
            unverified = {**direct, "target": "Article 5", "review_status": "NEEDS_REVIEW"}
            pending = {**unverified, "target": "Article 6", "level": None}
            write_relevance_export(path, [direct, indirect, ignored, unverified, pending])
            for item in (direct, indirect, ignored):
                data = json.loads((path / f"{item['level'].lower()}.json").read_text(encoding="utf-8"))
                self.assertEqual(data, {item["celex"]: {item["target"]: {
                    key: item[key] for key in ("level", "reason", "area")
                }}})

    def test_scenario_reviewer_retries_and_withholds_unresolved_draft(self):
        draft = {"result": decision(), "context_requests": []}
        corrected = {"result": {**decision(), "level": "INDIRECT",
                                "reason": "The obligation affects the stated supplier."},
                     "context_requests": []}
        issue = {"issues": ["The scenario says an independent supplier operates the kiln."]}
        for final, expected in (({"issues": []}, "INDIRECT"), (issue, None)):
            with self.subTest(expected=expected):
                client = Mock()
                replies = [Mock(ok=True, reasoning="") for _ in range(4)]
                for reply, data in zip(replies, (draft, issue, corrected, final)):
                    reply.parse_json.return_value = data, None
                client.chat.side_effect = replies
                result, _, review = score_verified_batch(client, "score", "review", Mock(), payload())
                self.assertEqual(result["level"], expected)
                self.assertEqual(result["review_status"], "COMPLETE" if expected else "NEEDS_REVIEW")
                self.assertEqual(review["remaining_issues"], final["issues"])
                correction = json.loads(client.chat.call_args_list[2].args[0][1]["content"])
                self.assertEqual(correction["scenario_review_feedback"], issue["issues"])

    def test_document_results_join_by_celex_and_allow_missing(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            direct = {"32024R3110": {"level": "DIRECT", "reason": "fixture", "area": ["precast"]}}
            (path / "direct.json").write_text(json.dumps(direct), encoding="utf-8")
            (path / "indirect.json").write_text("{}", encoding="utf-8")
            self.assertEqual(load_exports(path, {"32024R3110": "", "32015L2193": ""}), direct)

    def test_invalid_anchor_citation_and_missing_branch_rejected(self):
        for key, value in (
            ("area", ["invented_anchor"]),
            ("evidence_refs", ["Article 99"]),
            ("branch_results", []),
            ("source_version_id", "some-other-version"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_answer({"result": {**decision(), key: value}, "context_requests": []}, payload())

    def test_model_requests_passage_and_reassesses_original_target(self):
        client, evidence = Mock(), Mock()
        passage = {"locator": "Article 17", "text": "Test fixture.", "note_ids": []}
        evidence.retrieve.return_value = passage, None
        evidence.note_evidence.return_value = []
        first, second = Mock(ok=True, reasoning=""), Mock(ok=True, reasoning="")
        first.parse_json.return_value = {
            "result": None, "context_requests": [
                {"locator": "Article 17", "instrument": "", "why": "Need marking context."}
            ]
        }, None
        final = decision()
        final["evidence_refs"].append("Article 17")
        second.parse_json.return_value = {"result": final, "context_requests": []}, None
        client.chat.side_effect = [first, second]
        value, trace = score_batch(client, "prompt", evidence, payload())
        self.assertEqual(value["target"], "Article 2")
        self.assertEqual(trace[0]["status"], "RETRIEVED")
        second_payload = json.loads(client.chat.call_args.args[0][1]["content"])
        self.assertEqual(second_payload["retrieved_context"], [passage])
        self.assertEqual(second_payload["target"], "Article 2")

    def test_unavailable_material_context_stays_pending(self):
        client, evidence = Mock(), Mock()
        evidence.retrieve.return_value = None, "External excerpt was not supplied."
        evidence.note_evidence.return_value = []
        response = Mock(ok=True, reasoning="")
        response.parse_json.return_value = {
            "result": None, "context_requests": [
                {"locator": "ref:26", "instrument": "32020L2184", "why": "Need exemption scope."}
            ],
        }, None
        client.chat.return_value = response
        value, trace = score_batch(client, "prompt", evidence, payload())
        self.assertIsNone(value["level"])
        self.assertEqual(value["review_status"], "NEEDS_REVIEW")
        self.assertEqual([r["status"] for r in trace], ["UNAVAILABLE", "BUDGET_REACHED"])

    def test_rollup_preserves_review_and_validated_direct_result(self):
        direct = decision()
        pending = deepcopy(direct)
        pending.update(level=None, area=[], missing_evidence=["Role missing"], review_status="NEEDS_REVIEW")
        combined = combine([direct, pending], "Article 2 / Scope")
        self.assertEqual(combined["level"], "DIRECT")
        self.assertEqual(combined["review_status"], "NEEDS_REVIEW")
        self.assertEqual(combined["missing_evidence"], ["Role missing"])
        indirect = {**direct, "level": "INDIRECT"}
        self.assertIsNone(combine([indirect, pending], "Article 2 / Scope")["level"])

    def test_batching_never_drops_branches(self):
        branches = [{"locator": str(i), "text": "x" * 1000} for i in range(18)]
        batches = list(branch_batches(branches))
        self.assertGreater(len(batches), 1)
        self.assertEqual([branch for batch in batches for branch in batch], branches)
