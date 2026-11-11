import json
import unittest
from unittest.mock import Mock

from eurlex_provision.simple import ModelError, score, validate_decision
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
            "target_quote": "Kiln dust provision.",
            "anchor_quotes": [{"id": "cement", "quote": "operates a cement kiln"}],
        }

    def test_one_call_and_one_decision_per_provision(self):
        for label in ("DIRECT", "INDIRECT", "IGNORE"):
            with self.subTest(label=label):
                client = Mock()
                result = {**self.result, "level": label}
                if label == "IGNORE":
                    result.update(area=[], anchor_quotes=[])
                client.chat.return_value = ChatResult(
                    ok=True, finish_reason="stop", content=json.dumps(result),
                )
                decision, audit = score(client, "System instruction.", self.pack)
                self.assertEqual(decision, {k: result[k] for k in ("level", "reason", "area")})
                self.assertEqual(audit["evidence"]["target_quote"], result["target_quote"])
                client.chat.assert_called_once()
                sent = json.loads(client.chat.call_args.args[0][1]["content"])
                self.assertEqual(sent, self.pack)

    def test_citations_from_wrong_target_or_anchor_are_rejected(self):
        cases = (
            ({"target_quote": "Limits from another provision."}, "target provision itself"),
            ({"anchor_quotes": [{"id": "cement", "quote": "operates a lime kiln"}]}, "quote for cement"),
            ({"anchor_quotes": []}, "one anchor quote per area"),
            ({"area": ["unknown"]}, "area IDs"),
            ({"reason": " "}, "non-empty reason"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ModelError, message):
                    validate_decision({**self.result, **changes}, self.pack)

    def test_one_correction_and_audit_for_unverified_quote(self):
        client = Mock()
        bad = {**self.result, "target_quote": "Limits from another provision."}
        client.chat.side_effect = [
            ChatResult(ok=True, finish_reason="stop", content=json.dumps(candidate))
            for candidate in (bad, self.result)
        ]
        _, audit = score(client, "System instruction.", self.pack)
        self.assertEqual(client.chat.call_count, 2)
        self.assertIn("target provision itself", audit["rejected_reply"]["error"])

    def test_context_limit_is_an_error(self):
        client = Mock()
        client.chat.return_value = ChatResult(ok=False, error_kind="context_limit")
        with self.assertRaisesRegex(ModelError, "context limit"):
            score(client, "System instruction.", self.pack)
        client.chat.assert_called_once()
