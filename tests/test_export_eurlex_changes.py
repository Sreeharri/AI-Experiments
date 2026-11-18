"""Tests of tools/export_eurlex_changes.py.

The text helpers are tested without a database. The export itself runs against a throw-away PostgreSQL
schema only when LR_TEST_DATABASE_URL is set (for example a local test database); otherwise it is skipped.
The schema holds only the columns the export reads, so the test does not need the backend code.
"""

from datetime import date
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import export_eurlex_changes as export  # noqa: E402

AMENDING = (
    "Article 1 Amendments to Directive 2010/75/EU Directive 2010/75/EU is amended as follows: "
    "(1) Article 3 is amended as follows: (a) point 2 is replaced by the following: ‘(2) ‘pollution’ means the direct "
    "or indirect introduction of substances into air, water or land;’; "
    "(2) Article 15 is amended as follows: (a) paragraph 3 is replaced by the following: ‘3. Emission levels shall "
    "not exceed 85 TJ per year.’; "
    "(3) the following Article 14a is inserted: ‘Article 14a Environmental management system Operators shall "
    "prepare an environmental management system by 31 December 2027.’;"
)


class HelperTests(unittest.TestCase):
    def test_root_of_and_act_number(self):
        self.assertEqual(export.root_of("Article 15(3)(b)"), ("article", "15"))
        self.assertEqual(export.root_of("Annex II, point 2"), ("annex", "ii"))
        self.assertEqual(export.root_of("Article 14a"), ("article", "14a"))
        self.assertIsNone(export.root_of("Recital 5"))
        self.assertEqual(export.act_number("32010L0075"), "2010/75")
        self.assertEqual(export.act_number("32024R1781"), "2024/1781")

    def test_instruction_is_the_point_that_changes_the_provision(self):
        elements = [{"identifier": "Article 1", "text": AMENDING},
                    {"identifier": "Article 2", "text": "Transposition Member States shall comply by 1 July 2026."}]
        text, where = export.instruction_for(elements, "32010L0075", ("article", "15"))
        self.assertEqual(where, "Article 1")
        self.assertTrue(text.startswith("(2) Article 15 is amended"))
        self.assertNotIn("Article 3 is amended", text)
        self.assertNotIn("14a", text)
        text, _ = export.instruction_for(elements, "32010L0075", ("article", "14a"))
        self.assertIn("Environmental management system", text)
        self.assertEqual(export.instruction_for(elements, "32010L0075", ("article", "99")), ("", None))

    def test_quoted_new_text(self):
        point = "(2) Article 15 is amended as follows: (a) paragraph 3 is replaced by the following: ‘3. Emission " \
                "levels shall not exceed 85 TJ per year.’"
        self.assertEqual(export.quoted_new_text(point, "REPLACE"), "3. Emission levels shall not exceed 85 TJ per year.")
        self.assertEqual(export.quoted_new_text(point, "DELETE"), "")
        self.assertEqual(export.quoted_new_text("Article 5 is deleted.", "REPLACE"), "")

    def test_corrections_are_read_from_the_corrigendum_text(self):
        text = ("Corrigendum to Directive 2010/75/EU. On page 22, Article 3(2): for: ‘100 TJ’, read: ‘85 TJ’. "
                "Auf Seite 5, Artikel 4: anstatt: ‘Betreiber’ muss es heißen: ‘Anlagenbetreiber’.")
        found = export.corrections_in(text)
        self.assertEqual([(c["wrong"], c["correct"]) for c in found], [("100 TJ", "85 TJ"), ("Betreiber", "Anlagenbetreiber")])
        self.assertIn("On page 22, Article 3(2)", found[0]["location"])

    def test_final_articles_dates_and_roles(self):
        elements = [{"identifier": "Article 1", "heading": None, "text": AMENDING},
                    {"identifier": "Article 2", "heading": "Transposition", "text": "Member States shall bring into force ..."},
                    {"identifier": "Article 3", "heading": None, "text": "This Directive shall enter into force on the day ..."},
                    {"identifier": "Annex I", "heading": None, "text": "entry into force table"}]
        self.assertEqual([f["locator"] for f in export.final_articles(elements)], ["Article 2", "Article 3"])
        rel = {"valid_from": None, "entry_into_force": date(2024, 8, 4)}
        self.assertEqual(export.change_day({"effective_from": date(2024, 9, 1)}, rel), (date(2024, 9, 1), "CHANGE"))
        self.assertEqual(export.change_day({"raw_annotation": {"START_OF_VALIDITY": "2024-10-01"}}, rel),
                         (date(2024, 10, 1), "ANNOTATION"))
        self.assertEqual(export.change_day({}, rel), (date(2024, 8, 4), "ENTRY_INTO_FORCE"))
        self.assertEqual(export.change_day({}, {}), (None, "MISSING"))
        rels = [{"relation_type": "AMENDS", "changing_celex": "32024L1785", "changed_celex": "32010L0075"},
                {"relation_type": "REPEALS", "changing_celex": "32010L0075", "changed_celex": "32008L0001"},
                {"relation_type": "CORRECTS", "changing_celex": "32010L0075R(01)", "changed_celex": "32010L0075"},
                {"relation_type": "COMPLETES", "changing_celex": "32020R0001", "changed_celex": "32010L0075"}]
        roles = export.act_roles(rels)
        self.assertEqual(roles["32010L0075"]["labels"], ["Ändert andere Rechtsakte", "Wurde geändert", "Wurde berichtigt"])
        self.assertEqual(roles["32010L0075R(01)"]["labels"], ["Berichtigung"])
        self.assertEqual(roles["32008L0001"]["labels"], ["Wurde geändert"])
        self.assertNotIn("32020R0001", roles)                     # a link only is no change

    def test_clean_removes_revision_markers(self):
        self.assertEqual(export.clean("▼M1 Text  ▼B more"), "Text more")

    def test_corrigenda_in_english_and_german_and_whole_replacements(self):
        text = ("CORRIGENDUM to Regulation (EU) 2016/185\nOn page 79, Article 1(1):\nfor: ‘the Member State’s body’,\n"
                "read: ‘the competent body of the Member State’s choice’.\nOn page 80, Annex II is replaced by the "
                "following:\nANNEX II List 1. a\nSeite 5, Artikel 2 Absatz 1:\nAnstatt:\n„1 200 kg/m3“\n"
                "muss es heißen:\n„1,2 kg/m3“.")
        found = export.corrections_in(text)
        self.assertEqual([c["correct"] for c in found],
                         ["the competent body of the Member State’s choice", "ANNEX II List 1. a", "1,2 kg/m3"])
        self.assertEqual(found[1]["wrong"], "")
        self.assertTrue(export.is_corrigendum_text(text))
        self.assertFalse(export.is_corrigendum_text("Article 1 Subject matter. This Regulation lays down rules."))

    def test_sole_annex_and_own_article_is_not_an_instruction(self):
        self.assertEqual(export.root_of("Annex TXT"), ("annex", ""))
        self.assertEqual(export.root_of("Annex P A TXT"), ("annex", ""))
        elements = [{"identifier": "Article 51", "text": "Article 51 Hydrogen network development plans. Member "
                                                          "States shall ensure that operators submit plans."},
                    {"identifier": "Article 90", "text": "Article 90 Amendments to Regulation (EU) 2018/1999. "
                                                          "Regulation (EU) 2018/1999 is amended as follows: (1) "
                                                          "Article 2 is replaced by the following: ‘text’;"}]
        self.assertEqual(export.instruction_for(elements, "32018R1999", ("article", "51")), ("", None))
        self.assertEqual(export.instruction_for(elements, "32018R1999", ("article", "2"))[1], "Article 90")
        several = "Regulation (EU) 2019/1009 is amended as follows: (1) Article 4 is replaced. " \
                  "Directive 2008/98/EC is amended as follows: (1) Article 4 is deleted."
        self.assertNotIn("deleted", export.instruction_for([{"identifier": "Article 1", "text": several}],
                                                             "32019R1009", ("article", "4"))[0])

    def test_long_texts_keep_the_change(self):
        same = " ".join(f"Sentence number {i} stays the same." for i in range(800))
        old, new, trimmed = export.changed_regions(same + " The limit is 100 TJ.", same + " The limit is 85 TJ.")
        self.assertTrue(trimmed)
        self.assertIn("100 TJ", old)
        self.assertIn("85 TJ", new)
        self.assertLess(len(new), 2000)


    def test_eurlex_text_is_read_split_and_cached(self):
        import io
        from tempfile import TemporaryDirectory
        import eurlex_text_fetch as fetch
        page = ("<html><head><title>x</title><script>var a;</script></head><body><p class='oj-ti-art'>Article 1</p>"
                "<p>Subject matter</p><p>Directive 2010/75/EU is amended as follows: (1) Article 15 is replaced by the "
                "following: ‘text’;</p><p class='oj-ti-art'>Article 2</p><p>Entry into force</p><p>This Directive shall "
                "enter into force on the twentieth day.</p><p class='oj-doc-ti'>ANNEX</p><p>List of plants</p></body></html>")

        class Opener:
            calls = 0

            def open(self, request, timeout):
                Opener.calls += 1
                return io.BytesIO(page.encode("utf-8"))
        fetch.PAUSE_SECONDS = 0
        with TemporaryDirectory() as folder:
            text, status = fetch.fetch_text("32024L1785", "EN", Path(folder), Opener())
            again = fetch.fetch_text("32024L1785", "EN", Path(folder), Opener())
        self.assertEqual((status, again[1], Opener.calls), ("OK", "OK", 1))
        elements = fetch.split_elements(text)
        self.assertEqual([e["identifier"] for e in elements], ["Article 1", "Article 2", "Annex"])
        self.assertEqual(elements[1]["heading"], "Entry into force")
        self.assertNotIn("var a", text)
        self.assertEqual(export.final_articles(elements)[0]["locator"], "Article 2")


    def test_pilot_cases_quotes_citations_and_changing_annexes(self):
        # Article numbers inside the quoted new text are references, not the changed place (32013R0321).
        text = ("Article 1 Regulation (EU) No 321/2013 is amended as follows: (1) Article 4 is replaced as follows: "
                "‘Article 4 Not used.’; (2) Article 7 is deleted; (3) in point 1.3., the introductory phrase and point "
                "(a) are replaced by the following: ‘In accordance with Article 4(3) of Directive (EU) 2016/797, this "
                "TSI: (a) covers the ‘rolling stock-freight wagons’ subsystem;’;")
        elements = [{"identifier": "Article 1", "text": text}]
        instruction, where = export.instruction_for(elements, "32013R0321", ("article", "4"))
        self.assertTrue(instruction.startswith("(1) Article 4 is replaced"))
        self.assertNotIn("point 1.3", instruction)
        self.assertEqual(export.quoted_new_text(instruction, "REPLACE", ("article", "4")), "Article 4 Not used.")
        self.assertEqual(export.act_forms("31989L0391") >= {"1989/391", "89/391"}, True)
        # Only the quoted article of this change (32008L0098 Article 11b), not Article 1 of the same instruction.
        insert = ("Directive 2008/98/EC is amended as follows: (1) Article 1 is replaced by the following: ‘Article 1 "
                  "Subject matter’; (13) the following Articles are inserted: ‘Article 11a Rules on targets.’ "
                  "‘Article 11b Early warning report.’;")
        instruction, _ = export.instruction_for([{"identifier": "Article 1", "text": insert}], "32008L0098",
                                                ("article", "11b"))
        self.assertTrue(instruction.startswith("(13)"))
        self.assertEqual(export.quoted_new_text(instruction, "ADD", ("article", "11b")), "Article 11b Early warning report.")
        # A changing act's own article that cites the changed act is not a change of it (32018R1999 Art. 51).
        own = [{"identifier": "Article 56", "text": "Article 56 Hydrogen distribution network development plan 1. "
                "Operators shall submit plans, as reported under Article 51 of Regulation (EU) 2018/1999."}]
        self.assertEqual(export.instruction_for(own, "32018R1999", ("article", "51")), ("", None))
        # New wording in the changing act's annex (32019R0452).
        changing = [{"identifier": "Article 1", "text": "Article 1 The Annex to Regulation (EU) 2019/452 is replaced "
                                                         "by the text in the Annex to this Regulation."},
                    {"identifier": "Annex", "text": "ANNEX List of projects or programmes of Union interest."}]
        instruction, _ = export.instruction_for(changing, "32019R0452", ("annex", ""))
        self.assertEqual(export.changing_annex(instruction, changing), ("NEW", changing[1]["text"]))


