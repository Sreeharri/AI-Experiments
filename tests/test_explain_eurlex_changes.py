"""Tests of explain_eurlex_changes.py (v2.0 / v2.1, simple explanations) with a stand-in Qwen client."""

import csv
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from lr_ai.qwen_client import ChatResult

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import explain_eurlex_changes as explain  # noqa: E402

CHANGING = {"celex": "32024L1785", "title": "Directive (EU) 2024/1785 amending Directive 2010/75/EU",
            "date": "2024-04-24", "entry_into_force": "2024-08-04", "application": None, "url": None}
CHANGED = {"celex": "32010L0075", "title": "Industrial Emissions Directive", "url": None}
FINALS = [{"locator": "Article 2", "text": "Member States shall bring into force the laws necessary to comply with "
                                           "this Directive by 1 July 2026."}]


def provision(change, root, operation, old, new, instruction, wording, ordinal=1, place=None, **extra):
    return {"kind": "PROVISION", "id": f"p{change}", "change_id": change, "relation": "AMENDS",
            "changing": CHANGING, "changed": CHANGED, "final_articles": FINALS, "ordinal": ordinal,
            "operation": operation, "provision": place or f"Article {root}", "root": ["article", root.lower()],
            "changing_document_id": "doc-1785", "changed_document_id": "doc-ied", "relationship_id": "rel-1",
            "effective_from": "2024-08-04", "effective_source": "CHANGE", "old_text": old, "new_text": new,
            "instruction": instruction, "instruction_locator": "Article 1", "wording": wording, **extra}


RECORDS = [
    provision("1", "15", "REPLACE", "Emission limit values. 3. Levels shall not exceed 100 TJ per year.",
              "Emission limit values. 3. Emission levels shall not exceed 85 TJ per year.",
              "(2) Article 15 is amended as follows: paragraph 3 is replaced by the following.", "FULL"),
    provision("2", "15", "REPLACE", None, None, "(2) Article 15 paragraph 4 is replaced: Reports shall be published.",
              "INSTRUCTION_ONLY", 2),
    provision("3", "14a", "INSERT", None, "Operators shall prepare an environmental management system by 31 December 2027.",
              "(3) the following Article 14a is inserted.", "NEW_ONLY", 3),
    provision("4", "3", "REPLACE", "(2) pollution means emissions.", "(2) pollution means the introduction of substances.",
              "(1) Article 3 point 2 is replaced.", "FULL", 4),
    {"kind": "CORRIGENDUM", "id": "c1", "relation": "CORRECTS",
     "changing": {**CHANGING, "celex": "32010L0075R(01)", "title": "Corrigendum"}, "changed": CHANGED,
     "final_articles": [], "corrections": [{"location": "Article 3", "wrong": "100 TJ", "correct": "85 TJ"}],
     "instruction": ""},
    {"kind": "ACT", "id": "a1", "relation": "REPEALS", "changing": {**CHANGING, "celex": "32010L0075", "title": "IED"},
     "changed": {"celex": "32008L0001", "title": "IPPC Directive", "url": None}, "final_articles": [],
     "operation": "REPEALS", "instruction": "", "instruction_locator": None, "wording": "NONE"},
]
RESULTS = {
    "32010L0075": {"Article 15": {"level": "DIRECT", "primary_domain": "environmental_protection",
                                  "primary_title": "Umweltschutz", "departments": ["Umweltmanagement"],
                                  "secondary_departments": [], "primary_reason": "Plants of the company are covered.",
                                  "subject": "Emission limit values"},
                   "Article 3": {"level": "INDIRECT", "primary_domain": "environmental_protection",
                                 "primary_title": "Umweltschutz", "departments": ["Umweltmanagement"],
                                 "secondary_departments": ["Energiesysteme"]}},
    "32024L1785": {"Article 1": {"level": "INDIRECT", "primary_domain": "environmental_protection",
                                 "primary_title": "Umweltschutz", "departments": ["Recht"], "secondary_departments": []}},
}

