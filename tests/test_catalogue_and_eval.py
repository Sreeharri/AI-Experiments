import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from lr_ai.catalogue_input import Catalogue, validate_celex


class CatalogueTests(unittest.TestCase):
    def test_parenthesised_celex_and_language_fallback(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            celex = "32015D0016(01)"
            (path / "acts.jsonl").write_text(json.dumps({"celex": celex, "title_en": "Title"}) + "\n")
            (path / "text_en.jsonl").write_text(json.dumps({"celex": celex, "articles": []}) + "\n")
            (path / "text_de.jsonl").write_text(json.dumps({
                "celex": celex, "language": "DE", "version": {"key": "v1"},
                "articles": [{"n": "1", "heading": "Scope", "text": "Source"}],
                "recitals_version": "original-v1"
            }) + "\n")
            catalogue = Catalogue(path)
            a = catalogue.get(celex)
            self.assertEqual(a["version"]["language"], "DE")
            self.assertEqual(a["elements"][0]["body_text"], "Source")
            self.assertEqual(a["recitals_version"], "original-v1")
            self.assertEqual(catalogue.ids(), [celex])

    def test_invalid_celex_rejected(self):
        for value in ("../data", "32015D0016/01", ""):
            with self.assertRaises(ValueError):
                validate_celex(value)

    def test_all_review_does_not_pass_evaluation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            celex = "32015D0016(01)"
            (path / f"{celex}.json").write_text(json.dumps({
                "status": "needs_review", "decision": {"level": "NEEDS_REVIEW"},
                "elapsed_seconds": 1.0
            }))
            labels = path / "labels.csv"
            labels.write_text(f"celex,label\n{celex},IRRELEVANT\n")
            command = [sys.executable, "tools/evaluate_document_relevance.py",
                       "--run", str(path), "--labels", str(labels),
                       "--max-review-rate", "0.2", "--min-ignore-removal", "0.5"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            data = json.loads(result.stdout)
            self.assertFalse(data["all_gates_pass"])
            self.assertEqual(data["review_rate"], 1)
            self.assertEqual(data["mean_seconds_per_act"], 1)