SCHEMA = """
CREATE TABLE source_documents (id uuid PRIMARY KEY, external_id text, title_en text, title_de text,
  canonical_title text, document_date date, publication_date date, date_entry_into_force date,
  date_application date, information_url text, source text DEFAULT 'EUR_LEX');
CREATE TABLE legal_instruments (id uuid PRIMARY KEY, source_document_id uuid, celex text);
CREATE TABLE instrument_relationships (id uuid PRIMARY KEY, source_document_id uuid, target_document_id uuid,
  relation_type text, relation_family text, status text, valid_from date, valid_to date);
CREATE TABLE relationship_provision_changes (id uuid PRIMARY KEY, relationship_id uuid, ordinal int,
  operation text, raw_operation_code text, target_provision text, target_locator_type text,
  target_locator_number text, target_element_id uuid, resolution_status text, raw_annotation jsonb,
  effective_from date, effective_to date);
CREATE TABLE act_wording_comparisons (id uuid PRIMARY KEY, change_id uuid, language text, old_text text,
  new_text text, instruction_text text);
CREATE TABLE instrument_versions (id uuid PRIMARY KEY, instrument_id uuid, version_kind text, version_date date,
  effective_from date, is_current boolean, is_preferred boolean, language text, version_key text, source_url text,
  content_sha256 text);
CREATE TABLE legal_elements (id uuid PRIMARY KEY, version_id uuid, element_type text, identifier text,
  heading text, body_text text, path text, depth int, ordinal int);
"""


