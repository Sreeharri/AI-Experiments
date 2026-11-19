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
sys.path.insert(0, str(ROOT / "tests"))
import summarize_eurlex_provisions as summariser  # noqa: E402
from test_classify_eurlex_provisions import client_with, make_run, pack, record  # noqa: E402

TEXT = "Enterprises using more than 85 TJ of energy per year shall carry out an energy audit every four years."


def reply(value):
    return ChatResult(ok=True, finish_reason="stop", content=json.dumps(value))


def provision(*bullets, headline="Energy audits"):
    bullets = bullets or (("DUTY", "Enterprises above 85 TJ must carry out an energy audit.", ["P1"]),)
    return reply({"headline": headline, "overview": "Large energy users must have their energy use audited.",
                  "bullets": [{"kind": k, "text": t, "passages": p} for k, t, p in bullets]})


def act(*sentences):
    return reply({"sentences": list(sentences or ("The act sets rules on energy efficiency.", "Enterprises must carry out energy audits."))})


def card(*bullets):
    bullets = bullets or (("WHY_DEPARTMENT", "The provisions concern energy audits.", ["Article 1"]),)
    return reply({"bullets": [{"kind": k, "text": t, "provisions": p} for k, t, p in bullets]})


def classified(domain="energy_sustainability", departments=("Energiesysteme", "Konzernrechnungswesen"), **more):
    return {"level": "DIRECT", "subject": "energy audits", "primary_domain": domain,
            "primary_title": "Energie & Nachhaltigkeit", "primary_reason": "It regulates energy audits.",
            "secondary_domain": None, "secondary_title": "", "secondary_reason": "",
            "departments": list(departments), "secondary_departments": [], "check": [], **more}


class NumberCheckTests(unittest.TestCase):
    SOURCE = "The limit is 1 000 kg and 85 TJ, by 31 December 2027. Values: 300 800 kWh/t. Article 11(2)."

    def test_numbers_from_the_text_pass_in_any_notation(self):
        summariser.check_numbers(["Grenze 1.000 kg", "1000 kg", "1 000 kg", "bis 31. Dezember 2027", "ab 85 TJ",
                                  "300 kWh", "Artikel 11 Absatz 2", "ohne Zahl"], self.SOURCE)

    def test_invented_numbers_are_rejected(self):
        for text in ("bis 2028", "ab 90 TJ", "innerhalb von 30 Tagen"):
            with self.assertRaisesRegex(summariser.Unclassified, "does not occur"):
                summariser.check_numbers([text], self.SOURCE)

    def test_dates_must_match_day_month_and_year(self):
        summariser.check_numbers(["bis 31.12.2027", "bis zum 31. Dezember 2027", "bis Dezember 2027",
                                  "by December 31, 2027", "bis 2027-12-31"], self.SOURCE)
        for text in ("bis 31.01.2027", "bis 31. Januar 2027", "bis 30. Dezember 2027", "bis Januar 2027"):
            with self.assertRaisesRegex(summariser.Unclassified, "date"):
                summariser.check_numbers([text], self.SOURCE)

    def test_decimal_values_and_units_are_kept(self):
        source = "Plants above 1.5 MW and 10 % of EUR 2 500 must report."
        summariser.check_numbers(["über 1,5 MW", "über 1.5 MW", "10 Prozent", "2.500 EUR", "EUR 2 500"], source)
        for text, message in (("über 15 MW", "1.5 is not 15"), ("über 1,5 kW", "with this unit"),
                              ("10 kg", "with this unit"), ("25.000 EUR", "does not occur")):
            with self.assertRaisesRegex(summariser.Unclassified, message):
                summariser.check_numbers([text], source)


