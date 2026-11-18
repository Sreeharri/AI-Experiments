"""Tests of explain_eurlex_changes.py (v1.7) with a stand-in Qwen client (no Qwen, no database).

Regression cases from the pilots: Directive 2014/24 Article 13 points (a) and (b), Directive 2004/37 Article 14
paragraphs 1 and 8 (different evidence), an explicit repeal, a new-only change and a partially failed annex.
"""

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

# Directive 2014/24 Article 13, as amended by Delegated Regulation (EU) 2023/2495.
PROCUREMENT = {"celex": "32023R2495", "title": "Commission Delegated Regulation (EU) 2023/2495 amending Directive "
                                               "2014/24/EU in respect of the thresholds", "date": "2023-11-15",
               "entry_into_force": "2023-12-06", "application": None, "url": None}
POINT_A = ("(a) works contracts subsidised directly by more than 50 % by contracting authorities where the estimated "
           "value of the contract, net of VAT, is equal to or greater than EUR 5 538 000, where those contracts "
           "involve one of the following activities: (i) civil engineering activities;")
POINT_B = ("(b) service contracts subsidised directly by more than 50 % by contracting authorities where the estimated "
           "value of the contract, net of VAT, is equal to or greater than EUR 221 000 and which are connected to a "
           "works contract within the meaning of point (a).")


def article_13(letter, old_amount, new_amount, context):
    return {**provision(f"13{letter}", "13", "REPLACE", None, None,
                        f"(2) in Article 13, first paragraph is amended as follows: in point ({letter}), "
                        f"‘EUR {old_amount}’ is replaced by ‘EUR {new_amount}’;", "INSTRUCTION_ONLY",
                        place=f"Article 13 unnumbered paragraph 1 point ({letter})", heading="Contracts subsidised by "
                        "contracting authorities", point_context=context, effective_source="ANNOTATION",
                        effective_from="2024-01-01"),
            "changing": PROCUREMENT, "changed": {"celex": "32014L0024", "title": "Directive 2014/24/EU on public "
                                                 "procurement", "url": None}, "final_articles": []}


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


def unit_of(record, routing=None):
    item = explain.build_items([record])[0]
    return explain.build_unit(item, item["records"][0], routing)


def answer_for(messages):
    """What a well-behaved model would answer in the v1.7 format, decided from the request."""
    user = messages[1]["content"]
    sent = json.loads(user.partition("\n\nTASK:")[0])
    if "Write the paragraph" in user:
        return {"sentences": ["Die Richtlinie ändert die Industrieemissions-Richtlinie.",
                              "Die Änderungen werden in den jeweiligen Artikeln erläutert."]}
    evidence = {e["id"]: e for e in sent.get("evidence", [])}
    relevance = "why_relevant" in sent
    common = {"importance": "Die Änderung betrifft eine für Max Bögl relevante Vorschrift im Umweltschutz."
                            if relevance else "",
              "to_confirm": "Ob ein Werk unter die Vorschrift fällt, ist noch zu bestätigen." if relevance else ""}
    if "Explain the corrigendum" in user:
        return {"headline": "Schwellenwert berichtigt", "what_was_wrong": "Statt 100 TJ muss es 85 TJ heißen.",
                "facts": [{"what": "Schwellenwert", "before": "100 TJ", "after": "85 TJ", "before_evidence": ["W1"],
                           "after_evidence": ["R1"]}], "affects": "NUMBER", **common}
    situation = sent["situation"]
    new = [i for i, e in evidence.items() if i[0] == "N" and e.get("changed", True)]
    old = [i for i, e in evidence.items() if i[0] == "O" and e.get("changed", True)]
    instruction = [i for i in evidence if i[0] == "I"]
    if situation.startswith("REMOVAL"):
        fact = {"what": "Die Vorschrift", "before": "", "after": "", "before_evidence": (old or instruction)[:1],
                "after_evidence": []}
        return {"headline": "Vorschrift aufgehoben", "what_changed": "Die Vorschrift gilt in Zukunft nicht mehr.",
                "facts": [fact], "dates": [], "significance": "SUBSTANTIVE", **common}
    fact = {"what": "Inhalt der Vorschrift", "before": "die bisherige Fassung" if old else "",
            "after": "die geänderte Fassung", "before_evidence": old[:1], "after_evidence": (new or instruction)[:1]}
    dates = [{"kind": "TRANSPOSITION", "date": "1 July 2026", "evidence": ["F1"]}] if "F1" in evidence and \
        "1 July 2026" in evidence["F1"]["text"] else []
    return {"headline": "Vorschrift geändert", "what_changed": "Die Vorschrift wird geändert und gilt in der "
                                                                "angegebenen Fassung.",
            "facts": [fact], "dates": dates, "significance": "SUBSTANTIVE", **common}


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