# Directive 2004/37 Article 14 changed by Directive (EU) 2017/2398: paragraph 1 old and new, paragraph 8 new only.
CMD = {"celex": "32017L2398", "title": "Directive (EU) 2017/2398 amending Directive 2004/37/EC", "date": "2017-12-12",
       "entry_into_force": "2018-01-16", "application": None, "url": None}
P1_OLD = ("1. The Member States shall establish arrangements for carrying out relevant health surveillance of "
          "workers for whom the results of the assessment reveal a risk to health or safety.")
P1_NEW = P1_OLD + (" The doctor or authority responsible for the health surveillance of workers may indicate that "
                   "health surveillance must continue after the end of exposure for as long as they consider it "
                   "necessary.")
P8_NEW = ("8. All cases of cancer identified as resulting from occupational exposure to a carcinogen or mutagen "
          "shall be notified to the competent authority.")


def article_14(paragraph, old, new, status):
    return {**provision(f"14-{paragraph}", "14", "REPLACE", old, new, "", "FULL" if old else "NEW_ONLY",
                        ordinal=paragraph, place=f"Article 14 paragraph {paragraph}", comparison_status=status,
                        effective_from="2018-01-16", effective_source="ANNOTATION"),
            "changing": CMD, "changed": {"celex": "32004L0037", "title": "Directive 2004/37/EC", "url": None},
            "final_articles": [{"locator": "Article 3", "text": "Member States shall bring into force the laws "
                                                                 "necessary to comply with this Directive by 17 January 2020."}]}


REPEAL = {"kind": "ACT", "id": "r1", "relation": "REPEALS",
          "changing": {**CHANGING, "celex": "32024L2881", "title": "Directive (EU) 2024/2881 on ambient air quality",
                       "date": "2024-10-23", "entry_into_force": "2024-12-10"},
          "changed": {"celex": "32004L0107", "title": "Directive 2004/107/EC", "url": None}, "final_articles": [],
          "operation": "REPEALS", "instruction": "Directive 2004/107/EC is repealed with effect from 12 December 2026.",
          "instruction_locator": "Article 31", "wording": "INSTRUCTION_ONLY"}


def reply(value):
    return ChatResult(ok=True, finish_reason="stop", content=json.dumps(value))


def answer_for(messages):
    """A simple answer in the v2.0 format, built from the request."""
    user = messages[1]["content"]
    if "Write the paragraph" in user:
        return {"sentences": ["Die Richtlinie ändert die Industrieemissions-Richtlinie.",
                              "Die Änderungen werden in den jeweiligen Artikeln erläutert."]}
    sent = json.loads(user.partition("\n\nTASK:")[0])
    if "corrections" in sent:
        return {"explanation": "Statt 100 TJ muss es 85 TJ heißen."}
    if "old_text" in sent and "new_text" in sent:
        return {"explanation": f"In {sent['place']} wird der Wortlaut geändert."}
    return {"explanation": f"{sent['place']} regelt nun den neuen Inhalt."}


class Client:
    """Stand-in client: refuses requests before discover(), answers from answer_for, can inject replies."""

    def __init__(self, injected=None, answer=answer_for):
        self.ready, self.calls, self.injected, self.answer = False, [], list(injected or []), answer
        self.clean = lambda text: text

    def discover(self):
        self.ready = True
        return {"ok": True}

    def chat(self, messages, **options):
        if not self.ready:
            raise RuntimeError("Call discover() first")
        self.calls.append((messages, options))
        if self.injected:
            return self.injected.pop(0)
        return reply(self.answer(messages))


def ready_client(**options):
    client = Client(**options)
    client.discover()
    return client


SYSTEMS = {"change": "system", "corrigendum": "system"}


def sent_of(client):
    return [json.loads(m[1]["content"].partition("\n\nTASK:")[0]) for m, _ in client.calls]


