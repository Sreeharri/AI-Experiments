from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from enrich_eurlex_corpus import process, verify_enriched
from eurlex_enrichment.enrichment import NoUsableProvisions, build
from eurlex_enrichment.inputs import read_json
from eurlex_provision.evidence import Evidence, TARGET_KINDS
from test_corpus_source_recovery import PDF, VERSION, data, element


def document(raw, body, language="EN", celex="31999L0031"):
    source = data(raw)
    source.update(celex=celex)
    source["version"].update(language=language, content_sha256=sha256(raw).hexdigest())
    source["legal_elements"] = [element("original", "DOCUMENT_TEXT", "Document text", 1, body)]
    return source


def recover(source, raw):
    return build(source, raw, "STORED_SNAPSHOT", recover_from_database=True, recover_document_text=True)


class FullDocumentRecoveryTests(unittest.TestCase):
    def test_pdf_native_headings_create_targets_with_source_pages(self):
        lines = [(2, 1, "Article 1"), (2, 2, "Operators shall keep a record."),
                 (3, 1, "Article 2"), (3, 2, "The record shall be retained."),
                 (3, 3, "ANNEX I"), (3, 4, "List of required records.")]
        source = document(PDF, "Whole act stored without line breaks")
        with patch("eurlex_enrichment.document_recovery.pdf_text", return_value=lines):
            enriched = recover(source, PDF)
        metrics = verify_enriched(enriched, source["celex"], VERSION)
        self.assertEqual((metrics["articles"], metrics["annexes"]), (2, 1))
        self.assertEqual(enriched["elements"]["original"]["text"], "Whole act stored without line breaks")
        target = enriched["elements"][f"{source['celex']}:recovered:article:2"]
        self.assertEqual(target["recovery_source"]["start"]["page"], 3)
        self.assertEqual(target["text"], "The record shall be retained.")

    def test_conflicting_heading_sequences_keep_full_document(self):
        source = document(PDF, "Article 1\nFirst requirement.\nArticle 2\nSecond requirement.")
        with patch("eurlex_enrichment.document_recovery.pdf_text",
                   return_value=[(1, 1, "Article 1"), (1, 2, "First requirement.")]):
            enriched = recover(source, PDF)
        self.assertEqual(enriched["summary"]["articles"], 0)
        self.assertEqual(enriched["summary"]["document_text_targets"], 1)

    def test_column_text_with_an_unaccounted_heading_remains_document_only(self):
        source = document(PDF, "Whole act stored without line breaks")
        lines = [(1, 1, "Article 1"), (1, 2, "Operators shall keep a record. Article 2"),
                 (1, 3, "Retain the record for five years.")]
        with patch("eurlex_enrichment.document_recovery.pdf_text", return_value=lines):
            self.assertEqual(recover(source, PDF)["summary"]["document_text_targets"], 1)

    def test_german_corrigendum_is_preserved_without_inventing_articles(self):
        raw = b"<html><body><p>Berichtigung</p><p>Artikel 1</p><p>Die Bezeichnung wird ersetzt.</p></body></html>"
        source = document(raw, "Artikel 1\nDie Bezeichnung wird ersetzt.", "DE", "32024R1268R(01)")
        enriched = recover(source, raw)
        self.assertEqual(enriched["document"]["language"], "DE")
        self.assertEqual(enriched["document"]["source_coverage"]["document_recovery"], "CORRIGENDUM_TEXT")
        evidence = Evidence(enriched, {})
        self.assertTrue(all(unit["kind"] not in TARGET_KINDS for unit in evidence.units.values()))
        self.assertEqual(verify_enriched(enriched, source["celex"], VERSION)["document_text_targets"], 1)

    def test_german_article_html_recovers_from_dom_when_database_is_flat(self):
        raw = b"<html><body><p>Artikel 1</p><p>Diese Verordnung tritt in Kraft.</p></body></html>"
        source = document(raw, "Artikel 1 Diese Verordnung tritt in Kraft.", "DE", "32005R0077")
        enriched = recover(source, raw)
        self.assertEqual(verify_enriched(enriched, source["celex"], VERSION)["articles"], 1)
        self.assertEqual(enriched["units"][0]["label"], "Artikel 1")

    def test_missing_text_still_fails_instead_of_creating_empty_target(self):
        source = document(PDF, "")
        with self.assertRaises(NoUsableProvisions):
            recover(source, PDF)

    def test_corpus_report_separates_document_only_from_empty_files_and_resumes(self):
        raw = b"<html><body><p>Berichtigung</p></body></html>"
        source = document(raw, "Text der Berichtigung.", "DE", "32024R1268R(01)")
        with TemporaryDirectory() as directory:
            output = Path(directory)
            args = SimpleNamespace(output_dir=output, verify_only=False, xhtml_dir=None, fetch_xhtml=False)
            manifest = {source["celex"]: VERSION}
            with patch("enrich_eurlex_corpus.from_postgres", return_value=source) as fetch:
                self.assertFalse(process(args, manifest, "mock"))
                self.assertFalse(process(args, manifest, "mock"))
                fetch.assert_called_once()
            report = read_json(output / "enrichment_share_summary.json")
            self.assertEqual(report["acts_failed"], 0)
            self.assertEqual(report["acts_with_provision_targets"], 0)
            self.assertEqual(report["acts_with_document_text_only"]["count"], 1)
            self.assertEqual(report["acts_without_any_targets"]["count"], 0)
