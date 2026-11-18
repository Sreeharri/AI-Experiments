"""Tests of explain_eurlex_changes.py with a stand-in Qwen client (no Qwen, no database)."""

import csv
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from lr_ai.qwen_client import ChatResult

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import explain_eurlex_changes as explain  # noqa: E402

CHANGING = {"celex": "32024L1785", "title": "Directive (EU) 2024/1785 amending Directive 2010/75/EU",
            "date": "2024-04-24", "entry_into_force": "2024-08-04", "application": None, "url": None}
CHANGED = {"celex": "32010L0075", "title": "Industrial Emissions Directive", "url": None}
FINALS = [{"locator": "Article 2", "text": "Member States shall bring into force the laws by 1 July 2026."}]


def provision(change, root, operation, old, new, instruction, wording, ordinal=1):
    return {"kind": "PROVISION", "id": f"p{change}", "change_id": change, "relation": "AMENDS",
            "changing": CHANGING, "changed": CHANGED, "final_articles": FINALS, "ordinal": ordinal,
            "operation": operation, "provision": f"Article {root}", "root": ["article", root.lower()],
            "changing_document_id": "doc-1785", "changed_document_id": "doc-ied", "relationship_id": "rel-1",
            "effective_from": "2024-08-04", "old_text": old, "new_text": new, "instruction": instruction,
            "instruction_locator": "Article 1", "wording": wording}


RECORDS = [
    provision("1", "15", "REPLACE", "Emission limit values. 3. Levels shall not exceed 100 TJ per year.",
              "Emission limit values. 3. Emission levels shall not exceed 85 TJ per year.",
              "(2) Article 15 is amended as follows: paragraph 3 is replaced by the following.", "FULL"),
    provision("2", "15", "REPLACE", None, None, "(2) Article 15 paragraph 4 is deleted.", "INSTRUCTION_ONLY", 2),
    provision("3", "14a", "INSERT", None, "Operators shall prepare an environmental management system by 31 December 2027.",
              "(3) the following Article 14a is inserted.", "NEW_ONLY", 3),
    provision("4", "3", "REPLACE", "(2) pollution means emissions.", "(2) pollution means the introduction of substances.",
              "(1) Article 3 point 2 is replaced.", "FULL", 4),
    {"kind": "CORRIGENDUM", "id": "c1", "relation": "CORRECTS",
     "changing": {**CHANGING, "celex": "32010L0075R(01)", "title": "Corrigendum"}, "changed": CHANGED,
     "final_articles": [], "corrections": [{"location": "On page 22, Article 3(2)", "wrong": "100 TJ", "correct": "85 TJ"}],
     "instruction": ""},
    {"kind": "ACT", "id": "a1", "relation": "REPEALS", "changing": {**CHANGING, "celex": "32010L0075", "title": "IED"},
     "changed": {"celex": "32008L0001", "title": "IPPC Directive", "url": None}, "final_articles": [],
     "operation": "REPEALS", "instruction": "", "instruction_locator": None, "wording": "NONE"},
]
RESULTS = {
    "32010L0075": {"Article 15": {"level": "DIRECT", "primary_domain": "environmental_protection",
                                  "primary_title": "Umweltschutz", "departments": ["Umweltmanagement"],
                                  "secondary_departments": []},
                   "Article 3": {"level": "INDIRECT", "primary_domain": "environmental_protection",
                                 "primary_title": "Umweltschutz", "departments": ["Umweltmanagement"],
                                 "secondary_departments": ["Energiesysteme"]}},
    "32024L1785": {"Article 1": {"level": "INDIRECT", "primary_domain": "environmental_protection",
                                 "primary_title": "Umweltschutz", "departments": ["Recht"], "secondary_departments": []}},
}


def reply(value):
    return ChatResult(ok=True, finish_reason="stop", content=json.dumps(value))