class ProvisionTests(unittest.TestCase):
    def test_summary_comes_from_the_text_alone_and_keeps_the_quoted_passage(self):
        client = client_with(provision())
        saved = record("A", "Article 1", text=TEXT)
        summary, audit = summariser.summarise_provision(client, "SYSTEM", saved)
        self.assertEqual(summary["headline"], "Energy audits")
        self.assertEqual(summary["bullets"][0]["quotes"], [TEXT])
        self.assertIn("audited", summary["overview"])
        self.assertEqual(summary["bullets"][0]["part"], "1")
        messages = client.chat.call_args.args[0]
        evidence, _, task = messages[1]["content"].partition("\n\nTASK:")
        self.assertIn("[P1] Enterprises using", json.loads(evidence)["target"]["text"])
        self.assertNotIn("Screening reason", evidence)
        self.assertIn("Summarise this provision", task)
        schema = client.chat.call_args.kwargs["schema"]
        self.assertEqual(schema["properties"]["bullets"]["items"]["properties"]["passages"]["items"]["enum"], ["P1"])
        self.assertEqual(audit["parts_total"], 1)

    def test_an_invented_number_is_accepted_and_only_marked(self):
        wrong = provision(("DEADLINE", "The audit is due within 30 days.", ["P1"]))
        client = client_with(wrong)
        summary, audit = summariser.summarise_provision(client, "S", record("A", "Article 1", text=TEXT))
        self.assertEqual(summary["bullets"][0]["text"], "The audit is due within 30 days.")
        self.assertIn("number 30", summary["bullets"][0]["number_check"][0])
        self.assertEqual(len(audit["requests"]), 1)                       # no correction request

    def test_micro_sign_and_greek_mu_do_not_crash_the_check(self):
        from eurlex_summary_checks import number_problems

        source = "The limit value is 5 µg/m3 and 2 μg per litre."
        self.assertEqual(number_problems(["Höchstens 5 µg/m3 und 2 μg je Liter."], source), [])
        self.assertTrue(number_problems(["Höchstens 5 µg/m3."], "The limit value is 5 mg/m3."))

    def test_the_answer_is_taken_as_it_is_only_an_unreadable_one_is_asked_again(self):
        passages, source = {"P1": TEXT}, TEXT
        long_text = "Unternehmen müssen ein Energieaudit durchführen, " * 30
        answer = {"headline": "H" * 300, "overview": "", "bullets": [
            {"kind": "DUTY", "text": long_text, "passages": ["P1"]},
            {"kind": "WHY", "text": "Unknown kind.", "passages": ["P9"]}]}
        result = summariser.validate_provision(answer, passages, source)
        self.assertEqual(result["headline"], "H" * 300)                     # nothing is cut
        self.assertEqual(result["bullets"][0]["text"], summariser.squash(long_text))
        self.assertEqual((result["bullets"][1]["kind"], result["bullets"][1]["passages"]), ("OTHER", []))
        with self.assertRaises(summariser.Unclassified):
            summariser.validate_provision({"text": "x"}, passages, source)
        schema = json.dumps(summariser.provision_schema(["P1"]))
        self.assertNotIn("maxLength", schema)
        unreadable = ChatResult(ok=True, finish_reason="stop", content="{not json")
        summary, audit = summariser.summarise_provision(client_with(unreadable, provision()), "S",
                                                        record("A", "Article 1", text=TEXT))
        self.assertEqual((summary["headline"], len(audit["requests"])), ("Energy audits", 2))

    def test_a_bullet_gets_the_passage_that_holds_its_number(self):
        passages = {"P1": "Enterprises using more than 85 TJ shall carry out an energy audit.",
                    "P2": "The audit shall be repeated every four years."}
        source = "Directive (EU) 2023/1791, existing plants before 27 November 2002. " + " ".join(passages.values())
        reply_ = {"headline": "Audits", "overview": "Large users are audited.",
                  "bullets": [{"kind": "CONDITION", "text": "Applies above 85 TJ.", "passages": ["P2"]}]}
        bullet = summariser.validate_provision(reply_, passages, source, "Article 11")["bullets"][0]
        self.assertEqual((bullet["passages"], "number_outside_passages" in bullet), (["P2", "P1"], False))
        reply_["bullets"] = [{"kind": "OTHER", "text": "Plants before 27.11.2002 count as existing.", "passages": ["P2"]}]
        bullet = summariser.validate_provision(reply_, passages, source, "Article 11")["bullets"][0]
        self.assertTrue(bullet["number_outside_passages"])        # the date stands in the text, outside the passages
        reply_["bullets"] = [{"kind": "OTHER", "text": "Plants before 27.11.2001 count as existing.", "passages": ["P2"]}]
        bullet = summariser.validate_provision(reply_, passages, source, "Article 11")["bullets"][0]
        self.assertIn("27.11.2001", bullet["number_check"][0])     # accepted, only marked

    def test_cases_from_the_full_run_pass_and_real_changes_still_fail(self):
        summariser.check_numbers(["ab dem 1.1.1993"], "as from 1st January 1993")
        summariser.check_numbers(["2.400 t für Deutschland"], "Quantity (tonnes): Germany 2 400, France 950")
        summariser.check_numbers(["gemäß 1.4.1.4", "nach den Nummern 2 bis 4 und Absätzen 2, 3 und 12"], "point 1.4.1.4")
        for text, source in (("über 1,5 kW", "Plants above 1.5 MW"), ("2.500 t", "Quantity (tonnes): 2 400"),
                             ("bis 31.01.2027", "by 31 December 2027"), ("mindestens 12 Monate", "no numbers")):
            with self.assertRaises(summariser.Unclassified):
                summariser.check_numbers([text], source)

    def test_references_to_parts_and_acts_are_not_numbers_to_check(self):
        summariser.check_numbers(["nach Artikel 15 Absatz 4", "Verordnung (EG) Nr. 451/2008", "Anhang II Teil 3",
                                  "Richtlinie 2008/98/EG", "Angaben nach Absatz 1 Buchstabe a"], "No numbers here.")
        with self.assertRaisesRegex(summariser.Unclassified, "number 30"):
            summariser.check_numbers(["innerhalb von 30 Tagen nach Artikel 5"], "No numbers here.")

    def test_a_part_too_long_for_the_model_is_read_in_pieces_and_joined(self):
        text = " ".join(f"Sentence about the energy audit of enterprises, item {n}." for n in range(180))
        saved = record("A", "Annex I", text=text)
        self.assertEqual(len(summariser.split_target(saved["evidence_pack"]["provision"], summariser.TARGET_CHARS)), 1)
        too_long = ChatResult(ok=False, error_kind="context_limit", error="too long")
        merged = reply({"headline": "Energy audit rules", "overview": "Covers both pieces."})
        client = client_with(too_long, provision(("DUTY", "First piece duty.", ["P1"])),
                             provision(("CONDITION", "Second piece condition.", ["P1"])), merged)
        summary, audit = summariser.summarise_provision(client, "S", saved, "MERGE")
        self.assertEqual(audit["parts"], ["1.1", "1.2"])
        self.assertEqual(summary["not_read"] if "not_read" in summary else [], [])
        self.assertEqual((summary["headline"], summary["overview"]), ("Energy audit rules", "Covers both pieces."))
        self.assertEqual([b["kind"] for b in summary["bullets"]], ["DUTY", "CONDITION"])
        self.assertEqual(client.chat.call_args.args[0][0]["content"], "MERGE")
        alone = record("A", "Annex I", text="A short text that the model still refuses to read in this test.")
        with self.assertRaisesRegex(summariser.Unclassified, "No part"):
            summariser.summarise_provision(client_with(too_long), "S", alone)

    def test_only_the_target_is_sent_not_the_surrounding_articles(self):
        saved = record("A", "Article 1", text=TEXT)
        saved["evidence_pack"]["essential_context"] = [{"locator": "Article 2", "text": "Article 2 says other things."}]
        saved["evidence_pack"]["direct_references"] = [{"locator": "Annex II", "text": "Annex II procedure."}]
        client = client_with(provision())
        summariser.summarise_provision(client, "S", saved)
        sent = json.loads(client.chat.call_args.args[0][1]["content"].partition("\n\nTASK:")[0])
        self.assertEqual(set(sent) - {"source_notes"}, {"act", "target"})

    def test_an_answer_in_another_language_is_accepted(self):
        english = provision(("DUTY", "Enterprises must carry out an energy audit of the site.", ["P1"]))
        with patch.object(summariser, "OUTPUT_LANGUAGE", "de"):
            summary, audit = summariser.summarise_provision(client_with(english), "S", record("A", "Article 1", text=TEXT))
        self.assertEqual((len(summary["bullets"]), len(audit["requests"])), (1, 1))
        sentences = ["Die Richtlinie regelt Energieaudits.", "Es gibt keine konkrete Frist."]
        self.assertEqual(summariser.without_empty_statements(sentences, lambda x: x), sentences[:1])

    def test_a_provision_without_words_is_not_sent(self):
        client = client_with()
        summary, audit = summariser.summarise_provision(client, "S", record("A", "Annex VII", text="(7-146)"))
        client.chat.assert_not_called()
        self.assertTrue(summary["text_missing"])
        self.assertEqual((summary["bullets"], audit["requests"]), ([], []))

    def test_long_provision_reads_only_kept_parts_and_balances_the_bullets(self):
        text = " ".join(f"Sentence number {n} about the energy audit of enterprises." for n in range(1500))
        saved = record("A", "Annex I", text=text)
        total = len(summariser.split_target(saved["evidence_pack"]["provision"], summariser.TARGET_CHARS))
        saved["model_attempt"] = {"parts": [{"part": str(n), "decision": {"level": "DIRECT" if n in (2, 4) else "IGNORE"}}
                                            for n in range(1, total + 1)]}
        first = provision(("DUTY", "Duty one.", ["P1"]), ("DUTY", "Duty two.", ["P1"]), ("DUTY", "Duty three.", ["P1"]))
        second = provision(("WHO", "Employers.", ["P1"]), ("DEADLINE", "Every year.", ["P1"]),
                           ("CONDITION", "Only large sites.", ["P1"]))
        merge = reply({"headline": "Whole annex", "overview": "Covers parts 2 and 4."})
        client = client_with(first, second, merge)
        summary, audit = summariser.summarise_provision(client, "S", saved)
        self.assertEqual(audit["parts"], ["2", "4"])
        self.assertEqual([b["kind"] for b in summary["bullets"]], ["WHO", "DUTY", "DEADLINE", "CONDITION"])
        self.assertEqual([b["text"] for b in summary["more_bullets"]], ["Duty two.", "Duty three."])
        self.assertEqual((summary["parts_read"], summary["parts_total"]), (["2", "4"], total))
        self.assertEqual({item["reason"] for item in summary["not_read"]}, {"NOT_RELEVANT"})
        self.assertEqual(len(summary["not_read"]), total - 2)
        self.assertEqual(summary["overview"], "Covers parts 2 and 4.")
        note = summariser.parts_note(summary, "de")
        self.assertIn("Teil 2, 4 von", note)
        self.assertEqual(note.count("bei der Prüfung nicht relevant"), 1)     # one line, not one per part
        sent = json.loads(client.chat.call_args_list[0].args[0][1]["content"].partition("\n\nTASK:")[0])
        self.assertEqual(sent["target"]["part"], f"2 of {total}")

    def test_connection_problem_is_not_a_summary(self):
        lost = ChatResult(ok=False, error_kind="timeout", error="Timed out")
        with self.assertRaises(summariser.ConnectionLost):
            summariser.summarise_provision(client_with(lost), "S", record("A", "Article 1", text=TEXT))


