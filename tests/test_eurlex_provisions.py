from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import Mock

from eurlex_provision.evidence import branch_batches
from eurlex_provision.screening import combine, score_batch, validate_answer
from screen_eurlex_provisions import load_exports


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
        "branch_results": [{"locator": "Article 2(1)", "status": "ASSESSED",
                            "reason": "Applies to the supplied scenario.",
                            "evidence_refs": ["Article 2(1)"]}],
    }


class ProvisionTests(unittest.TestCase):
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
        first, second = Mock(ok=True), Mock(ok=True)
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
        response = Mock(ok=True)
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
