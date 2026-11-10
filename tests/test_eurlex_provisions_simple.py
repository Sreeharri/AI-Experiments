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

    def test_one_call_and_one_label_per_provision(self):
        for label in ("DIRECT", "INDIRECT", "IGNORE"):
            with self.subTest(label=label):
                client = Mock()
                client.chat.return_value = ChatResult(
                    ok=True, finish_reason="stop", content=json.dumps({"level": label}),
                )
                decision, _ = score(client, "System instruction.", self.pack)
                self.assertEqual(decision, label)
                client.chat.assert_called_once()
                sent = json.loads(client.chat.call_args.args[0][1]["content"])
                self.assertEqual(sent, self.pack)

    def test_other_labels_and_extra_fields_are_rejected(self):
        for result in ({"level": "APPLIES"}, {"level": "NEEDS_REVIEW"},
                       {"level": "DIRECT", "reason": "Extra output."}):
            with self.subTest(result=result):
                client = Mock()
                client.chat.return_value = ChatResult(
                    ok=True, finish_reason="stop", content=json.dumps(result),
                )
                with self.assertRaises(ModelError):
                    score(client, "System instruction.", self.pack)

    def test_context_limit_is_an_error(self):
        client = Mock()
        client.chat.return_value = ChatResult(ok=False, error_kind="context_limit")
        with self.assertRaisesRegex(ModelError, "context limit"):
            score(client, "System instruction.", self.pack)