class TidyTests(unittest.TestCase):
    """Rules applied to saved summaries when the cards are written; Qwen is not asked again."""

    def bullets(self, *pairs):
        return {"headline": "H", "overview": "O", "bullets": [{"kind": k, "text": t} for k, t in pairs]}

    def test_a_definition_is_not_a_duty_but_a_real_duty_in_it_stays(self):
        text = "Article 2 Definitions For the purposes of this Regulation, 'vehicle' means any motor vehicle."
        summary = summariser.tidy_summary(self.bullets(
            ("DUTY", "Der Begriff 'Fahrzeug' bezeichnet jedes Kraftfahrzeug."),
            ("DUTY", "Hersteller müssen das Fahrzeug kennzeichnen.")), text)
        self.assertEqual([b["kind"] for b in summary["bullets"]], ["DUTY", "OTHER"])
        self.assertEqual(summary["tidied"], {"relabelled_to_other": 1, "removed": 0})

    def test_a_duty_that_only_describes_the_act_becomes_a_note(self):
        summary = summariser.tidy_summary(self.bullets(
            ("DUTY", "Die Entscheidung legt technische Bedingungen für das Frequenzband fest."),
            ("DUTY", "Betreiber müssen die Bedingungen einhalten.")), "Article 1 This Decision establishes ...")
        self.assertEqual([b["kind"] for b in summary["bullets"]], ["DUTY", "OTHER"])

    def test_only_empty_statements_are_left_out(self):
        summary = summariser.tidy_summary(self.bullets(
            ("DUTY", "Betreiber müssen jährlich berichten."),
            ("DEADLINE", "Es wird kein konkreter Zeitpunkt genannt."),
            ("DEADLINE", "Es wird kein konkreter Zeitpunkt genannt, aber die Geldstrafe wird sofort fällig."),
            ("DEADLINE", "Die Steuer gilt ab Inkrafttreten. Es wird kein spezifischer Zeitpunkt genannt.")), "Article 3")
        self.assertEqual(len(summary["bullets"]), 3)
        self.assertEqual(summary["tidied"]["removed"], 1)
        only = summariser.tidy_summary(self.bullets(("DEADLINE", "Es wird kein konkreter Zeitpunkt genannt.")), "x")
        self.assertEqual(len(only["bullets"]), 1)                      # at least one point stays

    def test_untouched_summary_has_no_note_and_language_rows_are_found(self):
        summary = self.bullets(("DUTY", "Betreiber müssen jährlich berichten."))
        self.assertEqual(summariser.tidy_summary(summary, "Article 3 Operators shall report."), summary)
        self.assertTrue(summariser.is_other_language_row({"text": "‘HR U dodiru s kiselinama oslobađa plin.’"}))
        self.assertFalse(summariser.is_other_language_row({"text": "Article 5 Member States shall"}))
        self.assertFalse(summariser.is_other_language_row({"text": "‘EN In contact with acids ...’"}))