def answer_for(messages):
    """What a well-behaved model would answer, decided from the request."""
    answer = plain_answer(messages)
    if "sentences" not in answer:
        sent = json.loads(messages[1]["content"].partition("\n\nTASK:")[0])
        answer["importance"] = ("Die Änderung betrifft eine für Max Bögl relevante Vorschrift im Umweltschutz."
                                if "why_relevant" in sent else "")
        answer["to_confirm"] = ("Ob ein Werk unter die Vorschrift fällt, ist noch zu bestätigen."
                                if "why_relevant" in sent else "")
    return answer


def plain_answer(messages):
    user = messages[1]["content"]
    sent = json.loads(user.partition("\n\nTASK:")[0])
    if "Explain the corrigendum" in user:
        return {"headline": "Schwellenwert berichtigt", "what_was_wrong": "Statt 100 TJ muss es 85 TJ heißen.",
                "facts": [{"what": "Schwellenwert", "before": "100 TJ", "after": "85 TJ", "passages": ["W1", "R1"]}],
                "affects": "NUMBER"}
    if "Write the paragraph" in user:
        return {"sentences": ["Die Richtlinie ändert die Industrieemissions-Richtlinie.",
                              "Der Schwellenwert sinkt auf 85 TJ."]}
    provision_ = sent.get("provision")
    if provision_ == "Article 15":
        return {"headline": "Schwellenwert gesenkt", "what_changed": "Der Grenzwert sinkt von 100 TJ auf 85 TJ pro Jahr.",
                "facts": [{"what": "Grenzwert", "before": "100 TJ", "after": "85 TJ", "passages": ["O1", "N1"]}],
                "dates": [{"what": "Umsetzungsfrist", "date": "1. Juli 2026", "passages": ["F1"]}],
                "significance": "SUBSTANTIVE"}
    if provision_ == "Article 14a":
        return {"headline": "Neue Pflicht zum Umweltmanagementsystem",
                "what_changed": "Betreiber müssen bis zum 31. Dezember 2027 ein Umweltmanagementsystem einführen.",
                "facts": [{"what": "Pflicht", "before": "", "after": "31 December 2027", "passages": ["N1"]}],
                "dates": [], "significance": "SUBSTANTIVE"}
    return {"headline": "Begriff neu gefasst", "what_changed": "Die Begriffsbestimmung wird nur sprachlich angepasst.",
            "facts": [], "dates": [], "significance": "EDITORIAL"}


class Client:
    """Stand-in client: refuses requests before discover(), answers from answer_for, can inject replies."""

    def __init__(self, injected=None):
        self.ready, self.calls, self.injected = False, [], list(injected or [])
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
        return reply(answer_for(messages))


