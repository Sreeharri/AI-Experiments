import json
import unittest
from unittest.mock import Mock

from eurlex_provision.simple import score
from lr_ai.qwen_client import ChatResult


class SimpleScoringTests(unittest.TestCase):
    def setUp(self):
        self.pack = {
            "business_anchors": {"cement": {"label": "Cement"}},
            "document_level_result": {"level": "DIRECT", "reason": "Act context.", "area": ["cement"]},
            "provision": {"locator": "BAT 17", "blocks": [{"text": "Kiln dust provision."}]},
        }

    def test_sends_one_request_and_returns_provision_decision(self):
        result = {"level": "INDIRECT", "reason": "The supplier operates the kiln.", "area": ["cement"]}
        client = Mock()
        client.chat.return_value = ChatResult(ok=True, finish_reason="stop", content=json.dumps(result))
        decision, _ = score(client, "System instruction.", self.pack)
        self.assertEqual(decision, result)
        client.chat.assert_called_once()
        sent = json.loads(client.chat.call_args.args[0][1]["content"])
        self.assertEqual(sent, self.pack)

    def test_missing_facts_and_context_limit_are_held(self):
        for reply in (
            ChatResult(ok=True, finish_reason="stop", content=json.dumps({
                "level": "NEEDS_REVIEW", "reason": "The kiln type is unknown.", "area": []})),
            ChatResult(ok=False, error_kind="context_limit"),
        ):
            with self.subTest(reply=reply):
                client = Mock()
                client.chat.return_value = reply
                decision, _ = score(client, "System instruction.", self.pack)
                self.assertEqual(decision["level"], "NEEDS_REVIEW")
                client.chat.assert_called_once()

    def test_invalid_area_does_not_export(self):
        client = Mock()
        client.chat.return_value = ChatResult(ok=True, finish_reason="stop", content=json.dumps({
            "level": "DIRECT", "reason": "Fixture.", "area": ["invented_anchor"]}))
        decision, _ = score(client, "System instruction.", self.pack)
        self.assertEqual(decision["level"], "NEEDS_REVIEW")