class ActAndCardTests(unittest.TestCase):
    PROVISIONS = [
        {"locator": "Article 1", "provision_id": "A:1", "level": "INDIRECT", "role": "primary",
         "domain": "energy_sustainability", "domain_title": "Energie & Nachhaltigkeit", "domain_reason": "Energy.",
         "why_relevant": "Affects suppliers.", "indirect_path": "suppliers_prices", "to_confirm": "",
         "headline": "Scope", "bullets": [{"kind": "WHO", "text": "Enterprises."}], "check": []},
        {"locator": "Article 2", "provision_id": "A:2", "level": "DIRECT", "role": "primary",
         "domain": "energy_sustainability", "domain_title": "Energie & Nachhaltigkeit", "domain_reason": "Audits.",
         "why_relevant": "Max Bögl is above 85 TJ.", "indirect_path": None, "to_confirm": "Is the use above 85 TJ?",
         "headline": "Audits", "bullets": [{"kind": "DUTY", "text": "Audit every 4 years."}], "check": []},
    ]

    def test_act_summary_is_taken_as_written(self):
        client = client_with(act())
        sentences, _ = summariser.summarise_act(client, "S", "A", "Directive 2023/1791", "energy efficiency",
                                                [{"locator": "Article 1", "headline": "Scope", "points": ["Enterprises."]}])
        self.assertEqual(len(sentences), 2)
        sent = json.loads(client.chat.call_args.args[0][1]["content"].partition("\n\nTASK:")[0])
        self.assertEqual(list(sent), ["act", "act_subject", "provisions_total", "provisions"])
        new = act("It sets rules.", "It applies from 2030.")
        self.assertEqual(summariser.summarise_act(client_with(new), "S", "A", "T", "x", [])[0][1], "It applies from 2030.")

    def test_card_input_puts_direct_first_and_limits_the_list(self):
        sent = summariser.card_input(["Energiesysteme"], "A", "T", ["S."], self.PROVISIONS)
        self.assertEqual([p["locator"] for p in sent["provisions"]], ["Article 2", "Article 1"])
        self.assertEqual(sent["provisions"][0]["bullets"], ["Audit every 4 years."])
        many = [{**self.PROVISIONS[0], "locator": f"Article {n}"} for n in range(40)]
        sent = summariser.card_input(["X"], "A", "T", [], many)
        self.assertEqual((len(sent["provisions"]), sent["more_provisions"]), (15, 25))

    def test_department_summary_is_taken_as_written(self):
        sent = summariser.card_input(["Energiesysteme"], "A", "T", ["S."], self.PROVISIONS)
        good = card(("ATTENTION", "Read Article 2 first.", ["Article 2"]),
                    ("WHY_MAX_BOEGL", "Suppliers are affected.", ["Article 1"]),
                    ("TO_CONFIRM", "Is the use above 85 TJ?", ["Article 2"]),
                    ("ATTENTION", "Audit within 90 days.", ["Article 2"]))
        bullets, log = summariser.summarise_department(client_with(good), "S", sent)
        self.assertEqual([b["kind"] for b in bullets], ["WHY_MAX_BOEGL", "ATTENTION", "ATTENTION", "TO_CONFIRM"])
        self.assertEqual(len(log), 1)
        self.assertIn("number 90", [b for b in bullets if "90" in b["text"]][0]["number_check"][0])
        with self.assertRaises(summariser.Unclassified):
            summariser.validate_department({"bullets": []}, sent["provisions"], "")

    def test_departments_with_the_same_provisions_share_one_card(self):
        records = [{"target": "Article 1", "target_id": "A:1", "level": "DIRECT", "classification": classified(),
                    "facts": {"why_relevant": "r", "indirect_path": None, "to_confirm": ""},
                    "summary": {"headline": "H", "overview": "O.", "bullets": []}},
                   {"target": "Article 2", "target_id": "A:2", "level": "DIRECT",
                    "classification": classified("customs_trade", ()),
                    "facts": {"why_relevant": "r", "indirect_path": None, "to_confirm": ""},
                    "summary": {"headline": "H", "overview": "O.", "bullets": []}}]
        groups = list(summariser.groups_of("A", records).values())
        self.assertEqual(sorted(g["departments"] for g in groups),
                         [[summariser.NO_DEPARTMENT], ["Energiesysteme", "Konzernrechnungswesen"]])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.run_folder = make_run(self.root, "first", [[
            record("A", "Article 1", text=TEXT), record("A", "Article 2", level="INDIRECT", text=TEXT),
            record("B", "Article 1", text=TEXT), record("B", "Article 9", level="IGNORE", text=TEXT)]])
        self.cls = self.root / "cls"
        self.cls.mkdir()
        (self.cls / "results.json").write_text(json.dumps({
            "A": {"Article 1": classified(), "Article 2": classified(level="INDIRECT", to_confirm="Imports itself?")},
            "B": {"Article 1": classified("customs_trade", ())}}), encoding="utf-8")
        (self.cls / "acts.json").write_text(json.dumps({
            "A": {"title": "Test act A", "act_domain": "energy_sustainability", "act_subject": "energy audits"}}),
            encoding="utf-8")
        self.out = self.root / "out"

    def main(self, client, *options):
        if "--language" not in options:
            options = ("--language", "en", *options)   # the stand-in answers are English
        argv = ["summarise", "--runs", str(self.run_folder), "--unsure-dir", str(self.root / "none"),
                "--classification-dir", str(self.cls), "--output-dir", str(self.out), "--workers", "1", *options]
        with patch.object(sys, "argv", argv), patch.object(summariser.QwenClient, "from_env", return_value=client), \
                patch("classify_eurlex_provisions.time.sleep") as sleep:
            code = summariser.main()
        return code, sleep

    def test_full_run_builds_cards_preview_and_review_list(self):
        client = client_with(provision(), provision(headline="Scope"), provision(),     # A1, A2, B1
                             act(), act(),                                                # acts A, B
                             card(("WHY_DEPARTMENT", "Energy audits concern you.", ["Article 1"]),
                                  ("TO_CONFIRM", "Imports itself?", ["Article 2"])))      # one card for act A
        code, _ = self.main(client)
        self.assertEqual(code, 0)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["provisions_summarised"], summary["provisions_failed"], summary["acts"],
                          summary["cards"], summary["cards_without_department_summary"]), (3, 0, 2, 3, 0))
        inbox = json.loads((self.out / "inbox" / "Energiesysteme.json").read_text(encoding="utf-8"))
        first = inbox["cards"][0]
        self.assertEqual((first["celex"], first["title"], first["direct"], first["indirect"]), ("A", "Test act A", 1, 1))
        self.assertTrue(first["eurlex_url"].endswith("CELEX:A"))
        self.assertEqual([b["kind"] for b in first["department_summary"]], ["WHY_DEPARTMENT", "TO_CONFIRM"])
        self.assertEqual([p["locator"] for p in first["provisions"]], ["Article 1", "Article 2"])
        self.assertEqual(first["provisions"][1]["to_confirm"], "Imports itself?")
        self.assertEqual(first["provisions"][0]["bullets"][0]["quotes"], [TEXT])
        self.assertEqual(first["note"], summariser.NOTE["en"])
        same = json.loads((self.out / "inbox" / "Konzernrechnungswesen.json").read_text(encoding="utf-8"))
        self.assertEqual(same["cards"][0]["department_summary"], first["department_summary"])
        nobody = json.loads((self.out / "inbox" / "no_department_yet.json").read_text(encoding="utf-8"))
        self.assertEqual((nobody["cards"][0]["celex"], nobody["cards"][0]["department_summary"]), ("B", []))
        page = (self.out / "preview" / "Energiesysteme.html").read_text(encoding="utf-8")
        self.assertIn("Energy audits concern you.", page)
        self.assertIn("Directly relevant", page)
        self.assertNotIn("Task", page)
        self.assertIn("Energiesysteme.html", (self.out / "preview" / "index.html").read_text(encoding="utf-8"))
        with (self.out / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertEqual((len(rows), rows[0][3], rows[0][4]), (4, "headline", "overview"))
        self.assertIn("department right (yes/no)", rows[0])
        self.assertIn("Energiesysteme", rows[1][rows[0].index("departments")])
        self.assertIn("must have their energy use audited", page)
        again = client_with()
        self.assertEqual(self.main(again)[0], 0)
        again.chat.assert_not_called()

    def test_count_only_levels_and_extra_act(self):
        client = client_with()
        self.assertEqual(self.main(client, "--count-only")[0], 0)
        client.chat.assert_not_called()
        client = client_with(provision(), provision(), act(), act(), card())
        self.assertEqual(self.main(client, "--levels", "DIRECT")[0], 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_summarised"], 2)

    def test_sample_plus_named_act(self):
        client = client_with(provision(), provision(), provision(), act(), act(), card())
        code, _ = self.main(client, "--sample", "1", "--celex", "A", "B")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_summarised"], 3)

    def test_outage_waits_and_failed_items_are_retried_on_the_next_start(self):
        lost = ChatResult(ok=False, error_kind="connection", error="WinError 10060")
        unreadable = ChatResult(ok=True, finish_reason="stop", content="{not json")
        client = client_with(provision(), lost, unreadable, unreadable, provision(), act(), act(), card())
        client.discover_results = [{"ok": True}, {"ok": False, "error": "No model list found."}, {"ok": True}]
        code, sleep = self.main(client)
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] / 60 for call in sleep.call_args_list], [15])
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual((summary["provisions_summarised"], summary["provisions_failed"]), (2, 1))
        client = client_with(provision(headline="Scope"), act(), card())   # the failed provision, its act and card again
        self.assertEqual(self.main(client)[0], 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_failed"], 0)

    def test_only_cut_provisions_and_faulty_act_summaries_are_written_again(self):
        self.main(client_with(provision(), provision(), provision(), act(), act(), card()))
        for name in ("A", "B"):                                            # act summaries of v4.5: no version
            path = self.out / "acts" / f"{name}.json"
            saved = json.loads(path.read_text(encoding="utf-8"))
            saved.pop("version")
            path.write_text(json.dumps(saved), encoding="utf-8")
        files = sorted((self.out / "provisions").rglob("*.json"))
        first = json.loads(files[0].read_text(encoding="utf-8"))
        first["summary"]["bullets"][0]["text"] = CutOffTests.LONG          # as written by v4.5: cut, no mark
        first.pop("redo", None)
        files[0].write_text(json.dumps(first), encoding="utf-8")
        redo = client_with(provision(headline="New"))                     # only the cut provision
        self.assertEqual(self.main(redo)[0], 0)
        self.assertEqual(redo.chat.call_count, 1)
        self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["summary"]["headline"], "New")
        self.assertEqual(self.main(client_with())[0], 0)                   # asked once, not again
        path = self.out / "acts" / "B.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["sentences"][-1] = "Enterprises must carry out energy aud"
        path.write_text(json.dumps(saved), encoding="utf-8")
        again = client_with(act())                                        # only act B, A stays
        self.assertEqual(self.main(again)[0], 0)
        self.assertEqual(again.chat.call_count, 1)

    def test_a_failed_new_request_keeps_the_earlier_summary(self):
        self.main(client_with(provision(), provision(), provision(), act(), act(), card()))
        files = sorted((self.out / "provisions").rglob("*.json"))
        first = json.loads(files[0].read_text(encoding="utf-8"))
        first["summary"]["bullets"][0]["text"] = CutOffTests.LONG          # as written by v4.5: cut, no mark
        first.pop("redo", None)
        files[0].write_text(json.dumps(first), encoding="utf-8")
        bad = ChatResult(ok=True, finish_reason="stop", content="{not json")
        self.assertEqual(self.main(client_with(bad, bad))[0], 0)
        kept = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(kept["summary"]["bullets"][0]["text"], CutOffTests.LONG)
        self.assertIn("redo_failure", kept)
        self.assertEqual(self.main(client_with())[0], 0)

    def test_changed_language_needs_a_new_folder_and_missing_classification_is_reported(self):
        self.main(client_with(provision(), provision(), provision(), act(), act(), card()))
        self.assertEqual(self.main(client_with(), "--language", "de")[0], 1)
        argv = ["summarise", "--runs", str(self.run_folder), "--classification-dir", str(self.root / "nothing"),
                "--output-dir", str(self.out)]
        with patch.object(sys, "argv", argv):
            self.assertEqual(summariser.main(), 1)