class SimpleTests(unittest.TestCase):
    def test_every_case_gets_the_right_texts(self):
        cases = {
            "both": (provision("1", "15", "REPLACE", "Levels 100 TJ.", "Levels 85 TJ.", "(2) Article 15 is replaced.",
                               "FULL"), "CHANGED", {"old_text", "new_text"}, []),
            "new only": (article_14(8, None, P8_NEW, "PREVIOUS_WORDING_UNRESOLVED"), "CHANGED", {"new_text"},
                         ["OLD_MISSING"]),
            "instruction only": (RECORDS[1], "CHANGED", {"instruction"}, ["NEW_MISSING"]),
            "added": (RECORDS[2], "ADDED", {"new_text"}, []),
            "act": (REPEAL | {"relation": "DEROGATES", "operation": "DEROGATES"}, "ACT", {"instruction"}, []),
            "corrigendum": (RECORDS[4], "CORRECTION", {"corrections"}, []),
        }
        for name, (record, case, keys, remarks) in cases.items():
            with self.subTest(name):
                item = explain.build_items([record])[0]
                self.assertEqual(explain.change_case(item["kind"], record, record["relation"]), case)
                sent, found = explain.simple_request(item, record, case)
                self.assertTrue(keys <= set(sent), (name, set(sent)))
                self.assertEqual(found, remarks)
        nothing = provision("9", "16", "REPLACE", None, None, "", "NONE")
        item = explain.build_items([nothing])[0]
        self.assertEqual(explain.simple_request(item, nothing, "CHANGED"), (None, ["NO_TEXT"]))
        self.assertEqual(explain.change_case("PROVISION", provision("8", "16", "DELETE", "x", None, "", "OLD_ONLY"),
                                             "AMENDS"), "REMOVED")
        self.assertEqual(explain.change_case("ACT", REPEAL, "REPEALS"), "REMOVED")

    def test_old_and_new_text_go_to_qwen_and_the_answer_is_kept_as_it_is(self):
        record = article_14(1, P1_OLD, P1_NEW, "MATCHED_STORED_EVIDENCE")
        item = explain.build_items([record])[0]
        client = ready_client(answer=lambda m: {"explanation": "Neu: Die Überwachung kann nach 99 Tagen weiterlaufen."})
        name, explanation = explain.explain(client, SYSTEMS, item, [])
        sent = sent_of(client)[0]
        self.assertEqual((sent["old_text"], sent["new_text"]), (P1_OLD, P1_NEW))
        line = explanation["changes"][0]
        self.assertEqual(line["explanation"], "Neu: Die Überwachung kann nach 99 Tagen weiterlaufen.")  # not checked
        self.assertEqual((line["effective_from"], line["effective_source"]), ("2018-01-16", "ANNOTATION"))
        self.assertEqual(explanation["dates"], [{"what": "gilt ab", "date": "2018-01-16", "source": "DATABASE"}])
        self.assertEqual(explanation["outcome"], "EXPLAINED")

    def test_deletions_and_repeals_are_not_sent_to_qwen(self):
        deleted = provision("8", "16", "DELETE", "Article 16 Reports.", None, "(5) Article 16 is deleted.", "OLD_ONLY")
        client = ready_client()
        for record in (deleted, REPEAL):
            item = explain.build_items([record])[0]
            name, explanation = explain.explain(client, SYSTEMS, item, [])
            self.assertIsNone(explanation["changes"][0]["explanation"])
            self.assertEqual(explanation["outcome"], "NO_EXPLANATION_NEEDED")
        self.assertEqual(client.calls, [])
        self.assertEqual(explain.explain(client, SYSTEMS, explain.build_items([deleted])[0], [])[1]["changes"][0]
                         ["remark_texts"], ["Gestrichen."])

    def test_v21_deleted_in_the_amending_act_is_not_sent_even_when_the_database_says_other(self):
        mixed = ("(5) paragraph 1 of Article 32 is amended as follows: (a) point (c) is replaced by the following: "
                 "‘(c) the drawing up of guidelines;’; (b) points (d) and (e) are deleted; (c) points (f) and (g) are "
                 "replaced by the following: ‘(f) preliminary work; (g) technical assistance;’;")
        self.assertTrue(explain.deletes_place("(3) in Article 2, points 1, 2, 14 and 19 are deleted;", "Article 2 point 19"))
        self.assertTrue(explain.deletes_place("(4) Chapter III, containing Articles 15 to 29, is deleted;",
                                              "Chapter III Article 20"))
        self.assertTrue(explain.deletes_place(mixed, "Article 32 paragraph 1 point (d)"))
        self.assertTrue(explain.deletes_place(mixed, "Article 32 paragraph 1 point (e)"))
        self.assertFalse(explain.deletes_place(mixed, "Article 32 paragraph 1 point (c)"))
        self.assertFalse(explain.deletes_place(mixed, "Article 32 paragraph 1 point (f)"))
        self.assertFalse(explain.deletes_place("(1) the title is replaced by the following: ‘Points are deleted’",
                                               "Title"))
        record = provision("5", "2", "OTHER", None, None, "(3) in Article 2, points 1, 2 and 19 are deleted;",
                           "INSTRUCTION_ONLY", place="Article 2 point 19")
        client = ready_client()
        line = explain.explain(client, SYSTEMS, explain.build_items([record])[0], [])[1]["changes"][0]
        self.assertEqual((line["case"], line["operation"], line["remark_texts"]), ("REMOVED", "DELETE", ["Gestrichen."]))
        self.assertEqual(client.calls, [])
        added = provision("6", "2", "ADD", None, "x", "(3) point 20 is added and point 19 is deleted;", "NEW_ONLY")
        self.assertEqual(explain.change_case("PROVISION", added, "AMENDS"), "ADDED")   # additions stay additions

    def test_v23_one_point_of_a_mixed_instruction_gets_only_its_own_words(self):
        mixed = ("(5) paragraph 1 of Article 32 is amended as follows: (a) point (c) is replaced by the following: "
                 "‘(c) the drawing up of guidelines;’; (b) points (d) and (e) are deleted; (c) points (f) and (g) are "
                 "replaced by the following: ‘(f) preliminary work, such as studies; (g) technical assistance.’")
        records = {x: provision(x, "32", "REPLACE", None, None, mixed, "INSTRUCTION_ONLY",
                                place=f"Article 32 paragraph 1 point ({x})") for x in "cdfg"}
        self.assertEqual(explain.one_change(records["g"])["new_text"], "(g) technical assistance.")
        self.assertEqual(explain.one_change(records["f"])["new_text"], "(f) preliminary work, such as studies;")
        self.assertEqual(explain.one_change(records["c"])["new_text"], "(c) the drawing up of guidelines;")
        self.assertTrue(explain.one_change(records["g"])["instruction"].startswith("(c) points (f) and (g)"))
        client = ready_client()
        changes = explain.explain(client, SYSTEMS, explain.build_items(list(records.values()))[0], [])[1]["changes"]
        self.assertEqual([c["case"] for c in changes], ["CHANGED", "REMOVED", "CHANGED", "CHANGED"])
        sent = sent_of(client)
        self.assertEqual([s["new_text"] for s in sent], ["(c) the drawing up of guidelines;",
                                                         "(f) preliminary work, such as studies;",
                                                         "(g) technical assistance."])
        self.assertTrue(all("instruction" not in s for s in sent))      # no other points go along
        plain = provision("9", "15", "REPLACE", None, None, "(2) Article 15 point (b) is replaced: ‘(b) x’", "INSTRUCTION_ONLY",
                          place="Article 15 point (b)")
        self.assertEqual(explain.one_change(plain), plain)               # a simple instruction stays as it is

    def test_v21_the_whole_article_goes_along_as_background(self):
        record = provision("7", "32", "REPLACE", None, None, "(a) point (c) is replaced by the following: ‘(c) new.’",
                           "INSTRUCTION_ONLY", place="Article 32 paragraph 1 point (c)", heading="Financing",
                           article_before={"version_date": "2008-08-13", "text": "1. (c) old guidelines; (d) studies."},
                           article_now={"version_date": "2021-07-16", "text": "1. (c) new."})
        item = explain.build_items([record])[0]
        sent, remarks = explain.simple_request(item, record, "CHANGED")
        self.assertEqual(sent["article_heading"], "Financing")
        self.assertEqual(sent["article_before"], {"version_date": "2008-08-13", "text": "1. (c) old guidelines; (d) studies."})
        self.assertEqual(sent["article_now"]["text"], "1. (c) new.")
        self.assertEqual(remarks, ["NEW_MISSING"])
        plain = provision("8", "32", "REPLACE", None, None, "(a) point (c) is replaced.", "INSTRUCTION_ONLY")
        sent, _ = explain.simple_request(explain.build_items([plain])[0], plain, "CHANGED")
        self.assertNotIn("article_before", sent)                        # older exports still work

    def test_an_article_with_several_changes_and_a_missing_answer(self):
        records = [article_14(1, P1_OLD, P1_NEW, "MATCHED_STORED_EVIDENCE"),
                   article_14(8, None, P8_NEW, "PREVIOUS_WORDING_UNRESOLVED")]
        item = explain.build_items(records)[0]
        broken = ChatResult(ok=True, finish_reason="stop", content="")
        with TemporaryDirectory() as folder:
            client = ready_client(injected=[reply({"explanation": "Absatz 1 erhält einen neuen Satz."}), broken])
            name, explanation = explain.explain(client, SYSTEMS, item, [], {}, Path(folder))
            self.assertEqual(explanation["outcome"], "PARTIAL")
            self.assertEqual([c["remark_texts"] for c in explanation["changes"]],
                             [[], ["Alter Wortlaut nicht verfügbar: erklärt aus dem neuen Wortlaut.",
                                   "Erklärung konnte nicht erstellt werden."]])
            again = ready_client()
            name, explanation = explain.explain(again, SYSTEMS, item, [], {}, Path(folder))
            self.assertEqual(len(again.calls), 1)                       # only the missing one is asked again
            self.assertEqual(explanation["outcome"], "EXPLAINED")
            self.assertIn("Article 14 paragraph 8:", explanation["what_changed"])


