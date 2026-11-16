import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from enrich_eurlex_corpus import verify_enriched
from eurlex_enrichment.enrichment import build
from eurlex_enrichment.repair import (decode_glyph_codes, inline_provisions, is_false_heading,
                                      merge_false_headings)
from test_corpus_source_recovery import CELEX, PDF, VERSION, data, element

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import repair_enrichment_gaps as gaps  # noqa: E402


def row(key, kind, label, ordinal, text, heading=None, parent=None):
    return {**element(key, kind, label, ordinal, text, parent=parent), "heading": heading}


def amending_act():
    """Article 1 and the annex lost their text to rows that begin inside a sentence."""
    return [
        row("a1", "ARTICLE", "Article 1", 1, "Article 1", "Article 1"),
        row("x1", "ANNEX", "Annex II", 2, "Annex II to Directive 70/221/EEC is amended in accordance with the Annex.",
            "Annex II to Directive 70/221/EEC is amended in accordance"),
        row("x1p", "UNNUMBERED_PARAGRAPH", "Annex II unnumbered paragraph 1", 3, "with the Annex.", parent="x1"),
        row("a2", "ARTICLE", "Article 2", 4, "Article 2 Member States shall refuse type-approval.", "Article 2"),
        row("n", "ANNEX", "Annex", 5, "ANNEX", "ANNEX"),
        row("x2", "ANNEX", "Annex II", 6, "Annex II to Directive 70/221/EEC is amended as follows: the vehicle "
            "shall be tested at rest.", "Annex II to Directive 70/221/EEC is amended as follows:"),
        row("a15", "ARTICLE", "Article 15", 7, "Article 15 of Directive 2012/19/EU, make dismantling information "
            "available.", "Article 15 of Directive 2012/19/EU, make dismantling information"),
    ]


class FalseHeadingTests(unittest.TestCase):
    def check(self, kind, heading, expected, text=None):
        stored = {"element_type": kind, "parent_id": None, "heading": heading, "body_text": text or heading}
        self.assertEqual(is_false_heading(stored), expected, heading or text)

    def test_real_headings_are_kept(self):
        for kind, heading in (("ARTICLE", "Article 1"), ("ARTICLE", "Article 12a"), ("ANNEX", "ANNEX"),
                              ("ANNEX", "Annex II"), ("ANNEX", "ANNEX II WASTE ACCEPTANCE CRITERIA"),
                              ("ANNEX", "ANNEX I to the Agreement"), ("ARTICLE", "Article 3 Scope"),
                              ("ARTICLE", "Article 1 Amendments to Regulation (EU) 2019/424"),
                              ("ARTICLE", "Article 1 Regulation (EC) No 1/2003 is amended as follows:"),
                              ("ARTICLE", "Subject matter")):
            self.check(kind, heading, False)

    def test_sentences_and_citations_are_false_headings(self):
        for kind, heading in (("ANNEX", "Annex II to Directive 70/221/EEC is amended in accordance"),
                              ("ANNEX", "Annexes I and II to Directive 2008/68/EC are amended as follows:"),
                              ("ANNEX", "Annex."), ("ANNEX", "Annex I.1 (b) (i):"), ("ANNEX", "Annex III Annex IV"),
                              ("ARTICLE", "Article 6(2) of Directive 2008/68/EC."),
                              ("ARTICLE", "Article 2, point 22.’;"), ("ARTICLE", "Article 44 Article 33"),
                              ("ARTICLE", "Article 4(4) shall apply from 1 July 2021."),
                              ("ARTICLE", "Article 8 - Design competition .......... 10")):
            self.check(kind, heading, True)

    def test_rows_without_a_stored_heading_need_a_clear_citation(self):
        self.check("ARTICLE", None, False, "Article 1 Regulation (EC) No 1/2003 is amended as follows: (1) ...")
        self.check("ARTICLE", None, False, "The operator shall retain the records described in Annex I.")
        self.check("ARTICLE", None, False, "Annex I, Section 3.1.")      # an article that only points to an annex
        self.check("ARTICLE", None, True, "Article 17 of Directive 89/391/EEC.")
        self.check("ANNEX", None, True, "Annex II to Directive 70/221/EEC is amended")

    def test_merge_puts_text_and_children_back_under_the_real_provision(self):
        rows, merged = merge_false_headings(amending_act())
        self.assertEqual([r["id"] for r in rows], ["a1", "x1p", "a2", "n"])
        by_id = {r["id"]: r for r in rows}
        self.assertIn("is amended in accordance with the Annex.", by_id["a1"]["body_text"])
        self.assertEqual(by_id["x1p"]["parent_id"], "a1")
        self.assertIn("tested at rest", by_id["n"]["body_text"])
        self.assertIn("dismantling information", by_id["n"]["body_text"])
        self.assertEqual({label: len(items) for label, items in merged.items()}, {"Article 1": 1, "Annex": 2})

    def test_nothing_changes_without_a_real_provision_to_merge_into(self):
        only_false = [row("x", "ANNEX", "Annex II", 1, "Annex II to Directive 1 is amended.", "Annex II to Directive")]
        self.assertEqual(merge_false_headings(only_false), (only_false, {}))
        clean = data(PDF)["legal_elements"]
        self.assertEqual(merge_false_headings(clean), (clean, {}))

    def test_build_reports_the_repair_and_has_no_duplicate_labels(self):
        source = data(PDF)
        source["legal_elements"] = amending_act()
        enriched = build(source, PDF, "STORED_SNAPSHOT", recover_from_database=True)
        metrics = verify_enriched(enriched, CELEX, VERSION)
        self.assertEqual((metrics["articles"], metrics["annexes"]), (2, 1))
        labels = [unit["label"] for unit in enriched["units"] if unit["kind"] in {"ARTICLE", "ANNEX"}]
        self.assertEqual(labels, ["Article 1", "Article 2", "Annex"])
        self.assertEqual(list(enriched["document"]["provision_repair"]["merged_false_headings"]),
                         ["Article 1", "Annex"])
        self.assertNotIn("provision_repair", build(data(PDF), PDF, "STORED_SNAPSHOT",
                                                   recover_from_database=True)["document"])


