import json
import unittest
from unittest.mock import Mock

from eurlex_provision.simple import ModelError, score
from lr_ai.qwen_client import ChatResult


class SimpleScoringTests(unittest.TestCase):
    def setUp(self):
        self.pack = {
            "business_anchors": {"cement": {"label": "Cement"}},
            "provision": {"locator": "BAT 17", "blocks": [{"text": "Kiln dust provision."}]},
        }

    def test_one_call_and_one_decision_per_provision(self):
        for label in ("DIRECT", "INDIRECT", "IGNORE"):
            with self.subTest(label=label):
                client = Mock()
                result = {"level": label, "reason": "Evidence for the decision.",
                          "area": [] if label == "IGNORE" else ["cement"]}
                client.chat.return_value = ChatResult(
                    ok=True, finish_reason="stop", content=json.dumps(result),
                )
                decision, _ = score(client, "System instruction.", self.pack)
                self.assertEqual(decision, result)
                client.chat.assert_called_once()
                sent = json.loads(client.chat.call_args.args[0][1]["content"])
                self.assertEqual(sent, self.pack)

    def test_incomplete_or_unsupported_decisions_are_rejected(self):
        result = {"level": "DIRECT", "reason": "Evidence for the decision.", "area": ["cement"]}
        cases = (
            {"level": "DIRECT"},
            {**result, "level": "APPLIES"},
            {**result, "reason": " "},
            {**result, "area": ["unknown"]},
            {**result, "area": ["cement", "cement"]},
            {**result, "area": []},
            {**result, "level": "IGNORE"},
            {**result, "unexpected": True},
        )
        for invalid in cases:
            with self.subTest(result=invalid):
                client = Mock()
                client.chat.return_value = ChatResult(
                    ok=True, finish_reason="stop", content=json.dumps(invalid),
                )
                with self.assertRaises(ModelError):
                    score(client, "System instruction.", self.pack)

    def test_context_limit_is_an_error(self):
        client = Mock()
        client.chat.return_value = ChatResult(ok=False, error_kind="context_limit")
        with self.assertRaisesRegex(ModelError, "context limit"):
            score(client, "System instruction.", self.pack)