class HelperTests(unittest.TestCase):
    def test_items_group_changes_of_one_article(self):
        items = explain.build_items(RECORDS)
        kinds = sorted((i["kind"], len(i["records"])) for i in items)
        self.assertEqual(kinds, [("ACT", 1), ("CORRIGENDUM", 1), ("PROVISION", 1), ("PROVISION", 1), ("PROVISION", 2)])

    def test_routing_order(self):
        results, index = RESULTS, explain.locator_index(RESULTS)
        items = {explain.item_facts(i)["provision"] or i["kind"]: explain.item_facts(i) for i in explain.build_items(RECORDS)}
        self.assertEqual(explain.route(items["Article 15"], results, index)["route"], "CHANGED_PROVISION")
        self.assertEqual(explain.route(items["Article 14a"], results, index)["route"], "CHANGING_PROVISION")
        self.assertEqual(explain.route(items["CORRIGENDUM"], results, index)["route"], "CORRECTED_PROVISION")
        self.assertEqual(explain.route(items["ACT"], results, index)["route"], "CHANGING_ACT")
        lonely = {**items["Article 15"], "changed": {"celex": "X"}, "changing": {"celex": "Y"}, "instruction_locator": None}
        self.assertEqual(explain.route(lonely, results, index)["route"], "NOT_SCREENED")

    def test_no_explanation_without_evidence(self):
        only_old = provision("9", "15", "REPLACE", "Levels shall not exceed 100 TJ.", None, "", "OLD_ONLY")
        corr = {**RECORDS[4], "corrections": [], "instruction": "", "corrigendum_source": "NOT_FETCHED"}
        reasons = [explain.missing_reason({"kind": r["kind"], "records": [r]}) for r in (only_old, corr)]
        self.assertEqual(reasons, ["NEW_WORDING_MISSING", "CORRIGENDUM_TEXT_MISSING"])

    def test_roles_of_the_acts(self):
        roles = explain.act_roles(RECORDS)
        self.assertEqual(roles["32010L0075"]["labels"], ["Ändert andere Rechtsakte", "Wurde geändert", "Wurde berichtigt"])
        self.assertEqual(roles["32010L0075R(01)"]["labels"], ["Berichtigung"])

    def test_publication_requires_exact_affected_locator_and_wording(self):
        item = explain.build_items([RECORDS[0]])[0]
        facts = explain.item_facts(item)
        routing = explain.route(facts, RESULTS, explain.locator_index(RESULTS))
        record = {"facts": facts, "kind": "PROVISION", "routing": routing, "explanation": {"significance": "SUBSTANTIVE"}}
        self.assertTrue(explain.goes_to_inbox(record))
        facts["changes"][0]["provision"] = "Article 15 paragraph 3"
        self.assertIn("PROVISION_RELEVANCE_UNVERIFIED", explain.review_reasons(record))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "export").mkdir()
        (root / "export" / "records.jsonl").write_text("\n".join(json.dumps(r) for r in RECORDS), encoding="utf-8")
        (root / "cls").mkdir()
        (root / "cls" / "results.json").write_text(json.dumps(RESULTS), encoding="utf-8")
        self.root, self.out = root, root / "out"

    def run_main(self, client, *options):
        argv = ["--export-dir", str(self.root / "export"), "--classification-dir", str(self.root / "cls"),
                "--output-dir", str(self.out), "--workers", "1", *options]
        with patch.object(explain.QwenClient, "from_env", return_value=client), \
                patch("classify_eurlex_provisions.time.sleep"):
            return explain.main(argv)

    def test_full_run_explains_and_fills_the_inbox(self):
        self.assertEqual(self.run_main(Client()), 0)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["outcomes"]["EXPLAINED"], 4)
        self.assertEqual(summary["outcomes"]["MISSING_EVIDENCE"], 1)       # the repeal without any text
        inbox = json.loads((self.out / "inbox" / "Umweltmanagement.json").read_text(encoding="utf-8"))
        lines = [line for card in inbox["cards"] for change in card["changes"] for line in change["changes"]]
        self.assertTrue(any(line["explanation"] == "In Article 15 wird der Wortlaut geändert." for line in lines))
        page = (self.out / "preview" / "Umweltmanagement.html").read_text(encoding="utf-8")
        self.assertIn("In Article 15 wird der Wortlaut geändert.", page)
        self.assertIn("Neuer Wortlaut nicht verfügbar", page)            # Article 15 paragraph 4: instruction only
        with (self.out / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertEqual(len(rows), 6)

    def test_replay_by_item_ids_and_implicit_repeal_in_review(self):
        implicit = {**RECORDS[-1], "relation": "IMPLICITLY_REPEALS", "operation": "IMPLICITLY_REPEALS"}
        (self.root / "export" / "records.jsonl").write_text(json.dumps(implicit), encoding="utf-8")
        ids = self.root / "ids.txt"
        ids.write_text("a1\n", encoding="utf-8")
        client = Client()
        self.assertEqual(self.run_main(client, "--item-ids", str(ids)), 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["outcomes"]["SCOPE_REVIEW"], 1)

    def test_count_only_and_sample(self):
        client = Client()
        self.assertEqual(self.run_main(client, "--count-only"), 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.run_main(Client(), "--sample", "3"), 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["items_planned"], 3)


if __name__ == "__main__":
    unittest.main()