class TextRecoveryTests(unittest.TestCase):
    ACT = ("COUNCIL DIRECTIVE on landfill, and in particular Article 130s thereof, HAS ADOPTED THIS DIRECTIVE "
           "Article 1 Overall objective 1. Operators shall prevent harm as set out in Article 3 of this Directive. "
           "Article 2 Definitions For the purposes of Article 1, waste means any substance. "
           "Article 3 Scope Member States shall apply this Directive to any landfill. Official Journal EN "
           "ANNEX I GENERAL REQUIREMENTS The location must take account of Annex II. "
           "ANNEX II WASTE ACCEPTANCE CRITERIA Waste is accepted after testing.")

    def test_glyph_codes_become_text(self):
        coded = " ".join("".join(f"/C{ord(c)}" for c in word) for word in "COUNCIL DIRECTIVE on the landfill".split())
        self.assertEqual(decode_glyph_codes(coded), "COUNCIL DIRECTIVE on the landfill")
        self.assertEqual(decode_glyph_codes("/C145waste/C146 " + coded), "‘waste’ COUNCIL DIRECTIVE on the landfill")
        self.assertEqual(decode_glyph_codes("Path /C12 only"), "Path /C12 only")

    def test_one_block_of_text_is_cut_at_headings_not_at_citations(self):
        parts = inline_provisions(self.ACT)
        self.assertEqual([part[2] for part in parts], ["Article 1", "Article 2", "Article 3", "Annex I", "Annex II"])
        self.assertTrue(parts[0][3].startswith("Overall objective 1. Operators"))
        self.assertTrue(parts[0][3].endswith("Article 3 of this Directive."))
        self.assertTrue(parts[3][3].endswith("Annex II."))
        self.assertEqual(inline_provisions("Text citing Article 1 and Article 2 of the Treaty."), [])

    def test_document_stored_as_one_block_gets_article_targets(self):
        source = data(PDF)
        coded = " ".join("".join(f"/C{ord(c)}" for c in word) for word in self.ACT.split())
        source["legal_elements"] = [element("doc", "DOCUMENT_TEXT", "Document text", 1, coded)]
        with patch("eurlex_enrichment.document_recovery.pdf_text", return_value=[]):
            enriched = build(source, PDF, "STORED_SNAPSHOT", recover_from_database=True, recover_document_text=True)
        metrics = verify_enriched(enriched, CELEX, VERSION)
        self.assertEqual((metrics["articles"], metrics["annexes"], metrics["document_text_targets"]), (3, 2, 0))
        self.assertEqual(enriched["document"]["source_coverage"]["document_recovery"], "INLINE_HEADING_RECOVERY")
        self.assertIn("Check provision boundaries", " ".join(enriched["document"]["source_coverage"]["limitations"]))

    def test_longer_source_text_replaces_a_single_stored_page(self):
        source = data(PDF)
        source["legal_elements"] = [element("doc", "DOCUMENT_TEXT", "Document text", 1, "STATEMENT of two lines.")]
        pages = [(1, 1, "The operator shall keep records of every transport operation for one year."),
                 (2, 1, "The competent authority may inspect these records at the premises.")]
        with patch("eurlex_enrichment.document_recovery.pdf_text", return_value=pages):
            enriched = build(source, PDF, "STORED_SNAPSHOT", recover_from_database=True, recover_document_text=True)
        text = enriched["elements"][f"{CELEX}:recovered:document-text"]["text"]
        self.assertIn("competent authority may inspect", text)


