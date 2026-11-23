import csv
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lr_ai.qwen_client import ChatResult  # noqa: E402
import summarize_changed_provisions as changed  # noqa: E402


def client_with(*replies):
    """A stand-in for the Qwen client: answers with the given replies in order."""
    client = Mock()
    client.clean = lambda text: text
    queue = list(replies)

    def chat(messages, **options):
        if not queue:
            raise AssertionError("More requests than expected.")
        return queue.pop(0)

    client.chat.side_effect = chat
    client.discover.return_value = {"ok": True}
    return client


def reply(value):
    return ChatResult(ok=True, finish_reason="stop", content=json.dumps(value))


def provision(headline="Energy audits"):
    return reply({"headline": headline, "overview": "Large energy users must have their energy use audited.",
                  "bullets": [{"kind": "DUTY", "text": "Enterprises above 85 TJ must carry out an energy audit.",
                               "passages": ["P1"]}]})

CELEX = "32018R0858"
ARTICLE_6_TODAY = ("Article 6 Durability 1. Manufacturers shall ensure durability. 5. Limits are adjusted by the "
                   "multipliers in Table 2 of Annex IV for the additional lifetime. 6. Manufacturers shall report "
                   "the results to the approval authority every 12 months.")


def line(role, day, where, place, operation="REPLACE", old=None, new=None, by="32024R1257", **more):
    item = {"role": role, "date": day, "operation": operation, "code": "R", "relation": "AMENDS", "where": where,
            "place": place, "changing_act": by,
            "changing_act_link": f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:{by}",
            "status": "MATCHED_VERSION_COMPARISON", "complete": True, "note": "", **more}
    if old:
        item["old_text"] = old
    if new:
        item["new_text"] = new
    return item


def today(text, kind="CONSOLIDATED", day="2026-09-23"):
    return {"text": text, "heading": "Durability", "name": "Article 6", "exact": True, "version_date": day,
            "version_kind": kind, "link": f"https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:0{CELEX[1:]}"}


def export_folder(folder):
    roots = {
        "article/6": {"kind": "article", "number": "6", "name": "Article 6", "today": today(ARTICLE_6_TODAY),
                      "deleted": None, "changes": [
                          line("upcoming", "2028-01-01", "Article 6 paragraph 7", "Article 6 paragraph 7", "ADD",
                               new="7. From 2028 the report is published.", by="32026R0001"),
                          line("latest", "2024-07-07", "Article 6 paragraph 6", "Article 6 paragraph 6", "ADD",
                               new="6. Manufacturers shall report the results to the approval authority every 12 "
                                   "months."),
                          line("latest", "2024-07-07", "Article 6 paragraph 5", "Article 6 paragraph 5",
                               old="5. Limits are adjusted by the multipliers in Table 2 of Annex IV.",
                               new="5. Limits are adjusted by the multipliers in Table 2 of Annex IV for the "
                                   "additional lifetime."),
                          line("history", "2019-03-01", "Article 6 paragraph 1", "Article 6 paragraph 1",
                               by="32019R0001")]},
        "article/7": {"kind": "article", "number": "7", "name": "Article 7",
                      "today": today("Article 7 Penalties Member States shall lay down penalties.", "ORIGINAL", None),
                      "deleted": None, "changes": [line("history", "2012-01-01", "Article 7", "Article 7",
                                                         by="32012R0001")]},
        "article/9": {"kind": "article", "number": "9", "name": "Article 9", "today": None,
                      "deleted": {"date": "2024-07-07", "by": "32024R1257"},
                      "changes": [line("latest", "2024-07-07", "Article 9", "Article 9", "DELETE")]},
        "annex/i": {"kind": "annex", "number": "i", "name": "Annex I", "today": today("ANNEX I List of acts"),
                    "deleted": None, "changes": [
                        line("latest", "2025-01-01", "Annex I point 3", "Annex I point 3", "ADD",
                             new="3. Regulation C")]},
    }
    (folder / "acts").mkdir(parents=True)
    (folder / "acts" / f"{CELEX}.json").write_text(json.dumps({
        "celex": CELEX, "level": "DIRECT", "title": "Regulation (EU) 2018/858 on type-approval",
        "today_version": "version 2026-09-23", "today_version_celex": "02018R0858-20260923", "roots": roots,
        "act_changes": []}), encoding="utf-8")
    (folder / "index.json").write_text(json.dumps({CELEX: {"level": "DIRECT", "roots": 4}}), encoding="utf-8")


CLASSIFIED = {CELEX: {
    "Article 6": {"level": "DIRECT", "departments": ["Technik"], "primary_domain": "vehicles"},
    "Article 6 [source path article/6~duplicate-2]": {"level": "DIRECT"},
    "Article 7": {"level": "INDIRECT"},
    "Article 8": {"level": "DIRECT"},
    "Article 9": {"level": "INDIRECT"},
    "Annex 1, ANNEX I [section 2], unnumbered table 1": {"level": "DIRECT"},
    "Article 10": {"level": "IGNORE"},
}}