def table_reply(headline="Hazard statements for dangerous substances", *bullets):
    bullets = bullets or (("OTHER", "The tables give the hazard statements for each substance."),)
    return reply({"headline": headline, "overview": "The tables list hazard statements and their codes.",
                  "bullets": [{"kind": k, "text": t} for k, t in bullets]})


def saved(target, level="INDIRECT", headline="H", points=("A point.",)):
    return {"target": target, "target_id": f"X:{target}", "level": level,
            "summary": {"headline": headline, "overview": "O.", "bullets": [{"kind": "OTHER", "text": p} for p in points]}}


class ActTypeTests(unittest.TestCase):
    def test_type_and_number_come_from_the_celex_number(self):
        self.assertEqual(summariser.act_type("32015D0975", "de"), "Beschluss")
        self.assertEqual(summariser.act_type("31987D0182", "de"), "Entscheidung")
        self.assertEqual(summariser.act_type("32011R0305", "de"), "Verordnung")
        self.assertEqual(summariser.act_type("32023L1791", "en"), "Directive")
        self.assertIsNone(summariser.act_type("A", "de"))
        self.assertEqual(summariser.act_numbers("32015D0975")[:2], ["2015/975", "15/975"])

    def test_a_wrong_type_word_is_found_and_the_right_one_passes(self):
        find = summariser.wrong_type_word
        self.assertEqual(find(["Die EU-Verordnung 2015/975 betrifft einen Schlagbohrer."], "32015D0975", "de"),
                         "Verordnung")
        self.assertEqual(find(["Die Richtlinie 87/182/EWG ermächtigt die Kommission."], "31987D0182", "de"),
                         "Richtlinie")
        self.assertIsNone(find(["Der Beschluss (EU) 2015/975 betrifft einen Schlagbohrer."], "32015D0975", "de"))
        self.assertIsNone(find(["Die Verordnung (EU) Nr. 305/2011 ersetzt die Richtlinie 89/106/EWG."],
                               "32011R0305", "de"))                    # another act may be a directive

    def test_order_of_the_act(self):
        locators = ["Annex II", "Article 10", "Annex I", "Article 2", "Article 2a",
                    "Annex I, ANNEX I [section 2], unnumbered table 10", "Annex I, ANNEX I [section 2], unnumbered table 9"]
        self.assertEqual(sorted(locators, key=summariser.locator_key),
                         ["Article 2", "Article 2a", "Article 10", "Annex I",
                          "Annex I, ANNEX I [section 2], unnumbered table 9",
                          "Annex I, ANNEX I [section 2], unnumbered table 10", "Annex II"])


class ActStepTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(summariser, "OUTPUT_LANGUAGE", "de")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sentences_are_taken_as_written_without_a_correction(self):
        long = act("Der Beschluss 2015/975 betrifft einen Schlagbohrer, " * 12, "Hersteller müssen den Bohrer vom Markt neh")
        client = client_with(long)
        sentences, log = summariser.summarise_act(client, "S", "32015D0975", "T", "x", [])
        self.assertEqual((len(sentences), len(log)), (2, 1))
        self.assertTrue(sentences[1].endswith("neh"))
        sent = json.loads(client.chat.call_args.args[0][1]["content"].partition("\n\nTASK:")[0])
        self.assertEqual(sent["act"]["act_type"], "Beschluss")
        self.assertNotIn("maxLength", json.dumps(summariser.ACT_SCHEMA))

    def test_provisions_for_the_act_are_spread_and_a_table_group_is_one_entry(self):
        records = [saved(f"Article {n}", "DIRECT" if n in (40, 41) else "INDIRECT", headline=f"A{n}")
                   for n in range(1, 61)]
        records += [saved(f"Annex I, ANNEX I [section 2], unnumbered table {n}") for n in range(1, 31)]
        records += [saved(f"Annex II, Table {n}") for n in (1, 2)]            # too few for a group: one entry
        group = {"base": "Annex I, ANNEX I [section 2]",
                 "members": [f"X:Annex I, ANNEX I [section 2], unnumbered table {n}" for n in range(1, 31)],
                 "summary": {"headline": "Tables", "overview": "O.", "bullets": [{"kind": "OTHER", "text": "P."}]}}
        shown = summariser.act_input(records, {"k": group})
        locators = [entry["locator"] for entry in shown]
        self.assertEqual(len(shown), summariser.MAX_ACT_HEADLINES)
        self.assertIn("Article 40", locators)
        self.assertIn("Article 41", locators)
        self.assertIn("Article 1", locators)
        self.assertGreater(max(int(l.split()[1]) for l in locators if l.startswith("Article")), 50)  # not only the start
        self.assertEqual(locators, sorted(locators, key=summariser.locator_key))
        self.assertNotIn("level", shown[0])
        everything = summariser.act_input(records[60:], {"k": group})
        self.assertEqual([e["locator"] for e in everything], ["Annex I, ANNEX I [section 2]", "Annex II, Table 1"])
        self.assertEqual(everything[0]["tables"], 30)


