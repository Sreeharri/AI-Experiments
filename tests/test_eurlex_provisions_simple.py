import json
import unittest
from unittest.mock import Mock

from eurlex_provision.simple import ModelError, anchor_sources, score, target_passages, validate_decision
from lr_ai.qwen_client import ChatResult


class SimpleScoringTests(unittest.TestCase):
    def setUp(self):
        self.pack = {
            "business_anchors": {"cement": {"scope": "Max Bögl operates a cement kiln.", "roles": ["plant_operator"]}},
            "provision": {"locator": "BAT 17", "blocks": [{"text": "Kiln dust provision."}]},
            "direct_references": [{"text": "Limits from another provision."}],
        }
        self.result = {
            "level": "DIRECT", "reason": "Evidence for the decision.",
            "target_passage": "P1",
            "anchor_sources": ["cement:scope"],
        }

    def test_one_call_and_one_decision_per_provision(self):
        for label in ("DIRECT", "INDIRECT", "IGNORE"):
            with self.subTest(label=label):
                client = Mock()
                result = {**self.result, "level": label}
                if label == "IGNORE":
                    result.update(anchor_sources=[])
                client.chat.return_value = ChatResult(
                    ok=True, finish_reason="stop", content=json.dumps(result),
                )
                decision, audit = score(client, "System instruction.", self.pack)
                self.assertEqual(decision, {"level": label, "reason": result["reason"],
                                            "area": [] if label == "IGNORE" else ["cement"]})
                self.assertEqual(audit["evidence"]["target_quote"], "Kiln dust provision.")
                client.chat.assert_called_once()
                sent = json.loads(client.chat.call_args.args[0][1]["content"])
                self.assertEqual(sent["target_passages"], {"P1": "Kiln dust provision."})
                self.assertEqual(sent["anchor_source_options"], {"cement": ["cement:scope", "cement:roles.1"]})
                self.assertNotIn("area", client.chat.call_args.kwargs["schema"]["required"])

    def test_citations_from_wrong_target_or_anchor_are_rejected(self):
        cases = (
            ({"target_passage": "P2"}, "target_passage ID"),
            ({"anchor_sources": ["cement:company_facts.1"]}, "anchor_source_options"),
            ({"anchor_sources": []}, "Select at least one anchor source"),
            ({"reason": " "}, "non-empty reason"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ModelError, message):
                    validate_decision(
                        {**self.result, **changes}, target_passages(self.pack["provision"]),
                        anchor_sources(self.pack["business_anchors"]),
                    )

    def test_area_is_derived_once_from_the_selected_sources(self):
        candidate = {**self.result, "anchor_sources": ["cement:roles.1", "cement:scope", "cement:scope"]}
        decision, evidence = validate_decision(
            candidate, target_passages(self.pack["provision"]),
            anchor_sources(self.pack["business_anchors"]),
        )
        self.assertEqual(decision["area"], ["cement"])
        self.assertEqual([item["source"] for item in evidence["anchor_quotes"]],
                         ["cement:roles.1", "cement:scope"])

    def test_one_correction_and_audit_for_unknown_passage(self):
        client = Mock()
        bad = {**self.result, "target_passage": "P2"}
        client.chat.side_effect = [
            ChatResult(ok=True, finish_reason="stop", content=json.dumps(candidate))
            for candidate in (bad, self.result)
        ]
        _, audit = score(client, "System instruction.", self.pack)
        self.assertEqual(client.chat.call_count, 2)
        self.assertIn("target_passage ID", audit["rejected_reply"]["error"])

    def test_context_limit_is_an_error(self):
        client = Mock()
        client.chat.return_value = ChatResult(ok=False, error_kind="context_limit")
        with self.assertRaisesRegex(ModelError, "context limit"):
            score(client, "System instruction.", self.pack)
        client.chat.assert_called_once()
