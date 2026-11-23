import json
from pathlib import Path
import tempfile
import unittest

import inspect_ai_results as inspect


def pack(text, actor="", title="Regulation on things"):
    return {"evidence_pack": {"document": {"title": title}, "provision": {"locator": "Article 1", "text": text}},
            "details": {"actor": actor}}


class PatternTests(unittest.TestCase):
    def test_authority_rules_are_told_apart_from_business_rules(self):
        self.assertEqual(inspect.authority_kind("", "1. The Commission shall publish a report by 2025."), "authority")
        self.assertEqual(inspect.authority_kind("Member States", "Member States shall designate a body."), "authority")
        self.assertEqual(inspect.authority_kind(
            "", "Member States shall ensure that operators keep a register of all waste."), "via")
        self.assertIsNone(inspect.authority_kind("", "The manufacturer shall keep the documents for 10 years."))
        # The authority of the previous sentence does not make the next one an authority rule.
        self.assertIsNone(inspect.authority_kind(
            "verifier", "It goes to the competent authority of the Member State. The verifier shall submit it."))

    def test_english_text_is_found(self):
        self.assertTrue(inspect.english("Mediation center for dispute resolution"))
        self.assertFalse(inspect.english("Pflichten der Hersteller für Batterien"))


class SummaryTests(unittest.TestCase):
    def summary(self, point, quote, kind="DUTY", overview="Der Artikel regelt etwas."):
        return {"headline": "Pflichten", "overview": overview,
                "bullets": [{"kind": kind, "text": point, "quotes": [quote]}]}

    def checks(self, summary, target="Article 1", flags=()):
        found = inspect.Findings()
        inspect.check_summary(found, "", "32020R0001", target, "DIRECT", summary, set(flags))
        return set(found.items)

    def test_may_written_as_must_is_flagged(self):
        self.assertIn("SUM_MAY_AS_MUST", self.checks(self.summary(
            "Steuerpflichtige müssen Anmeldungen einreichen.",
            "Member States may require that taxable persons submit statements.")))
        self.assertNotIn("SUM_MAY_AS_MUST", self.checks(self.summary(
            "Steuerpflichtige können Anmeldungen einreichen.",
            "Member States may require that taxable persons submit statements.")))
        self.assertNotIn("SUM_MAY_AS_MUST", self.checks(self.summary(
            "Der Hersteller muss die Unterlagen aufbewahren.", "The manufacturer shall keep, as the case may be, the file.")))

    def test_an_authority_duty_without_the_authority_is_flagged(self):
        quote = "The competent authority shall carry out at least one field inspection per year."
        self.assertIn("SUM_AUTHORITY_UNNAMED", self.checks(self.summary(
            "Es muss mindestens eine Feldinspektion pro Jahr durchgeführt werden.", quote)))
        self.assertNotIn("SUM_AUTHORITY_UNNAMED", self.checks(self.summary(
            "Die zuständige Behörde muss mindestens eine Feldinspektion pro Jahr durchführen.", quote)))

    def test_amending_article_annex_start_and_number_marks(self):
        summary = self.summary("Hersteller müssen X melden.", "Manufacturers shall report X.")
        self.assertIn("SUM_AMENDING_AS_RULE", self.checks(summary, flags={"amending"}))
        summary["overview"] = "Die Richtlinie 2009/28/EG wird geändert."
        self.assertNotIn("SUM_AMENDING_AS_RULE", self.checks(summary, flags={"amending"}))
        annex = self.summary("x", "y", overview="Die Verordnung legt Grenzwerte fest.")
        self.assertIn("SUM_ANNEX_AS_ACT", self.checks(annex, target="Annex II"))
        annex["number_check"] = ["The number 5 does not occur"]
        self.assertIn("SUM_NUMBER_MARK", self.checks(annex, target="Annex II"))


