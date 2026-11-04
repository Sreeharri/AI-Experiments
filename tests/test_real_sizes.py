"""Checks with the real business profile size (the other tests count every request as 100 tokens).

Token counts are estimated as characters / 4, which is on the safe side for this JSON
(the endpoint check measured 4.9 characters per token for English prose).
"""
from datetime import date
import importlib.util
import json
from pathlib import Path
import unittest

from lr_ai import document_filter as f
from lr_ai.catalogue_input import adapt

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("document_relevance", ROOT / "tools" / "document_relevance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def estimate(value):
    return len(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)) // 4


class RealSizeTests(unittest.TestCase):
    def test_fixed_part_leaves_room_for_excerpts(self):
        profile, _ = runner.profile(ROOT / "config/business_anchors_en.yaml", ROOT / "config/domains.yaml")
        outline = [{"path": f"article/{i}/row{i}", "type": "ARTICLE", "heading": f"Article {i} / Obligations of operators"}
                   for i in range(1, 2001)]
        fixed = estimate(f.DECISION_PROMPT) + estimate(profile) + estimate(f.compact_outline(outline))
        parser_default = 24000
        self.assertLess(fixed, parser_default - 5000, f"fixed input about {fixed} tokens")

    def test_outline_cap(self):
        outline = [{"path": str(i), "type": "ARTICLE", "heading": "x" * 500} for i in range(400)]
        compact = f.compact_outline(outline)
        self.assertEqual(len(compact), f.MAX_OUTLINE_ENTRIES + 1)
        self.assertEqual(compact[-1]["type"], "NOTE")
        self.assertTrue(all(len(e["heading"]) <= f.MAX_HEADING_CHARS for e in compact[:-1]))

    def test_jsonl_record_gives_title_and_metadata(self):
        meta = {"celex": "32008L0098", "act_type": "directive",
                "title": {"en": "Directive 2008/98/EC on waste", "de": "Richtlinie 2008/98/EG über Abfälle"},
                "dates": {"document": "2008-11-19"},
                "resource_type_label": [{"id": "DIR", "en": "Directive", "de": "Richtlinie"}],
                "addressees": [{"id": "EUMS", "en": "The Member States", "de": None}],
                "legal_basis": [{"celex": "12006E175", "detail": {"BUILD_INFO": "noise"}, "label": "EC Treaty Art. 175(1)"}],
                "family": {"changes": [{"relation": "REPEALS", "celex": "32006L0012", "title_en": None, "title_de": None}]}}
        text = {"celex": "32008L0098", "language": "EN", "version": {"key": "v1"},
                "articles": [{"n": "2", "heading": "Article 2 / Exclusions from the scope", "text": "This Directive shall not apply to"}]}
        act = adapt(meta, text)
        self.assertEqual(act["document"]["title_en"], "Directive 2008/98/EC on waste")
        self.assertEqual(act["instrument"]["title"], "Directive 2008/98/EC on waste")
        self.assertEqual(act["document"]["act_type"], "Directive")
        self.assertEqual(act["document"]["addressed_to"], ["The Member States"])
        self.assertEqual(act["document"]["legal_basis"], ["EC Treaty Art. 175(1)"])
        self.assertEqual(act["document"]["changes"][0]["celex"], "32006L0012")
        blocks, _, problems = f.prepare(act, date(2026, 11, 4))
        self.assertEqual(problems, [])
        self.assertEqual(blocks[0]["type"], "ARTICLE")


if __name__ == "__main__":
    unittest.main()
