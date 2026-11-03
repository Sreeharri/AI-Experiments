"""Offline tests for the selected-act summarizer."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from tools import summarize_eurlex_recitals as cli


class FakeClient:
    model = "test-model"
    max_model_len = 4096
    api_base = "https://test.invalid/v1"

    def discover(self):
        return {"ok": True}

    def tokenize(self, messages):
        return 3000 if "split-marker" in messages[-1]["content"] and '"R2"' in messages[-1]["content"] else 100

    def chat(self, messages, **kwargs):
        data = json.loads(messages[-1]["content"])
        if "recitals" in data:
            points = [
                {"statement": "Grounded summary", "references": [recital["ref"]]}
                for recital in data["recitals"]
            ]
        else:
            points = [point for brief in data["partial_briefs"] for point in brief]
        return SimpleNamespace(parse_json=lambda: ({"points": points}, None))


class SummarizationTests(unittest.TestCase):
    def test_zip_chunking_language_fallback_missing_and_resume(self):
        acts = [
            {"id": "A", "title_en": "First", "recitals": {
                "en": [{"n": "7", "text": "split-marker"}, {"n": "8", "text": "Second recital"}],
                "de": [{"text": "Unused"}],
            }},
            {"id": "B", "title_en": "Second", "recitals": {
                "en": [], "de": [{"n": "3", "text": "Deutscher Erwägungsgrund"}],
            }},
            {"id": "C", "title_en": "Third", "recitals": {"en": [], "de": []}},
        ]
        client = FakeClient()
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            args = SimpleNamespace(input=path / "selected.zip", output=path / "result.json",
                                   split="dev", limit=None, env=path / ".env", context_limit=None)
            with ZipFile(args.input, "w") as archive:
                archive.writestr("selected_acts/set_1_development.json", json.dumps(acts))
            with patch.object(cli.qwen_client.QwenClient, "from_env", return_value=client):
                with patch.object(client, "chat", wraps=client.chat) as chat:
                    self.assertEqual(cli.run(args), 0)
                    self.assertEqual(chat.call_count, 4)
                    self.assertEqual(cli.run(args), 0)
                    self.assertEqual(chat.call_count, 4)
                result = json.loads(args.output.read_text(encoding="utf-8"))["acts"]
                self.assertEqual(result["A"]["recital_numbers"], {"R1": "7", "R2": "8"})
                self.assertEqual([p["references"] for p in result["A"]["points"]], [["R1"], ["R2"]])
                self.assertEqual(result["B"]["source_language"], "de")
                self.assertEqual(result["B"]["summary_language"], "en")
                self.assertEqual(result["C"]["status"], "no_recitals")
                self.assertEqual(result["C"]["points"], [])

    def test_unknown_citation_fails(self):
        client = FakeClient()
        response = {"points": [{"statement": "Unsupported", "references": ["R99"]}]}
        with patch.object(client, "chat", return_value=SimpleNamespace(parse_json=lambda: (response, None))):
            with self.assertRaisesRegex(ValueError, "Unknown recital references"):
                cli.relevance_model.summarize(client, "Title", "EN", [{"ref": "R1", "text": "Text"}])


if __name__ == "__main__":
    unittest.main()