class RunTests(unittest.TestCase):
    def test_the_whole_run_reads_every_step_and_writes_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "screening"
            batch = run / "batches" / "0001"
            (batch / "audit" / "32020R0001").mkdir(parents=True)
            files = {"Article 1": pack("1. The Commission shall publish a report by 2025.", "the Commission"),
                     "Article 2": pack("The manufacturer shall keep the documents for 10 years."),
                     "Article 3": pack("Directive 2009/28/EC is amended as follows: (1) Article 2 is replaced.")}
            results = []
            for number, (target, content) in enumerate(files.items()):
                name = f"audit/32020R0001/article_{number}.json"
                (batch / name).write_text(json.dumps(content), encoding="utf-8")
                results.append({"celex": "32020R0001", "target": target, "kind": "ARTICLE",
                                "target_id": target, "file": name, "level": "DIRECT"})
            results.append({"celex": "32020R0002", "target": "Article 1", "kind": "ARTICLE", "target_id": "x",
                            "file": "audit/32020R0001/article_0.json", "level": "IGNORE"})
            (batch / "audit" / "run_summary.json").write_text(json.dumps({"results": results}), encoding="utf-8")
            (root / "doc" / "relevance_export").mkdir(parents=True)
            (root / "doc" / "relevance_export" / "direct.json").write_text(json.dumps(
                {"32020R0002": {"level": "DIRECT", "title": "Regulation on feed additives"}}), encoding="utf-8")
            (root / "doc" / "relevance_export" / "indirect.json").write_text("{}", encoding="utf-8")
            (root / "class").mkdir()
            (root / "class" / "results.json").write_text(json.dumps({"32020R0001": {
                "Article 1": {"level": "DIRECT", "primary_domain": "no_fitting_domain", "departments": ["Einkauf"],
                              "check": ["no_fitting_domain"]}}}), encoding="utf-8")
            v1 = root / "v1" / "provisions" / "targets" / "32020R0001"
            v1.mkdir(parents=True)
            (v1 / "article_2.json").write_text(json.dumps({
                "celex": "32020R0001", "target": "Article 3", "level": "DIRECT",
                "summary": {"headline": "Meldungen", "overview": "Hersteller melden X.", "bullets": []}}),
                encoding="utf-8")
            (v1 / "article_3.json").write_text(json.dumps({"celex": "32020R0001", "target": "Article 9",
                                                           "failure": "cut off"}), encoding="utf-8")
            saved = (inspect.DEFAULT_RUNS, inspect.DEFAULT_UNSURE, inspect.NEW_ARTICLES, inspect.CLASSIFICATION,
                     inspect.SUMMARIES_V1, inspect.SUMMARIES_V2, inspect.DOCUMENT_RUN)
            try:
                inspect.DEFAULT_RUNS, inspect.DEFAULT_UNSURE = [run], root / "none"
                inspect.NEW_ARTICLES, inspect.CLASSIFICATION = root / "none", root / "class"
                inspect.SUMMARIES_V1, inspect.SUMMARIES_V2 = root / "v1", root / "none"
                inspect.DOCUMENT_RUN = root / "doc"
                self.assertEqual(inspect.main(["--output-dir", str(root / "out")]), 0)
            finally:
                (inspect.DEFAULT_RUNS, inspect.DEFAULT_UNSURE, inspect.NEW_ARTICLES, inspect.CLASSIFICATION,
                 inspect.SUMMARIES_V1, inspect.SUMMARIES_V2, inspect.DOCUMENT_RUN) = saved
            [out] = list((root / "out").iterdir())
            counts = json.loads((out / "summary.json").read_text(encoding="utf-8"))["counts"]
            self.assertEqual(counts["PROV_AUTHORITY_DIRECT"], 1)
            self.assertEqual(counts["CLASS_AUTHORITY_ROUTED"], 1)
            self.assertEqual(counts["PROV_AMENDING"], 1)
            self.assertEqual(counts["SUM_AMENDING_AS_RULE"], 1)
            self.assertEqual(counts["SUM_MISSING"], 1)
            self.assertEqual(counts["DOC_NO_KEPT_PROVISION"], 1)
            self.assertEqual(counts["DOC_FAR_AREA"], 1)
            self.assertEqual(counts["CLASS_NO_DOMAIN"], 1)
            self.assertIn("PROV_AUTHORITY_DIRECT", (out / "report.md").read_text(encoding="utf-8"))
            self.assertIn("check;celex;provision", (out / "issues.csv").read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
