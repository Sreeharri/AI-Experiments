"""Offline regression checks. Run python -m unittest discover -s tests -p test_relevance_round1.py."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import openpyxl

from lr_ai import relevance_model
from tools import relevance


class MockQwen:
    max_model_len = 40960

    def tokenize(self, messages):
        return 45000 if "split-marker" in messages[1]["content"] and '"R2"' in messages[1]["content"] else 1000

    def chat(self, request, **options):
        import json
        data = json.loads(request[1]["content"])
        if "recitals" in data:
            points = [
                {"statement": f"Evidence for {item['ref']}", "references": [item["ref"]]}
                for item in data["recitals"]
            ]
        else:
            points = [point for brief in data["partial_briefs"] for point in brief]
        return SimpleNamespace(parse_json=lambda: ({"points": points}, None))


class RelevanceTests(unittest.TestCase):
    def test_split_groups_exact_and_disjoint(self):
        rows = [
            {"celex": str(i), "group": "linked" if i < 2 else "",
             "stratum": ("DIRECT", "INDIRECT", "IRRELEVANT", "RANDOM")[i % 4]}
            for i in range(400)
        ]
        sample = relevance.split_groups(rows, {str(i): {} for i in range(400)})
        dev, holdout = [{row["celex"] for row in sample[part]} for part in ("dev", "holdout")]
        self.assertEqual((len(dev), len(holdout)), (200, 200))
        self.assertFalse(dev & holdout)
        self.assertEqual("0" in dev, "1" in dev)

    def test_summary_uses_both_chunks(self):
        points = relevance_model.summarize(MockQwen(), "Act", "EN", [
            {"ref": "R1", "text": "split-marker"}, {"ref": "R2", "text": "second recital"},
        ])
        self.assertEqual([p["references"] for p in points], [["R1"], ["R2"]])

    def test_blind_workbook_score_and_holdout_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(run=root, split="dev", labels=None)
            relevance.write(root / "sample.json", {
                "dev": [{"celex": "a", "stratum": "INDIRECT"}],
                "holdout": [{"celex": "b", "stratum": "RANDOM"}],
            })
            for key in ("a", "b"):
                relevance.write(root / "summaries" / f"{key}.json", {
                    "title": "Title", "language": "EN", "points": [],
                    "source_url": "https://example.invalid/", "recitals": [], "articles": [],
                })
            with patch.object(relevance, "client_for") as client:
                args.split = "holdout"
                with self.assertRaisesRegex(ValueError, "freeze"):
                    relevance.classify(args)
                client.assert_not_called()
                args.split = "dev"
                relevance.classify(args)
            relevance.workbook(args)
            path = root / "blind/dev/labels.xlsx"
            book = openpyxl.load_workbook(path)
            self.assertIsNone(book["Label"]["F2"].value)
            self.assertNotIn("INDIRECT", list(book["Label"].values)[1])
            book["Label"]["F2"] = "DIRECT"
            book.save(path)
            book.close()
            relevance.score(args)
            result = relevance.read(root / "score_dev.json")
            self.assertEqual(result["direct_missed"], 1)
            self.assertEqual(result["model_review_rate"], 1)


if __name__ == "__main__":
    unittest.main()