class RepairToolTests(unittest.TestCase):
    def test_find_and_check_prepare_a_screening_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, out = root / "old", root / "new", root / "run"
            old.mkdir(), new.mkdir()
            clean, broken = data(PDF), data(PDF)
            broken.update(celex="32006L0020")
            broken["legal_elements"] = amending_act()
            manifest = {CELEX: VERSION, "32006L0020": VERSION, "31999L0099": VERSION}
            untouched = data(PDF)
            untouched.update(celex="31999L0099")
            for source in (clean, broken, untouched):
                # The old files come from the enrichment without the repair step.
                with patch("eurlex_enrichment.enrichment.merge_false_headings", lambda rows: (rows, {})):
                    enriched = build(source, PDF, "STORED_SNAPSHOT", recover_from_database=True)
                (old / f"{source['celex']}_enriched.json").write_text(json.dumps(enriched), encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            args = SimpleNamespace(manifest=root / "manifest.json", enriched_dir=old, repaired_dir=new, output_dir=out)
            with patch.object(gaps, "SMOKE_ACTS", (CELEX,)):
                self.assertEqual(gaps.find(args), 0)
                repair = json.loads((out / "repair_manifest.json").read_text())
                self.assertEqual(set(repair), {CELEX, "32006L0020"})   # the broken act and the smoke-test act
                found = json.loads((out / "find_report.json").read_text())
                self.assertEqual(found["acts"], {"32006L0020": {"FALSE_HEADINGS": 3}})
                for source in (clean, broken):
                    enriched = build(source, PDF, "STORED_SNAPSHOT", recover_from_database=True)
                    (new / f"{source['celex']}_enriched.json").write_text(json.dumps(enriched), encoding="utf-8")
                self.assertEqual(gaps.check(args), 0)
            report = json.loads((out / "repair_report.json").read_text())
            self.assertEqual(report["repaired_acts"], ["32006L0020"])
            self.assertEqual((report["false_headings_left"], report["still_without_provisions"]), ({}, []))
            self.assertEqual(json.loads((out / "screen_manifest.json").read_text()), repair)
            summary = json.loads((new / "enrichment_run_summary.json").read_text())
            self.assertEqual((summary["acts_planned"], summary["acts_valid"], summary["acts_failed"]), (2, 2, 0))


if __name__ == "__main__":
    unittest.main()