class TableStepTests(unittest.TestCase):
    def test_pieces_of_one_section_form_a_group_a_titled_table_stays_alone(self):
        self.assertEqual(summariser.table_base("Annex 1, B. CLASSIFICATION [section 5], unnumbered table 10"),
                         "Annex 1, B. CLASSIFICATION [section 5]")
        self.assertEqual(summariser.table_base("Annex VII, Table 4"), "Annex VII")
        self.assertEqual(summariser.table_base("Annex VII, Table 4 [source path annex/7]"), "Annex VII")
        self.assertIsNone(summariser.table_base("Annex IV, Table 1 — Product areas [section 4], unnumbered table 1 x"))
        self.assertIsNone(summariser.table_base("Article 3"))
        records = [saved(f"Annex I, ANNEX I [section 2], unnumbered table {n}") for n in (10, 2, 1)]
        records += [saved("Annex II, Table 1"), saved("Annex II, Table 2"), saved("Article 1")]
        groups = list(summariser.table_groups_of("X", records).values())
        self.assertEqual(len(groups), 1)
        self.assertEqual([r["target"][-2:].strip() for r in groups[0]["records"]], ["1", "2", "10"])

    def test_table_summary_is_taken_as_written(self):
        records = [saved(f"Annex I, unnumbered table {n}", points=(f"Limit {n * 10} mg.",)) for n in range(1, 21)]
        sent = summariser.table_input("32013R0517", "T", "Annex I", records)
        self.assertEqual((sent["tables_total"], len(sent["tables"])), (20, summariser.MAX_TABLES_SHOWN))
        written = table_reply("Grenzwerte", ("OTHER", "Der Grenzwert ist 999 mg."), ("OTHER", "Die Werte stehen in mg."))
        summary, log = summariser.summarise_table_group(client_with(written), "S", sent)
        self.assertEqual((len(summary["bullets"]), len(log)), (2, 1))
        self.assertTrue(summary["number_check"])

class TidyMoreTests(unittest.TestCase):
    def test_who_that_names_no_one_becomes_a_note_and_terms_are_fixed(self):
        summary = {"headline": "Erklärung der Leistung", "overview": "Nach Regulation (EU) No 305/2011.", "bullets": [
            {"kind": "WHO", "text": "Die Vorschrift betrifft die Herkunft von Elektroabfällen."},
            {"kind": "WHO", "text": "Die Vorschrift betrifft Hersteller von Bauprodukten."},
            {"kind": "DUTY", "text": "Hersteller müssen eine Erklärung der Leistung nach Richtlinie 89/106/EEC erstellen."}]}
        tidied = summariser.tidy_summary(summary, "Article 4", "de")
        self.assertEqual([b["kind"] for b in tidied["bullets"]], ["WHO", "DUTY", "OTHER"])
        self.assertEqual(tidied["headline"], "Leistungserklärung")
        self.assertEqual(tidied["overview"], "Nach Verordnung (EU) Nr. 305/2011.")
        self.assertIn("Leistungserklärung nach Richtlinie 89/106/EWG", tidied["bullets"][1]["text"])
        self.assertEqual(tidied["tidied"], {"relabelled_to_other": 0, "removed": 0, "who_to_other": 1, "terms_fixed": 3})
        self.assertEqual(summariser.tidy_summary(summary, "Article 4", "en")["headline"], "Erklärung der Leistung")

    def test_saved_act_sentences_lose_celex_and_repeats_but_nothing_is_cut(self):
        sentences = ["Die REACH-Verordnung (32006R1907) regelt Chemikalien.", "Hersteller müssen registrieren.",
                     "Hersteller müssen registrieren.", "Die Erstellung muss innerhalb von 6 Monaten fertiggestelltwer"]
        kept, repeated = summariser.tidy_act_sentences(sentences, "de")
        self.assertEqual(kept, ["Die REACH-Verordnung regelt Chemikalien.", "Hersteller müssen registrieren.",
                                "Die Erstellung muss innerhalb von 6 Monaten fertiggestelltwer"])
        self.assertEqual(repeated, 1)