@unittest.skipUnless(os.environ.get("LR_TEST_DATABASE_URL") and os.environ.get("LR_BACKEND_DIR"),
                     "set LR_TEST_DATABASE_URL and LR_BACKEND_DIR to run the database test")
class ExportTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        self.schema = f"test_changes_{uuid.uuid4().hex[:8]}"
        self.url = os.environ["LR_TEST_DATABASE_URL"]
        self.connection = psycopg.connect(self.url, autocommit=True)
        self.connection.execute(f"CREATE SCHEMA {self.schema}")
        self.connection.execute(f"SET search_path TO {self.schema}")
        self.connection.execute(SCHEMA)
        self.ids = {}
        self.seed()
        self.directory = TemporaryDirectory()

    def tearDown(self):
        self.connection.execute(f"DROP SCHEMA {self.schema} CASCADE")
        self.connection.close()
        self.directory.cleanup()

    def act(self, celex, title, day, versions=()):
        doc, instrument = uuid.uuid4(), uuid.uuid4()
        self.connection.execute("INSERT INTO source_documents VALUES (%s,%s,%s,NULL,NULL,%s,%s,%s,NULL,%s,'EUR_LEX')",
                                (doc, celex, title, day, day, day, f"https://eur-lex/{celex}"))
        self.connection.execute("INSERT INTO legal_instruments VALUES (%s,%s,%s)", (instrument, doc, celex))
        for kind, version_day, elements in versions:
            version = uuid.uuid4()
            self.connection.execute("INSERT INTO instrument_versions VALUES (%s,%s,%s,%s,NULL,%s,%s,'EN',%s,'u',%s)",
                                    (version, instrument, kind, version_day, kind == "CONSOLIDATED", True,
                                     f"{'ORI' if kind == 'ORIGINAL' else 'CON'}:{celex}:{version_day}", f"sha-{celex}"))
            for ordinal, (identifier, heading, body) in enumerate(elements):
                self.connection.execute("INSERT INTO legal_elements VALUES (%s,%s,'ARTICLE',%s,%s,%s,%s,1,%s)",
                                        (uuid.uuid4(), version, identifier, heading, body, f"p{ordinal}", ordinal))
        self.ids[celex] = doc
        return doc

    def relation(self, child, base, kind, changes=()):
        rel = uuid.uuid4()
        self.connection.execute("INSERT INTO instrument_relationships VALUES (%s,%s,%s,%s,'OPERATIONAL','ACTIVE',NULL,NULL)",
                                (rel, self.ids[child], self.ids[base], kind))
        for ordinal, (operation, target, day) in enumerate(changes, 1):
            self.connection.execute(
                "INSERT INTO relationship_provision_changes VALUES (%s,%s,%s,%s,%s,%s,NULL,NULL,NULL,'RESOLVED',%s,%s,NULL)",
                (uuid.uuid4(), rel, ordinal, operation, operation, target, json.dumps({}), day))
        return rel

    def seed(self):
        self.act("32010L0075", "Industrial Emissions Directive", date(2010, 11, 24), [
            ("ORIGINAL", date(2010, 12, 17), [("Article 3", None, "Definitions (2) pollution means emissions."),
                                              ("Article 15", None, "Emission limit values. 3. Levels shall not exceed 100 TJ per year.")]),
            ("CONSOLIDATED", date(2024, 8, 4), [("Article 3", None, "▼M1 Definitions (2) pollution means the direct or indirect introduction of substances into air, water or land."),
                                                ("Article 14a", None, "Environmental management system Operators shall prepare an environmental management system by 31 December 2027."),
                                                ("Article 15", None, "Emission limit values. 3. Emission levels shall not exceed 85 TJ per year.")])])
        self.act("32024L1785", "Directive (EU) 2024/1785 amending Directive 2010/75/EU", date(2024, 4, 24), [
            ("ORIGINAL", date(2024, 4, 24), [("Article 1", "Amendments to Directive 2010/75/EU", AMENDING),
                                             ("Article 2", "Transposition", "Member States shall bring into force the laws by 1 July 2026."),
                                             ("Article 3", "Entry into force", "This Directive shall enter into force on the twentieth day.")])])
        self.act("32010L0075R(01)", "Corrigendum to Directive 2010/75/EU", date(2012, 5, 1), [
            ("ORIGINAL", date(2012, 5, 1), [("Article 1", None, "On page 22, Article 3(2): for: ‘100 TJ’, read: ‘85 TJ’.")])])
        self.act("32008L0001", "IPPC Directive", date(2008, 1, 15))
        self.act("31999X9999", "Old act", date(1999, 1, 1))
        self.act("32020R0001", "Unrelated act", date(2020, 1, 1))
        self.relation("32024L1785", "32010L0075", "AMENDS", [("REPLACE", "Article 15 paragraph 3", date(2024, 8, 4)),
                                                             ("INSERT", "Article 14a", date(2024, 8, 4)),
                                                             ("REPLACE", "Article 3 point 2", date(2024, 8, 4)),
                                                             ("REPLACE", "Article 99", date(2024, 8, 4))])
        self.relation("32010L0075R(01)", "32010L0075", "CORRECTS")
        self.relation("32010L0075", "32008L0001", "REPEALS")
        self.relation("31999X9999", "32010L0075", "AMENDS")        # too old for --since 2015 ... but 2010 base relevant
        self.relation("32020R0001", "32008L0001", "AMENDS")        # neither act relevant
        self.relation("32024L1785", "32008L0001", "COMPLETES")      # link only

    def run_export(self, *options):
        out = Path(self.directory.name) / "out"
        cls = Path(self.directory.name) / "cls"
        cls.mkdir(exist_ok=True)
        (cls / "results.json").write_text(json.dumps({"32010L0075": {}, "32024L1785": {}}), encoding="utf-8")
        os.environ["DATABASE_URL"] = f"{self.url}{'&' if '?' in self.url else '?'}options=-csearch_path%3D{self.schema}"
        try:
            code = export.main(["--env", str(Path(self.directory.name) / "none"), "--classification", str(cls),
                                "--out", str(out), "--backend-dir", os.environ["LR_BACKEND_DIR"], *options])
        finally:
            os.environ.pop("DATABASE_URL")
        records = [json.loads(line) for line in (out / "records.jsonl").read_text(encoding="utf-8").splitlines()] \
            if (out / "records.jsonl").exists() else []
        return code, records, out

    def test_export_builds_change_records_with_old_new_and_dates(self):
        code, records, out = self.run_export("--since", "2000")
        self.assertEqual(code, 0)
        kinds = sorted((r["kind"], r["relation"], r.get("provision")) for r in records)
        self.assertEqual(kinds, [("ACT", "REPEALS", None), ("CORRIGENDUM", "CORRECTS", None),
                                 ("PROVISION", "AMENDS", "Article 14a"), ("PROVISION", "AMENDS", "Article 15 paragraph 3"),
                                 ("PROVISION", "AMENDS", "Article 3 point 2"), ("PROVISION", "AMENDS", "Article 99")])
        by = {r.get("provision"): r for r in records}
        art15 = by["Article 15 paragraph 3"]
        self.assertEqual(art15["wording"], "FULL")
        self.assertIn("100 TJ", art15["old_text"])
        self.assertIn("85 TJ", art15["new_text"])
        self.assertTrue(art15["instruction"].startswith("(2) Article 15 is amended"))
        self.assertEqual(art15["effective_from"], "2024-08-04")
        self.assertEqual((art15["comparison_status"], art15["effective_source"]), ("MATCHED_STORED_EVIDENCE", "CHANGE"))
        self.assertEqual(art15["old_text"], "3. Levels shall not exceed 100 TJ per year.")   # the exact paragraph
        self.assertEqual(by["Article 99"]["comparison_status"], "NEEDS_MATCH_REVIEW")
        roles = json.loads((out / "roles.json").read_text(encoding="utf-8"))
        self.assertEqual(roles["32010L0075"]["labels"], ["Ändert andere Rechtsakte", "Wurde geändert", "Wurde berichtigt"])
        self.assertIn("eurlex_wording.py", json.loads((out / "export_summary.json").read_text(encoding="utf-8"))
                      ["backend_wording"]["file"])
        self.assertEqual([f["locator"] for f in art15["final_articles"]], ["Article 2", "Article 3"])
        self.assertEqual(art15["changing"]["celex"], "32024L1785")
        self.assertEqual((by["Article 14a"]["wording"], by["Article 14a"]["old_text"]), ("NEW_ONLY", None))
        self.assertNotIn("▼M1", by["Article 3 point 2"]["new_text"])
        self.assertEqual(by["Article 99"]["wording"], "INSTRUCTION_ONLY" if by["Article 99"]["instruction"] else "NONE")
        corrigendum = next(r for r in records if r["kind"] == "CORRIGENDUM")
        self.assertEqual(corrigendum["corrections"][0]["correct"], "85 TJ")
        summary = json.loads((out / "export_summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["records"], summary["links_only"]), (6, 1))
        self.assertEqual(summary["gaps"]["wording_FULL"], 2)

    def test_since_and_celex_filters_and_existing_folder(self):
        code, records, _ = self.run_export("--since", "2015")
        self.assertEqual(code, 0)
        self.assertNotIn("CORRIGENDUM", {r["kind"] for r in records})          # 2012 corrigendum is older
        self.assertNotIn("31999X9999", {r["changing"]["celex"] for r in records})
        with self.assertRaises(SystemExit):
            self.run_export("--since", "2015")                                  # folder already holds an export


if __name__ == "__main__":
    unittest.main()
