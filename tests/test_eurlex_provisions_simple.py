import json
import unittest
from unittest.mock import Mock

from eurlex_provision.simple import ModelError, anchor_sources, score, target_passages, validate_decision
from lr_ai.qwen_client import ChatResult


class SimpleScoringTests(unittest.TestCase):
    def setUp(self):
        self.pack = {
            "business_anchors": {"cement": {"scope": "Max Bögl operates a cement kiln."}},
            "provision": {"locator": "BAT 17", "blocks": [{"text": "Kiln dust provision."}]},
            "direct_references": [{"text": "Limits from another provision."}],
        }
        self.result = {
            "level": "DIRECT", "reason": "Evidence for the decision.", "area": ["cement"],
            "target_passage": "P1",
            "anchor_sources": ["cement:scope"],
        }

    def test_one_call_and_one_decision_per_provision(self):
        for label in ("DIRECT", "INDIRECT", "IGNORE"):
            with self.subTest(label=label):
                client = Mock()
                result = {**self.result, "level": label}
                if label == "IGNORE":
                    result.update(area=[], anchor_sources=[])
                client.chat.return_value = ChatResult(
                    ok=True, finish_reason="stop", content=json.dumps(result),
                )
                decision, audit = score(client, "System instruction.", self.pack)
                self.assertEqual(decision, {k: result[k] for k in ("level", "reason", "area")})
                self.assertEqual(audit["evidence"]["target_quote"], "Kiln dust provision.")
                client.chat.assert_called_once()
                sent = json.loads(client.chat.call_args.args[0][1]["content"])
                self.assertEqual(sent, {**self.pack, "target_passages": {"P1": "Kiln dust provision."}})

    def test_citations_from_wrong_target_or_anchor_are_rejected(self):
        cases = (
            ({"target_passage": "P2"}, "target_passage ID"),
            ({"anchor_sources": ["cement:company_facts.1"]}, "one source ID per area"),
            ({"anchor_sources": []}, "one source ID per area"),
            ({"area": ["unknown"]}, "area IDs"),
            ({"reason": " "}, "non-empty reason"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ModelError, message):
                    validate_decision(
                        {**self.result, **changes}, target_passages(self.pack["provision"]),
                        anchor_sources(self.pack["business_anchors"]), ["cement"],
                    )

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