def sentence(text="Neu ist die Pflicht der Hersteller, die Ergebnisse alle 12 Monate zu melden."):
    return reply({"sentence": text})


class WordsTests(unittest.TestCase):
    def test_places_acts_and_lines_in_german(self):
        self.assertEqual(changed.german_place("Article 6 paragraph 5 point b"), "Artikel 6 Absatz 5 Buchstabe b")
        self.assertEqual(changed.german_place("Annex VII appendix 2 part 1 point 1.3"),
                         "Anhang VII Anlage 2 Teil 1 Nummer 1.3")
        self.assertEqual(changed.act_name("32024R1257"), "Verordnung 2024/1257")
        self.assertEqual(changed.act_name("32011R0540"), "Verordnung Nr. 540/2011")
        self.assertEqual(changed.act_name("32008L0098"), "Richtlinie 2008/98")
        self.assertEqual(changed.fact_line(line("latest", "2024-07-07", "x", "Article 6 paragraph 3")),
                         "07.07.2024 – Artikel 6 Absatz 3 ersetzt durch Verordnung 2024/1257")
        self.assertEqual(changed.upcoming_line(line("upcoming", "2027-01-01", "x", "Article 6 paragraph 3 point b",
                                                    by="32026R0001")),
                         "Ab 01.01.2027: Artikel 6 Absatz 3 Buchstabe b wird ersetzt (Verordnung 2026/1)")
        suspended = line("latest", "2024-07-07", "x", "Article 6", "OTHER", relation="SUSPENDS",
                         status="NO_WORDING_NEEDED")
        self.assertIn("Artikel 6 ausgesetzt", changed.fact_line(suspended))

    def test_relevant_provisions_are_matched_to_the_changed_article_or_annex(self):
        roots = {"annex/i": {}, "article/6": {}}
        self.assertEqual(changed.find_root(roots, "annex", "1"), "annex/i")       # Arabic and Roman
        self.assertEqual(changed.find_root(roots, "annex", "sole"), "annex/i")    # the only annex
        self.assertIsNone(changed.find_root(roots, "article", "7"))
        self.assertIsNone(changed.root_of_locator("Annex B-03 Chapter I"))


class MergeTests(unittest.TestCase):
    def test_articles_inserted_later_are_added_from_their_own_classification(self):
        with TemporaryDirectory() as tmp:
            first, extra, missing = Path(tmp) / "v5", Path(tmp) / "new", Path(tmp) / "none"
            first.mkdir()
            extra.mkdir()
            (first / "results.json").write_text(json.dumps({CELEX: {"Article 6": {"level": "DIRECT"}}}))
            (extra / "results.json").write_text(json.dumps(
                {CELEX: {"Article 6": {"level": "IGNORE"}, "Article 15a": {"level": "INDIRECT"}}}))
            result = changed.merged([first, extra, missing])
        self.assertEqual(result, {CELEX: {"Article 6": {"level": "DIRECT"}, "Article 15a": {"level": "INDIRECT"}}})