class HelperTests(unittest.TestCase):
    def test_items_group_changes_of_one_article(self):
        items = explain.build_items(RECORDS)
        kinds = sorted((i["kind"], len(i["records"])) for i in items)
        self.assertEqual(kinds, [("ACT", 1), ("CORRIGENDUM", 1), ("PROVISION", 1), ("PROVISION", 1), ("PROVISION", 2)])
        article15 = next(i for i in items if len(i["records"]) == 2)
        facts = explain.item_facts(article15)
        self.assertEqual((facts["provision"], facts["wording"], facts["effective_from"]), ("Article 15", "FULL", "2024-08-04"))

    def test_routing_order(self):
        results, index = RESULTS, explain.locator_index(RESULTS)
        items = {explain.item_facts(i)["provision"] or i["kind"]: explain.item_facts(i) for i in explain.build_items(RECORDS)}
        self.assertEqual(explain.route(items["Article 15"], results, index)["route"], "CHANGED_PROVISION")
        self.assertEqual(explain.route(items["Article 15"], results, index)["departments"], ["Umweltmanagement"])
        self.assertEqual(explain.route(items["Article 14a"], results, index)["route"], "CHANGING_PROVISION")
        self.assertEqual(explain.route(items["CORRIGENDUM"], results, index)["route"], "CORRECTED_PROVISION")
        self.assertEqual(explain.route({**items["CORRIGENDUM"], "correction_roots": []}, results, index)["route"],
                         "CHANGED_ACT")
        self.assertEqual(explain.route(items["ACT"], results, index)["route"], "CHANGING_ACT")
        lonely = {**items["Article 15"], "changed": {"celex": "X"}, "changing": {"celex": "Y"}, "instruction_locator": None}
        self.assertEqual(explain.route(lonely, results, index)["route"], "NOT_SCREENED")

    def test_long_texts_keep_only_changed_passages(self):
        same = " ".join(f"Sentence {n} stays the same in both versions of the text." for n in range(300))
        old, new = same + " The limit is 100 TJ.", same + " The limit is 85 TJ."
        text, passages = explain.passages_of(new, "N", budget=2000, keep=explain.other_side(old))
        self.assertIn("85 TJ", text)
        self.assertTrue(text.startswith("…"))
        self.assertLess(sum(map(len, passages.values())), 2100)

    def test_checks_reject_invented_values_and_hidden_changes(self):
        items = explain.build_items(RECORDS)
        article15 = next(i for i in items if len(i["records"]) == 2)
        _, sent, passages = explain.request_for(article15)
        good = answer_for([None, {"content": json.dumps(sent) + "\n\nTASK: x"}])
        self.assertEqual(explain.validate_change(good, passages, sent)["significance"], "SUBSTANTIVE")
        for change, message in ((lambda a: a["facts"][0].update(after="90 TJ"), "after of"),
                                (lambda a: a["facts"][0].update(before="120 TJ"), "before of"),
                                (lambda a: a.update(significance="EDITORIAL"), "not EDITORIAL"),
                                (lambda a: a["dates"][0].update(date="1. Juli 2027"), "date"),
                                (lambda a: a.update(what_changed="The limit for the plants is now 85 TJ and it applies to all of the operators in the Union."),
                                 "Write everything in German")):
            bad = json.loads(json.dumps(good))
            change(bad)
            with self.assertRaisesRegex(explain.Unclassified, message):
                explain.validate_change(bad, passages, sent)
        new_only = next(i for i in items if explain.item_facts(i)["provision"] == "Article 14a")
        _, sent, passages = explain.request_for(new_only)
        bad = answer_for([None, {"content": json.dumps(sent) + "\n\nTASK: x"}])
        bad["facts"][0]["before"] = "nothing"
        with self.assertRaisesRegex(explain.Unclassified, "before must stay empty"):
            explain.validate_change(bad, passages, sent)

    def test_corrigendum_that_changes_a_number_is_not_wording_only(self):
        item = next(i for i in explain.build_items(RECORDS) if i["kind"] == "CORRIGENDUM")
        name, sent, passages = explain.request_for(item)
        self.assertEqual((name, list(passages)), ("corrigendum", ["W1", "R1"]))
        answer = answer_for([None, {"content": json.dumps(sent) + "\n\nTASK: Explain the corrigendum."}])
        answer["affects"] = "WORDING_ONLY"
        with self.assertRaisesRegex(explain.Unclassified, "not WORDING_ONLY"):
            explain.validate_corrigendum(answer, passages, sent)


    def test_no_explanation_without_new_wording_or_corrigendum_text(self):
        only_old = provision("9", "15", "REPLACE", "Levels shall not exceed 100 TJ.", None, "", "OLD_ONLY")
        deleted = provision("10", "16", "DELETE", "Article 16 Reports.", None, "", "OLD_ONLY")
        corr = {**RECORDS[4], "corrections": [], "instruction": "", "corrigendum_source": "NOT_FETCHED"}
        other = {**RECORDS[4], "corrections": [], "instruction": "", "corrigendum_source": "OTHER_LANGUAGES_ONLY"}
        reasons = [explain.missing_reason({"kind": r["kind"], "records": [r]}) for r in (only_old, deleted, corr, other)]
        self.assertEqual(reasons, ["NEW_WORDING_MISSING", None, "CORRIGENDUM_TEXT_MISSING", "OTHER_LANGUAGES_ONLY"])
        facts = explain.item_facts({"id": "x", "kind": "PROVISION", "records": [only_old]})
        self.assertIn("Article 15 32010L0075 ersetzt durch 32024L1785, ab 2024-08-04. Der neue Wortlaut liegt nicht vor.",
                      explain.code_line(facts, "de"))
        self.assertFalse(explain.goes_to_inbox({"kind": "CORRIGENDUM", "relation": "CORRECTS", "no_text": True,
                                                "routing": {"level": "DIRECT"}}))

    def test_without_old_wording_nothing_is_compared_and_before_stays_empty(self):
        item = {"id": "x", "kind": "PROVISION", "records": [RECORDS[2]]}
        RECORDS_REPLACE = provision("11", "14", "REPLACE", None, "Health surveillance applies to cancer cases.",
                                    "(4) Article 14 is replaced by the following.", "NEW_ONLY")
        item = {"id": "y", "kind": "PROVISION", "records": [RECORDS_REPLACE]}
        name, sent, passages = explain.request_for(item)
        self.assertIn("compare with nothing", sent["notes"][0])
        self.assertIn("M1", passages)                                  # dates of the changing act are given
        schema = explain.change_schema(passages, False)
        self.assertEqual(schema["properties"]["facts"]["items"]["properties"]["before"]["maxLength"], 0)
        have = explain.text_situation(item)
        reply = {"headline": "Meldepflicht erweitert", "what_changed": "Früher galt weniger. Nun gilt mehr.",
                 "facts": [], "dates": [], "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}
        with self.assertRaises(explain.Unclassified):
            explain.validate_change(reply, passages, sent, have)
        reply["headline"], reply["what_changed"] = "Gesundheitsüberwachung bei Krebsfällen", "Sie gilt für Krebsfälle."
        self.assertEqual(explain.validate_change(reply, passages, sent, have)["headline"],
                         "Gesundheitsüberwachung bei Krebsfällen")

    def test_dates_may_come_from_the_changing_act_and_old_words_from_the_instruction(self):
        record = provision("12", "4", "REPLACE", None, None,
                           "(1) In Article 4, the words ‘100 TJ’ are replaced by ‘85 TJ’.", "INSTRUCTION_ONLY")
        item = {"id": "z", "kind": "PROVISION", "records": [record]}
        name, sent, passages = explain.request_for(item)
        have = explain.text_situation(item)
        self.assertTrue(have["pair"])
        reply = {"headline": "Schwelle von 100 TJ auf 85 TJ gesenkt",
                 "what_changed": "Die Schwelle sinkt von 100 TJ auf 85 TJ. Das gilt ab dem 4. August 2024.",
                 "facts": [{"what": "Schwelle", "before": "100 TJ", "after": "85 TJ", "passages": ["I1"]},
                           {"what": "Inkrafttreten", "before": "", "after": "4. August 2024", "passages": ["M1"]}],
                 "dates": [{"what": "gilt ab", "date": "4. August 2024", "passages": ["M1"]}],
                 "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}
        self.assertEqual(len(explain.validate_change(reply, passages, sent, have)["facts"]), 2)

    def test_places_that_cannot_be_read_form_one_item(self):
        a = {**provision("20", "1", "REPLACE", None, None, "Directive 2010/75/EU is amended: ...", "INSTRUCTION_ONLY"),
             "root": None, "provision": "Title"}
        b = {**a, "id": "p21", "change_id": "21", "provision": ""}
        items = explain.build_items([a, b])
        self.assertEqual(len(items), 1)
        self.assertEqual(explain.request_for(items[0])[1]["provision"], "several places, see the instruction")


    def test_removals_mixed_changes_and_long_texts(self):
        repealed = {**provision("30", "51", "OTHER", "Article 51 Amendments to Directive 2009/73/EC.", None, "",
                                "OLD_ONLY"), "relation": "REPEALS"}
        mixed = [provision("31", "2", "ADD", None, None, "", "NONE"),
                 provision("32", "2", "DELETE", "Annex II point 79 standard EN 1.", None, "", "OLD_ONLY", 2)]
        self.assertIsNone(explain.missing_reason({"kind": "PROVISION", "records": [repealed]}))
        self.assertEqual(explain.missing_reason({"kind": "PROVISION", "records": mixed}), "NEW_WORDING_MISSING")
        item = {"id": "r", "kind": "PROVISION", "records": [repealed]}
        name, sent, passages = explain.request_for(item)
        have = explain.text_situation(item)
        self.assertIn("Nothing is new", sent["notes"][-1])
        reply = {"headline": "Neue Pflicht zu Wasserstoffplänen", "what_changed": "Neue Pläne sind vorzulegen.",
                 "facts": [], "dates": [], "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}
        with self.assertRaisesRegex(explain.Unclassified, "nothing is new"):
            explain.validate_change(reply, passages, sent, have)
        reply.update(headline="Artikel 51 aufgehoben", what_changed="Artikel 51 mit den Änderungen der Richtlinie "
                                                                     "2009/73/EC gilt nicht mehr.")
        self.assertEqual(explain.validate_change(reply, passages, sent, have)["headline"], "Artikel 51 aufgehoben")
        long_item = {"id": "l", "kind": "PROVISION", "records": [{**RECORDS[0], "trimmed": True}]}
        name, sent, passages = explain.request_for(long_item)
        editorial = {"headline": "Text angepasst", "what_changed": "Nur sprachlich.", "facts": [], "dates": [],
                     "significance": "EDITORIAL", "importance": "", "to_confirm": ""}
        with self.assertRaisesRegex(explain.Unclassified, "cannot be EDITORIAL"):
            explain.validate_change(editorial, passages, sent, explain.text_situation(long_item))
        same = {**editorial, "significance": "SUBSTANTIVE",
                "facts": [{"what": "Liste", "before": "ABEC, ADC", "after": "ABEC, ADC", "passages": ["O1", "N1"]}]}
        with self.assertRaisesRegex(explain.Unclassified, "are the same"):
            explain.validate_change(same, passages, sent, explain.text_situation(long_item))

    def test_importance_for_max_boegl_comes_from_the_classification(self):
        item = next(i for i in explain.build_items(RECORDS) if explain.item_facts(i)["provision"] == "Article 15")
        routing = {"level": "DIRECT", "route": "CHANGED_PROVISION", "domain": "environmental_protection",
                   "domain_title": "Umweltschutz", "subject": "Emission limits",
                   "reason": "Plants of the company are covered.", "departments": ["Umweltmanagement"]}
        name, sent, passages = explain.request_for(item, routing)
        self.assertIn("Umweltmanagement", sent["why_relevant"])
        self.assertEqual(explain.change_schema(passages)["properties"]["importance"]["maxLength"], 500)
        answer = answer_for([None, {"content": json.dumps(sent) + "\n\nTASK: x"}])
        have = explain.text_situation(item)
        self.assertIn("Max Bögl", explain.validate_change(answer, passages, sent, have)["importance"])
        for value, message in (("", "one or two sentences"), ("Max Bögl sollte die Anlagen prüfen.", "does not advise")):
            with self.assertRaisesRegex(explain.Unclassified, message):
                explain.validate_change({**answer, "importance": value}, passages, sent, have)
        explain.BUSINESS_AREAS["environmental_protection"] = ["Umweltgenehmigungen der Werke"]
        try:
            self.assertIn("Umweltgenehmigungen der Werke", explain.request_for(item, routing)[1]["why_relevant"])
        finally:
            explain.BUSINESS_AREAS.clear()
        broad = {**routing, "route": "CHANGING_ACT"}                   # only the changing act is classified
        self.assertNotIn("why_relevant", explain.request_for(item, broad)[1])
        name, sent, passages = explain.request_for(item, {"level": None})
        self.assertNotIn("why_relevant", sent)
        self.assertEqual(explain.change_schema(passages)["properties"]["importance"]["maxLength"], 0)
        with self.assertRaisesRegex(explain.Unclassified, "must stay empty"):
            explain.validate_change(answer if answer["importance"] else {**answer, "importance": "x"}, passages, sent,
                                    have)

    def test_roles_of_the_acts(self):
        records = RECORDS + [{**RECORDS[0], "changing": {**CHANGING, "celex": "32026L0001"},
                              "changed": {"celex": "32024L1785", "title": "", "url": None}},
                             {**RECORDS[5], "relation": "CORRECTS", "changing": {**CHANGING, "celex": "32025R0099"}}]
        roles = explain.act_roles(records)
        labels = {celex: roles[celex]["labels"] for celex in ("32010L0075", "32024L1785", "32026L0001",
                                                              "32010L0075R(01)", "32025R0099", "32008L0001")}
        self.assertEqual(labels, {"32010L0075": ["Ändert andere Rechtsakte", "Wurde geändert", "Wurde berichtigt"],
                                  "32024L1785": ["Ändert andere Rechtsakte", "Wurde geändert"],
                                  "32026L0001": ["Ändert andere Rechtsakte"], "32010L0075R(01)": ["Berichtigung"],
                                  "32025R0099": ["Berichtigung"], "32008L0001": ["Wurde geändert", "Wurde berichtigt"]})
        self.assertEqual(explain.role_of(roles, "32008L0001", "en")["labels"], ["Was changed", "Was corrected"])


    def test_each_change_keeps_its_own_place_date_and_wording(self):
        early = {**provision("40", "5", "REPLACE", None, "5. Reports are due by 1 March.",
                             "(5) in Article 5, paragraph 5 is replaced.", "NEW_ONLY"),
                 "provision": "Article 5 paragraph 5", "effective_from": "2025-01-01", "effective_source": "CHANGE",
                 "comparison_status": "PREVIOUS_WORDING_UNRESOLVED"}
        late = {**provision("41", "5", "INSERT", None, "6. Small companies are exempt.",
                            "(5) in Article 5, paragraph 6 is inserted.", "NEW_ONLY", 2),
                "provision": "Article 5 paragraph 6", "effective_from": "2027-01-01", "effective_source": "ANNOTATION",
                "comparison_status": "MATCHED_STORED_EVIDENCE"}
        item = explain.build_items([early, late])[0]
        facts = explain.item_facts(item)
        self.assertTrue(facts["dates_differ"])
        self.assertEqual([(c["provision"], c["effective_from"], c["effective_source"]) for c in facts["changes"]],
                         [("Article 5 paragraph 5", "2025-01-01", "CHANGE"), ("Article 5 paragraph 6", "2027-01-01",
                                                                             "ANNOTATION")])
        name, sent, passages = explain.request_for(item)
        self.assertEqual([c["applies_from"] for c in sent["changes"]], ["2025-01-01", "2027-01-01"])
        self.assertEqual(list(passages)[:2], ["N1", "N2"])                 # one numbering over both changes
        self.assertIn("different dates", " ".join(sent["notes"]))
        record = {"facts": facts, "kind": "PROVISION", "relation": "AMENDS", "routing": {"route": "CHANGED_PROVISION"},
                  "texts": [{"old": None, "new": "5. Reports are due by 1 March."},
                            {"old": None, "new": "6. Small companies are exempt."}]}
        lines = explain.entry(record, "de")["changes"]
        self.assertEqual(lines[0]["wording_notes"], ["Alter Wortlaut nicht verfügbar"])
        self.assertEqual(lines[1]["wording_notes"], [])                     # an insertion has no old wording


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

    def test_full_run_explains_routes_and_builds_inbox(self):
        client = Client()
        self.assertEqual(self.run_main(client, "--link-template", "/acts/{changed_document_id}/changes?change={change_id}"), 0)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["explained"], summary["failed"]), (5, 0))
        self.assertEqual(summary["by_kind"], {"ACT": 1, "CORRIGENDUM": 1, "PROVISION": 3})
        self.assertEqual(summary["significance"]["NO_TEXT"], 1)          # the repeal without text: line by code
        self.assertEqual(summary["route"]["CHANGED_PROVISION"], 2)
        inbox = json.loads((self.out / "inbox" / "Umweltmanagement.json").read_text(encoding="utf-8"))
        changes = inbox["cards"][0]["changes"]
        headlines = {c["headline"] for c in changes}
        self.assertIn("Schwellenwert gesenkt", headlines)
        self.assertIn("Schwellenwert berichtigt", headlines)
        self.assertNotIn("Begriff neu gefasst", headlines)               # EDITORIAL stays out of the inbox
        article15 = next(c for c in changes if c["headline"] == "Schwellenwert gesenkt")
        self.assertEqual(article15["facts"][0]["quotes"][0][:15], "Emission limit ")
        self.assertEqual(article15["dates"][0]["date"], "1. Juli 2026")
        self.assertIn("100 TJ", article15["old_text"])
        recht = json.loads((self.out / "inbox" / "Recht.json").read_text(encoding="utf-8"))
        self.assertEqual([c["headline"] for c in recht["cards"][0]["changes"]], ["Neue Pflicht zum Umweltmanagementsystem"])
        repeal = next(c for c in inbox["cards"] if c["celex"] == "32008L0001")["changes"][0]
        self.assertEqual((repeal["label"], repeal["headline"][:9]), ("Aufhebung", "Aufhebung"))
        page = (self.out / "preview" / "Umweltmanagement.html").read_text(encoding="utf-8")
        self.assertIn("Berichtigung", page)
        self.assertIn('class="before">100 TJ', page)
        acts = json.loads((self.out / "acts" / "32024L1785.json").read_text(encoding="utf-8"))
        self.assertEqual(len(acts["sentences"]), 2)
        with (self.out / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertEqual(len(rows), 6)
        self.assertIn("significance right (yes/no)", rows[0])
        amending = json.loads((self.out / "act_pages" / "32024L1785.json").read_text(encoding="utf-8"))
        made = amending["changes_made"]
        self.assertEqual((made["count"], made["changed_acts"][0]["celex"], len(made["summary"])), (3, "32010L0075", 2))
        self.assertIsNone(amending["changed_by"])
        first = next(i for i in made["items"] if i["provision"] == "Article 15")
        self.assertEqual((first["headline"], first["short"]), ("Schwellenwert gesenkt",
                                                               "Der Grenzwert sinkt von 100 TJ auf 85 TJ pro Jahr."))
        self.assertEqual(first["change_ids"], ["1", "2"])
        self.assertEqual(first["link"], "/acts/doc-ied/changes?change=1")
        card = json.loads(next((self.out / "inbox").glob("Umweltmanagement*.json")).read_text(encoding="utf-8"))["cards"][0]
        self.assertEqual(card["role"]["labels"], ["Ändert andere Rechtsakte", "Wurde geändert", "Wurde berichtigt"])
        self.assertTrue(any("Max Bögl" in change["importance"] for change in card["changes"]))
        base = json.loads((self.out / "act_pages" / "32010L0075.json").read_text(encoding="utf-8"))
        self.assertEqual(base["changed_by"]["count"], 4)                    # 3 changes by 32024L1785 + 1 corrigendum
        self.assertIn("4-mal", base["changed_by"]["summary_line"])
        self.assertIn("32024L1785", base["changed_by"]["summary_line"])
        self.assertEqual(base["changes_made"]["items"][0]["label"], "Aufhebung")   # the IED repeals the IPPC directive
        self.assertIn("Änderungen durch diesen Rechtsakt", (self.out / "preview" / "acts" / "32024L1785.html")
                      .read_text(encoding="utf-8"))
        calls = len(client.calls)
        self.assertEqual(self.run_main(client), 0)                        # nothing is asked twice
        self.assertEqual(len(client.calls), calls)

    def test_failed_item_is_retried_and_language_change_needs_new_folder(self):
        wrong = reply({"headline": "x", "what_changed": "Der Grenzwert sinkt auf 70 TJ.", "facts": [], "dates": [],
                       "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""})
        client = Client(injected=[wrong, wrong])
        self.assertEqual(self.run_main(client), 0)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["failed"], 1)
        self.assertIn("70", summary["failures"][0]["error"])
        self.assertEqual(self.run_main(Client()), 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["failed"], 0)
        self.assertEqual(self.run_main(Client(), "--language", "en"), 1)

    def test_count_only_and_sample(self):
        client = Client()
        self.assertEqual(self.run_main(client, "--count-only"), 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.run_main(Client(), "--sample", "3"), 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["items_planned"], 3)


if __name__ == "__main__":
    unittest.main()