class CutOffTests(unittest.TestCase):
    LONG = ("Hersteller müssen die technische Dokumentation zehn Jahre aufbewahren. " * 6)[:370] + " und der"

    def setUp(self):
        patcher = patch.object(summariser, "OUTPUT_LANGUAGE", "de")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_length_limit_is_sent_to_qwen(self):
        for schema in (summariser.provision_schema(["P1"]), summariser.MERGE_SCHEMA, summariser.table_schema(),
                       summariser.ACT_SCHEMA, summariser.department_schema(["A"])):
            self.assertNotIn("maxLength", json.dumps(schema))

    def test_texts_cut_in_the_earlier_run_are_found_and_not_changed_by_the_tidying(self):
        summary = {"headline": ("Pflichten der Hersteller " * 6)[:140], "overview": "Kurz.",
                   "bullets": [{"kind": "DUTY", "text": self.LONG}]}
        self.assertTrue(summariser.was_cut(summary))
        tidied = summariser.tidy_summary(summary, "Article 1", "de")
        self.assertEqual((tidied["headline"], tidied["bullets"][0]["text"]), (summary["headline"], self.LONG))
        self.assertFalse(summariser.was_cut({"headline": "H", "overview": "O.",
                                             "bullets": [{"kind": "WHO", "text": "Mitgliedstaaten"}]}))

class TableRunTests(unittest.TestCase):
    TABLES = [f"Annex I, ANNEX I [section 2], unnumbered table {n}" for n in (1, 2, 3)]

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        celex = "32015D0975"
        self.run_folder = make_run(self.root, "first", [[
            record(celex, "Article 1", text=TEXT), *(record(celex, t, level="INDIRECT", text=TEXT) for t in self.TABLES)]])
        self.cls = self.root / "cls"
        self.cls.mkdir()
        (self.cls / "results.json").write_text(json.dumps({celex: {
            "Article 1": classified(), **{t: classified(level="INDIRECT") for t in self.TABLES}}}), encoding="utf-8")
        (self.cls / "acts.json").write_text(json.dumps({celex: {"act_subject": "energy audits"}}), encoding="utf-8")
        self.out = self.root / "out"

    def main(self, client):
        argv = ["summarise", "--runs", str(self.run_folder), "--unsure-dir", str(self.root / "none"),
                "--classification-dir", str(self.cls), "--output-dir", str(self.out), "--workers", "1",
                "--language", "en"]
        with patch.object(sys, "argv", argv), patch.object(summariser.QwenClient, "from_env", return_value=client), \
                patch("classify_eurlex_provisions.time.sleep"):
            return summariser.main()

    def test_tables_become_one_group_on_the_card_and_a_new_act_version_redoes_only_the_acts(self):
        right = act("The Decision 2015/975 sets rules on energy.", "Enterprises must carry out energy audits.")
        client = client_with(provision(headline="T1"), provision(headline="T2"), provision(headline="T3"),
                             provision(), table_reply(), right, card())
        self.assertEqual(self.main(client), 0)
        act_request = json.loads(client.chat.call_args_list[5].args[0][1]["content"].partition("\n\nTASK:")[0])
        self.assertEqual([p["locator"] for p in act_request["provisions"]], ["Article 1", "Annex I, ANNEX I [section 2]"])
        self.assertEqual((act_request["act"]["act_type"], act_request["provisions_total"]), ("Decision", 4))
        card_saved = json.loads((self.out / "inbox" / "Energiesysteme.json").read_text(encoding="utf-8"))["cards"][0]
        self.assertEqual(card_saved["act_summary"][0], "The Decision 2015/975 sets rules on energy.")
        self.assertEqual(len(card_saved["provisions"]), 4)                     # the UI still gets every provision
        group = card_saved["table_groups"][0]
        self.assertEqual((group["headline"], group["members"]), ("Hazard statements for dangerous substances", self.TABLES))
        self.assertEqual(sum("table_group" in p for p in card_saved["provisions"]), 3)
        page = (self.out / "preview" / "Energiesysteme.html").read_text(encoding="utf-8")
        self.assertIn("3 tables", page)
        self.assertEqual(page.count("<summary>Annex I, ANNEX I [section 2], unnumbered table"), 0)
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["table_groups"], summary["tables_in_groups"]), (1, 3))
        with (self.out / "review_tables.csv").open(encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(len(list(csv.reader(handle, delimiter=";"))), 2)
        again = client_with()
        self.assertEqual(self.main(again), 0)
        again.chat.assert_not_called()
        with patch.object(summariser, "ACT_VERSION", "act-v3"):
            newer = client_with(right)                                        # the act only, not the card
            self.assertEqual(self.main(newer), 0)
        self.assertEqual(newer.chat.call_count, 1)

    def test_a_folder_of_v4_5_continues_when_the_provision_prompts_are_the_same(self):
        self.out.mkdir()
        systems = {name: summariser.system_prompt(name, "en") for name in summariser.PROVISION_PROMPTS}
        config = self.out / "run_config.json"
        config.write_text(json.dumps({"fingerprint": "old", "language": "en",
                                      "system_prompts": {**systems, "act": "an older act prompt"}}), encoding="utf-8")
        right = act("The Decision 2015/975 sets rules on energy.", "Enterprises must carry out energy audits.")
        client = client_with(provision(), provision(), provision(), provision(), table_reply(), right, card())
        self.assertEqual(self.main(client), 0)
        self.assertIn("provision_fingerprint", json.loads(config.read_text(encoding="utf-8")))
        config.write_text(json.dumps({"fingerprint": "old", "language": "en",
                                      "system_prompts": {**systems, "provision": "another prompt"}}), encoding="utf-8")
        self.assertEqual(self.main(client_with()), 1)


if __name__ == "__main__":
    unittest.main()