class PlanTests(unittest.TestCase):
    def test_one_unit_per_changed_article_with_what_is_done_for_it(self):
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            export_folder(folder)
            units, facts = changed.plan(folder, CLASSIFIED, {"DIRECT", "INDIRECT"})
        kinds = {unit["key"]: changed.unit_kind(unit) for unit in units}
        self.assertEqual(kinds, {"annex/i": "tables", "article/6": "summary", "article/7": "same_text",
                                 "article/9": "deleted"})
        article_6 = next(unit for unit in units if unit["key"] == "article/6")
        self.assertEqual(len(article_6["provisions"]), 2)          # the duplicate copy too
        self.assertEqual(facts["relevant provisions not changed"], 1)  # Article 8


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        export_folder(self.folder)
        self.units = {unit["key"]: unit for unit in changed.plan(self.folder, CLASSIFIED, {"DIRECT", "INDIRECT"})[0]}
        self.systems = {"provision": "P", "merge": "M", "change": "C"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_changed_article_gets_version_2_what_changed_and_upcoming(self):
        client = client_with(provision(headline="Haltbarkeit und Meldepflicht"), sentence(),
                             sentence("Künftig wird der Bericht veröffentlicht."))
        record = changed.summarise_unit(client, self.systems, self.units["article/6"])
        self.assertEqual(record["summary"]["headline"], "Haltbarkeit und Meldepflicht")
        self.assertEqual(record["what_changed"]["facts"], [
            "07.07.2024 – Artikel 6 Absatz 6 eingefügt durch Verordnung 2024/1257",
            "07.07.2024 – Artikel 6 Absatz 5 ersetzt durch Verordnung 2024/1257"])
        self.assertTrue(record["what_changed"]["sentence"].startswith("Neu ist die Pflicht"))
        self.assertNotIn("number_check", record["what_changed"])   # 12 is in the new wording
        self.assertEqual(record["upcoming"], ["Ab 01.01.2028: Artikel 6 Absatz 7 wird eingefügt (Verordnung 2026/1)"])
        self.assertEqual(record["upcoming_explained"], [{
            "date": "2028-01-01", "line": "Ab 01.01.2028: Artikel 6 Absatz 7 wird eingefügt (Verordnung 2026/1)",
            "sentence": "Künftig wird der Bericht veröffentlicht."}])
        coming = json.loads(client.chat.call_args_list[2].args[0][1]["content"].split("\n\nTASK")[0])
        self.assertEqual(coming["applies_from"], "2028-01-01")
        self.assertEqual(coming["changes"][0]["new_wording"], "7. From 2028 the report is published.")
        self.assertEqual([card["role"] for card in record["changes"]["history"]], ["history"])
        self.assertNotIn("old_text", record["changes"]["history"][0])
        sent = json.loads(client.chat.call_args_list[1].args[0][1]["content"].split("\n\nTASK")[0])
        self.assertEqual([item["type"] for item in sent["changes"]], ["added", "replaced"])
        self.assertNotIn("old_wording", sent["changes"][0])

    def test_a_number_not_in_the_wording_is_marked(self):
        client = client_with(provision(), sentence("Die Frist beträgt nun 24 Monate."), sentence())
        record = changed.summarise_unit(client, self.systems, self.units["article/6"])
        self.assertTrue(record["what_changed"]["number_check"])

    def test_deleted_same_text_and_tables_ask_no_summary(self):
        client = client_with(sentence("Neu aufgenommen wurde Verordnung C."))
        deleted = changed.summarise_unit(client, self.systems, self.units["article/9"])
        self.assertEqual(deleted["what_changed"]["facts"], ["Gestrichen am 07.07.2024 durch Verordnung 2024/1257"])
        self.assertNotIn("summary", deleted)
        same = changed.summarise_unit(client, self.systems, self.units["article/7"])
        self.assertNotIn("summary", same)
        self.assertEqual(same["what_changed"]["facts"], ["Zuletzt geändert am 01.01.2012 durch Verordnung Nr. 1/2012"])
        tables = changed.summarise_unit(client, self.systems, self.units["annex/i"])   # the one request
        self.assertNotIn("summary", tables)
        self.assertEqual(tables["what_changed"]["sentence"], "Neu aufgenommen wurde Verordnung C.")

    def test_a_long_text_is_read_first_where_the_change_is(self):
        unit = self.units["article/6"]
        filler = " ".join(f"Sentence {n} about something else entirely, repeated to make the text long." for n in range(900))
        node = {"locator": "Article 6", "heading": "", "text": filler + " " + ARTICLE_6_TODAY}
        saved = changed.saved_pack(unit, node)
        decisions = saved["model_attempt"]["parts"]
        self.assertGreater(len(decisions), 3)
        kept = [d["part"] for d in decisions if d["decision"]["level"] == "DIRECT"]
        self.assertEqual(len(kept), 3)
        self.assertIn(decisions[-1]["part"], kept)   # the last part holds the changed paragraphs


class RunTests(unittest.TestCase):
    def test_the_run_writes_records_review_and_preview_and_continues(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            export_folder(tmp / "changed")
            (tmp / "classification").mkdir()
            (tmp / "classification" / "results.json").write_text(json.dumps(CLASSIFIED), encoding="utf-8")
            client = client_with(sentence("Neu aufgenommen wurde Verordnung C."), provision(), sentence(),
                                 sentence("Künftig wird der Bericht veröffentlicht."))
            args = ["--changed-dir", str(tmp / "changed"), "--classification-dir", str(tmp / "classification"),
                    "--output-dir", str(tmp / "out"), "--workers", "1"]
            with patch.object(changed.QwenClient, "from_env", return_value=client):
                self.assertEqual(changed.main(args), 0)
                self.assertEqual(changed.main(args), 0)   # nothing asked again
            out = tmp / "out"
            records = json.loads((out / "changed_provisions.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(r["kind"] for r in records), ["deleted", "same_text", "summary", "tables"])
            with (out / "review.csv").open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle, delimiter=";"))
            self.assertEqual(len(rows), 4)
            page = (out / "preview" / f"{CELEX}.html").read_text(encoding="utf-8")
            self.assertIn("Was hat sich geändert", page)
            self.assertIn("Ab 01.01.2028", page)
            self.assertIn("Künftig wird der Bericht veröffentlicht.", page)
            self.assertIn("Frühere Änderungen (1)", page)
            self.assertEqual(json.loads((out / "summary.json").read_text())["summary"], 1)


if __name__ == "__main__":
    unittest.main()