class EvidenceTests(unittest.TestCase):
    def test_article_13_points_a_and_b_keep_their_own_subject(self):
        unit_b = unit_of(article_13("b", "215 000", "221 000", POINT_B))
        unit_a = unit_of(article_13("a", "5 382 000", "5 538 000", POINT_A))
        self.assertTrue(unit_b["subject"].startswith("service contracts subsidised"))
        self.assertTrue(unit_a["subject"].startswith("works contracts subsidised"))
        self.assertEqual(unit_b["situation"], "INSTRUCTION")
        self.assertIn("I1", unit_b["before_ids"])                     # the instruction names the replaced amount
        good = {"headline": "Schwellenwert für Dienstleistungsaufträge in Punkt (b) auf EUR 221 000 angehoben",
                "what_changed": "In Punkt (b) wird der Betrag EUR 215 000 durch EUR 221 000 ersetzt. Das betrifft "
                                "Dienstleistungsaufträge, die mit einem Bauauftrag verbunden sind.",
                "facts": [{"what": "Schwellenwert Punkt (b)", "before": "EUR 215 000", "after": "EUR 221 000",
                           "before_evidence": ["I1"], "after_evidence": ["I1"]}],
                "dates": [], "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}
        answer = explain.validate_unit(good, unit_b)
        self.assertEqual(answer["facts"][0]["after_evidence"][0]["text"],
                         "(2) in Article 13, first paragraph is amended as follows: in point (b), ‘EUR 215 000’ is "
                         "replaced by ‘EUR 221 000’;")                  # copied by Python, word for word
        self.assertEqual(answer["facts"][0]["after_evidence"][0]["ref"], "13b:I1")
        self.assertEqual([d["date"] for d in answer["dates"]], ["2024-01-01", "2023-12-06", "2023-11-15"])
        wrong = {**good, "headline": "Schwellenwert für Werkverträge von 215 000 auf 221 000 EUR erhöht"}
        with self.assertRaisesRegex(explain.Unclassified, "does not name works contracts.*service contracts"):
            explain.validate_unit(wrong, unit_b)                       # the pilot error: (b) is not about works
        swapped = {**good, "headline": "Schwellenwert für Dienstleistungen in Punkt (a)",
                   "what_changed": "In Punkt (a) wird der Betrag EUR 5 382 000 durch EUR 5 538 000 ersetzt.",
                   "facts": [{**good["facts"][0], "before": "EUR 5 382 000", "after": "EUR 5 538 000"}]}
        with self.assertRaisesRegex(explain.Unclassified, "does not name services"):
            explain.validate_unit(swapped, unit_a)                      # the second pilot error: (a) is works
        works = {**swapped, "headline": "Schwellenwert für Bauaufträge in Punkt (a) auf EUR 5 538 000 angehoben"}
        self.assertEqual(explain.validate_unit(works, unit_a)["subject_of_point"][:15], "works contracts")
        blind = unit_of(article_13("b", "215 000", "221 000", None))   # no wording of point (b): no category at all
        self.assertIn("not given", blind["sent"]["place"]["subject_of_point"])
        with self.assertRaisesRegex(explain.Unclassified, "does not name services"):
            explain.validate_unit(good, blind)
        neutral = {**good, "headline": "Schwellenwert in Punkt (b) auf EUR 221 000 angehoben",
                   "what_changed": "In Punkt (b) wird der Betrag EUR 215 000 durch EUR 221 000 ersetzt."}
        self.assertEqual(explain.validate_unit(neutral, blind)["headline"], neutral["headline"])
        with self.assertRaisesRegex(explain.Unclassified, "does not occur"):
            explain.validate_unit({**neutral, "facts": [{**good["facts"][0], "after": "EUR 222 000"}]}, blind)

    def test_2004_37_article_14_paragraph_1_old_and_new(self):
        unit = unit_of(article_14(1, P1_OLD, P1_NEW, "MATCHED_STORED_EVIDENCE"))
        self.assertEqual(unit["situation"], "BOTH")
        flags = {i: e["changed"] for i, e in unit["evidence"].items() if i[0] in "ON"}
        unchanged = [i for i, changed in flags.items() if i[0] == "N" and changed is False]
        added = [i for i, changed in flags.items() if i[0] == "N" and changed]
        self.assertEqual(len(unchanged), 1)                              # the first sentence did not change
        self.assertEqual(len(added), 1)
        self.assertIn("continue after the end of exposure", unit["evidence"][added[0]]["text"])
        self.assertEqual(unit["after_ids"], added)                       # only the added sentence can be cited
        schema = explain.unit_schema(unit)["properties"]["facts"]["items"]["properties"]
        self.assertNotIn(unchanged[0], schema["after_evidence"]["items"]["enum"])
        answer = {"headline": "Gesundheitsüberwachung nach Ende der Exposition",
                  "what_changed": "In Absatz 1 wird ein Satz eingefügt: Arzt oder Behörde können festlegen, dass die "
                                  "Gesundheitsüberwachung nach Ende der Exposition weiterläuft.",
                  "facts": [{"what": "Überwachung nach Exposition", "before": "", "after": "kann weiterlaufen",
                             "before_evidence": [], "after_evidence": [unchanged[0]]}],
                  "dates": [], "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}
        with self.assertRaisesRegex(explain.Unclassified, "cannot support it"):
            explain.validate_unit(answer, unit)                         # the pilot error: unchanged sentence cited
        answer["facts"][0]["after_evidence"] = added
        result = explain.validate_unit(answer, unit)
        self.assertEqual(result["facts"][0]["after_evidence"][0]["place"], "Article 14 paragraph 1")

    def test_2004_37_article_14_paragraph_8_new_only(self):
        unit = unit_of(article_14(8, None, P8_NEW, "PREVIOUS_WORDING_UNRESOLVED"))
        self.assertEqual((unit["situation"], unit["before_ids"]), ("NEW_ONLY", []))
        self.assertEqual(explain.unit_schema(unit)["properties"]["facts"]["items"]["properties"]["before"]["maxLength"], 0)
        compared = {"headline": "Meldepflicht für Krebsfälle konkretisiert",
                    "what_changed": "In Absatz 8 wurde die Meldepflicht für berufsbedingte Krebsfälle konkretisiert.",
                    "facts": [{"what": "Meldung", "before": "", "after": "alle berufsbedingten Krebsfälle",
                               "before_evidence": [], "after_evidence": ["N1"]}],
                    "dates": [], "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}
        with self.assertRaisesRegex(explain.Unclassified, "'konkretisiert' compares"):
            explain.validate_unit(compared, unit)                       # the pilot error: no old wording given
        plain = {**compared, "headline": "Meldepflicht für berufsbedingte Krebsfälle",
                 "what_changed": "Absatz 8 regelt nun: Alle berufsbedingten Krebsfälle sind der Behörde zu melden."}
        self.assertEqual(explain.validate_unit(plain, unit)["facts"][0]["after_evidence"][0]["text"], P8_NEW)

    def test_explicit_repeal_is_supported_by_the_instruction_alone(self):
        unit = unit_of(REPEAL)
        self.assertEqual((unit["situation"], unit["after_ids"]), ("REMOVAL_INSTRUCTION", []))
        answer = {"headline": "Richtlinie 2004/107/EG aufgehoben",
                  "what_changed": "Die Richtlinie 2004/107/EG wird mit Wirkung vom 12. Dezember 2026 aufgehoben.",
                  "facts": [{"what": "Richtlinie 2004/107/EG", "before": "", "after": "", "before_evidence": ["I1"],
                             "after_evidence": []}],
                  "dates": [], "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}
        result = explain.validate_unit(answer, unit)
        self.assertEqual(result["facts"][0]["before_evidence"][0]["text"], REPEAL["instruction"])
        self.assertEqual([d["source"] for d in result["dates"]], ["ENTRY_INTO_FORCE", "ADOPTION"])
        for headline, message in (("Rücktritt der Richtlinie 2004/107/EG", "Aufhebung"),
                                  ("Neue Regeln ersetzen die Richtlinie", "nothing is new")):
            with self.assertRaisesRegex(explain.Unclassified, message):
                explain.validate_unit({**answer, "headline": headline}, unit)
        empty = {**answer, "facts": [{**answer["facts"][0], "before_evidence": []}]}
        with self.assertRaisesRegex(explain.Unclassified, "needs before or after"):
            explain.validate_unit(empty, unit)

    def test_new_provision_and_dates_with_their_purpose(self):
        unit = unit_of(RECORDS[2])                                       # Article 14a inserted, new wording only
        self.assertEqual(unit["situation"], "NEW_ONLY")
        answer = {"headline": "Neuer Artikel 14a: Umweltmanagementsystem",
                  "what_changed": "Betreiber müssen bis zum 31. Dezember 2027 ein Umweltmanagementsystem erstellen.",
                  "facts": [{"what": "Frist", "before": "", "after": "31. Dezember 2027", "before_evidence": [],
                             "after_evidence": ["N1"]}],
                  "dates": [{"kind": "TRANSPOSITION", "date": "1. Juli 2026", "evidence": ["F1"]}],
                  "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}
        result = explain.validate_unit(answer, unit)                    # "neu" is fine for an insertion
        self.assertEqual([(d["what"], d["date"]) for d in result["dates"]],
                         [("gilt ab", "2024-08-04"), ("Inkrafttreten des Änderungsakts", "2024-08-04"),
                          ("Datum des Änderungsakts", "2024-04-24"), ("Umsetzungsfrist", "2026-07-01")])
        self.assertEqual([explain.iso_date(v) for v in ("1 July 2026", "1. Juli 2026", "1.7.2026", "Juli 2026")],
                         ["2026-07-01", "2026-07-01", "2026-07-01", None])
        with self.assertRaisesRegex(explain.Unclassified, "REPORTING date"):
            explain.validate_unit({**answer, "dates": [{**answer["dates"][0], "kind": "REPORTING"}]}, unit)
        with self.assertRaisesRegex(explain.Unclassified, "does not occur"):
            explain.validate_unit({**answer, "dates": [{**answer["dates"][0], "date": "1. Juli 2027"}]}, unit)
        for field, value, message in (("what_changed", "Siehe [N1] für Details zur Frist.", "markers"),
                                      ("significance", "EDITORIAL", "EDITORIAL needs")):
            with self.assertRaisesRegex(explain.Unclassified, message):
                explain.validate_unit({**answer, field: value}, unit)

    def test_long_text_keeps_the_changed_passage(self):
        same = " ".join(f"Sentence {n} stays the same in both versions of the text." for n in range(300))
        old, new = same + " The limit is 100 TJ.", same + " The limit is 85 TJ."
        flags_old, flags_new = explain.changed_flags(old, new)
        passages = explain.evidence_passages(new, "N", flags_new, budget=2000)
        self.assertTrue(any("85 TJ" in p["text"] and p["changed"] for p in passages))
        self.assertLess(sum(len(p["text"]) for p in passages), 2100)

    def test_corrigendum_values_and_wording_only(self):
        unit = unit_of(RECORDS[4])
        self.assertEqual((unit["before_ids"], unit["after_ids"]), (["W1"], ["R1"]))
        answer = {"headline": "Schwellenwert berichtigt", "what_was_wrong": "Statt 100 TJ muss es 85 TJ heißen.",
                  "facts": [{"what": "Schwellenwert", "before": "100 TJ", "after": "85 TJ", "before_evidence": ["W1"],
                             "after_evidence": ["R1"]}], "affects": "NUMBER", "importance": "", "to_confirm": ""}
        self.assertEqual(explain.validate_unit(answer, unit)["facts"][0]["before_evidence"][0]["text"], "100 TJ")
        with self.assertRaisesRegex(explain.Unclassified, "never WORDING_ONLY"):
            explain.validate_unit({**answer, "affects": "WORDING_ONLY"}, unit)

    def test_importance_needs_a_precise_route(self):
        item = next(i for i in explain.build_items(RECORDS) if explain.item_facts(i)["provision"] == "Article 3")
        routing = explain.route(explain.item_facts(item), RESULTS, explain.locator_index(RESULTS))
        unit = explain.build_unit(item, item["records"][0], routing)
        self.assertIn("Umweltmanagement", unit["sent"]["why_relevant"])
        self.assertEqual(explain.unit_schema(unit)["properties"]["importance"]["maxLength"], 500)
        broad = explain.build_unit(item, item["records"][0], {**routing, "route": "CHANGING_ACT"})
        self.assertNotIn("why_relevant", broad["sent"])
        answer = answer_for([None, {"content": json.dumps(unit["sent"]) + "\n\nTASK: x"}])
        self.assertIn("Max Bögl", explain.validate_unit(answer, unit)["importance"])
        with self.assertRaisesRegex(explain.Unclassified, "conditionally"):
            explain.validate_unit({**answer, "importance": "Max Bögl muss die Genehmigung anpassen."}, unit)

    def test_failure_kinds(self):
        self.assertEqual(explain.failure_kind("Invalid answer: 'erweitert' compares with an old wording"),
                         "COMPARISON_WITHOUT_OLD")
        self.assertEqual(explain.failure_kind("The evidence for point (b) does not name works contracts."), "SUBJECT")
        self.assertEqual(explain.failure_kind("In after of fact 1: The number 131 does not occur in the text."),
                         "UNSUPPORTED_NUMBER")


class PartTests(unittest.TestCase):
    def test_partially_failed_annex_keeps_good_parts_and_retries_only_the_failed_one(self):
        annex = [provision("a1", "I", "REPLACE", None, "Point 1. Limit 10 mg.", "(4) Annex I point 1 is replaced.",
                           "NEW_ONLY", 1, place="Annex I point 1", root_kind="annex"),
                 provision("a2", "I", "REPLACE", None, "Point 2. Limit 20 mg.", "(4) Annex I point 2 is replaced.",
                           "NEW_ONLY", 2, place="Annex I point 2")]
        for record in annex:
            record["root"] = ["annex", "i"]
        item = explain.build_items(annex)[0]
        bad = {"headline": "Grenzwert auf 30 mg erhöht", "what_changed": "Der Grenzwert steigt auf 30 mg.",
               "facts": [], "dates": [], "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""}

        def answers(messages):
            sent = json.loads(messages[1]["content"].partition("\n\nTASK:")[0])
            return bad if sent["place"]["place"] == "Annex I point 2" else answer_for(messages)
        systems = {"change": "system", "corrigendum": "system"}
        with TemporaryDirectory() as folder:
            parts = Path(folder)
            name, explanation = explain.explain(ready_client(answer=answers), systems, item, [], {}, parts)
            self.assertEqual((explanation["outcome"], explanation["missing_changes"]), ("PARTIAL", ["Annex I point 2"]))
            self.assertEqual([p["status"] for p in explanation["parts"]], ["OK", "FAILED"])
            failed = json.loads((parts / "2.json").read_text(encoding="utf-8"))
            self.assertEqual(failed["status"], "FAILED")
            self.assertEqual(len(failed["attempts"]), 2)                # first and corrected answer, both saved
            self.assertEqual(failed["attempts"][0]["answer"]["headline"], "Grenzwert auf 30 mg erhöht")
            self.assertIn("does not occur", failed["attempts"][1]["error"])
            self.assertEqual(failed["sent"]["place"]["place"], "Annex I point 2")   # the evidence actually sent
            client = ready_client()
            name, explanation = explain.explain(client, systems, item, [], {}, parts)
            self.assertEqual(len(client.calls), 1)                      # only the failed part is asked again
            self.assertEqual(explanation["outcome"], "EXPLAINED")
            self.assertEqual(len(explanation["facts"]), 2)


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

    def test_full_run_counts_outcomes_and_shows_evidence(self):
        self.assertEqual(self.run_main(Client()), 0)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["outcomes"], {"EXPLAINED": 4, "PARTIAL": 0, "FAILED": 0, "MISSING_EVIDENCE": 1,
                                               "SCOPE_REVIEW": 0, "OPEN": 0})
        self.assertEqual(summary["parts"], {"OK": 5})
        page = (self.out / "preview" / "Umweltmanagement.html").read_text(encoding="utf-8")
        self.assertIn("CORRECT c1:R1: „85 TJ“", page)                   # the quotation copied by Python
        with (self.out / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertIn("outcome", rows[0])
        self.assertEqual(len(rows), 6)

    def test_failed_item_is_retried_with_diagnostics(self):
        wrong = reply({"headline": "x", "what_changed": "Der Grenzwert sinkt auf 70 TJ.", "facts": [], "dates": [],
                       "significance": "SUBSTANTIVE", "importance": "", "to_confirm": ""})
        self.assertEqual(self.run_main(Client(injected=[wrong, wrong])), 2)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["outcomes"]["FAILED"] + summary["outcomes"]["PARTIAL"], 1)
        self.assertEqual(summary["failed_part_kinds"], {"UNSUPPORTED_NUMBER": 1})
        diagnosis = json.loads((self.out / summary["failed_part_files"][0]).read_text(encoding="utf-8"))
        self.assertEqual([a["answer"]["what_changed"] for a in diagnosis["attempts"]], ["Der Grenzwert sinkt auf 70 TJ."] * 2)
        self.assertEqual(self.run_main(Client()), 0)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["outcomes"]["FAILED"], summary["outcomes"]["PARTIAL"]), (0, 0))

    def test_replay_by_item_ids_and_implicit_repeal_in_review(self):
        implicit = {**RECORDS[-1], "relation": "IMPLICITLY_REPEALS", "operation": "IMPLICITLY_REPEALS"}
        (self.root / "export" / "records.jsonl").write_text(json.dumps(implicit), encoding="utf-8")
        ids = self.root / "ids.txt"
        ids.write_text("a1\n", encoding="utf-8")
        client = Client()
        self.assertEqual(self.run_main(client, "--item-ids", str(ids)), 0)
        self.assertEqual(client.calls, [])
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["outcomes"]["SCOPE_REVIEW"], 1)
        self.assertIn("REPEAL_SCOPE_UNVERIFIED", (self.out / "to_screen.csv").read_text(encoding="utf-8-sig"))

    def test_count_only_and_sample(self):
        client = Client()
        self.assertEqual(self.run_main(client, "--count-only"), 0)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.run_main(Client(), "--sample", "3"), 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text(encoding="utf-8"))["items_planned"], 3)


if __name__ == "__main__":
    unittest.main()
