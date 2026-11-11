import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from enrich_eurlex_corpus import process, verify_enriched
from eurlex_enrichment.enrichment import NoUsableProvisions, build
from eurlex_enrichment.inputs import read_json
from eurlex_enrichment.pdf_source import heading_index, table_owner
from eurlex_provision.evidence import Evidence
from eurlex_provision.simple import build_pack


PDF = b"%PDF-1.4"
CELEX = "31964L0475"
VERSION = "a4882db3-f6e3-46b8-9a83-04da3aaf790b"
ARTICLE_TEXT = "The operator shall retain the records described in Annex I."
ANNEX_TEXT = "Keep the operating log in the records for five years."


def element(key, kind, label, ordinal, text, number=None, parent=None):
    return {
        "id": key, "element_type": kind, "identifier": label, "ordinal": ordinal,
        "body_text": text, "number": number, "parent_id": parent,
        "path": f"/{kind.lower()}/{number or key}", "source_locator": "missing",
    }


def data(raw):
    return {
        "celex": CELEX, "title": "A test act",
        "version": {
            "id": VERSION, "language": "EN", "source_format": "PDF_TEXT" if raw == PDF else "LEGACY_HTML",
            "content_sha256": hashlib.sha256(raw).hexdigest(),
        },
        "raw_content_snapshots": [{"payload_bytes_raw": raw}],
        "legal_elements": [
            element("article", "ARTICLE", "Article 1", 1, ARTICLE_TEXT, "1"),
            element("annex", "ANNEX", "Annex I", 2, ANNEX_TEXT, "I"),
        ],
        "element_references": [],
    }


class SourceRecoveryTests(unittest.TestCase):
    def test_legacy_annex_uses_database_body_when_source_does_not_match(self):
        raw = b"<html><body><p>Unrelated source block</p></body></html>"
        enriched = build(data(raw), raw, "STORED_SNAPSHOT", recover_from_database=True)
        self.assertEqual(enriched["document"]["source_coverage"]["database_annex_ids"], ["annex"])
        self.assertEqual(enriched["summary"]["xhtml_data_tables"], 0)
        evidence = Evidence(enriched, {})
        pack = build_pack(evidence, evidence.units["article"], {})
        self.assertIn(ANNEX_TEXT, str(pack["direct_references"]))
        self.assertTrue(pack["document"]["source_coverage"])

    def test_legacy_flat_annex_can_match_its_exact_stored_body(self):
        raw = f"<html><body><p>ANNEX I</p><p>{ANNEX_TEXT}</p></body></html>".encode()
        enriched = build(data(raw), raw, "STORED_SNAPSHOT", recover_from_database=True)
        self.assertNotIn("source_coverage", enriched["document"])
        self.assertEqual(enriched["document"]["annex_locator_recoveries"][0]["match"], "DATABASE_BODY")

    def test_pdf_with_no_provisions_is_reported(self):
        source = data(PDF)
        source["legal_elements"] = [element("doc", "DOCUMENT_TEXT", "Document text", 1, "Whole act")]
        with self.assertRaises(NoUsableProvisions):
            build(source, PDF, "STORED_SNAPSHOT", recover_from_database=True)

    def test_pdf_table_scope_rejects_boundary_overlap_and_duplicate_headings(self):
        annexes = {"first": ("Annex I", "Registration number Annual emissions"),
                   "second": ("Annex II", "Records")}
        headings = [((1, 120), "ANNEX I"), ((2, 150), "ANNEX II")]
        positions, owners = heading_index(headings, annexes)
        grid = {"source": {"page": 1, "bbox": [10, 125, 210, 160]},
                "rows": [{"cells": [{"text": "Registration number"}, {"text": "Annual emissions"}]}]}
        bodies = {"first": "registrationnumberannualemissions", "second": "records"}
        self.assertEqual(table_owner(grid, bodies, positions, owners), "first")
        grid["source"]["page"] = 2
        self.assertIsNone(table_owner(grid, bodies, positions, owners))
        self.assertEqual(heading_index(headings + [((3, 100), "ANNEX I")], annexes), ([], []))

    def test_pdf_corpus_resume_keeps_partial_coverage_visible(self):
        with TemporaryDirectory() as directory:
            output = Path(directory)
            args = SimpleNamespace(output_dir=output, verify_only=False, xhtml_dir=None, fetch_xhtml=False)
            with patch("enrich_eurlex_corpus.from_postgres", return_value=data(PDF)) as fetch:
                self.assertFalse(process(args, {CELEX: VERSION}, "mock"))
                self.assertFalse(process(args, {CELEX: VERSION}, "mock"))
                fetch.assert_called_once()
            result = read_json(output / f"{CELEX}_enriched.json")
            self.assertEqual(verify_enriched(result, CELEX, VERSION)["articles"], 1)
            self.assertEqual(result["document"]["source_parser"], "PDF_DATABASE")
            summary = read_json(output / "enrichment_share_summary.json")
            self.assertEqual(summary["acts_written"], 0)
            self.assertEqual(summary["acts_with_partial_source_coverage"], 1)
            self.assertEqual(summary["acts_with_pdf_scan_errors"], 1)
            self.assertEqual(summary["acts_failed"], 0)
