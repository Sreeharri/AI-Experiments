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

    def test_invented_number_gets_one_correction_then_fails(self):
        wrong = provision(("DEADLINE", "The audit is due within 30 days.", ["P1"]))
        client = client_with(wrong, provision())
        summary, _ = summariser.summarise_provision(client, "S", record("A", "Article 1", text=TEXT))
        self.assertEqual(len(summary["bullets"]), 1)
        self.assertIn("The number 30 does not occur", client.chat.call_args.args[0][1]["content"])
        with self.assertRaisesRegex(summariser.Unclassified, "after one correction"):
            summariser.summarise_provision(client_with(wrong, wrong), "S", record("A", "Article 1", text=TEXT))

    def test_bad_shapes_are_rejected_and_repeated_bullets_merged(self):
        passages, source = {"P1": TEXT}, TEXT
        twice = {"headline": "H", "overview": "O.", "bullets": [{"kind": "DUTY", "text": "Same.", "passages": ["P1"]},
                                              {"kind": "OTHER", "text": "same.", "passages": ["P1"]}]}
        self.assertEqual(len(summariser.validate_provision(twice, passages, source)["bullets"]), 1)
        for value, message in (({"headline": "", "overview": "O.", "bullets": twice["bullets"]}, "headline"),
                               ({"headline": "H", "overview": "", "bullets": twice["bullets"]}, "overview"),
                               ({"headline": "H", "overview": "Within 30 days.", "bullets": twice["bullets"]}, "number 30"),
                               ({"headline": "H", "overview": "O.", "bullets": []}, "1 to 4"),
                               ({"headline": "H", "overview": "O.", "bullets": [{"kind": "DUTY", "text": "x", "passages": ["P9"]}]},
                                "passage marks"),
                               ({"headline": "H", "overview": "O.", "bullets": [{"kind": "WHY", "text": "x", "passages": ["P1"]}]},
                                "kind")):
            with self.assertRaisesRegex(summariser.Unclassified, message):
                summariser.validate_provision(value, passages, source)

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
        with self.assertRaisesRegex(summariser.Unclassified, "date 27.11.2001"):
            summariser.validate_provision(reply_, passages, source, "Article 11")

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

    def test_wrong_language_gets_a_correction_and_empty_statements_are_dropped(self):
        english = provision(("DUTY", "Enterprises must carry out an energy audit of the site.", ["P1"]))
        german = reply({"headline": "Energieaudit", "overview": "Große Energieverbraucher müssen ein Audit machen.",
                        "bullets": [{"kind": "DUTY", "text": "Unternehmen müssen ein Energieaudit durchführen.",
                                     "passages": ["P1"]},
                                    {"kind": "DEADLINE", "text": "Eine Frist wird nicht genannt.", "passages": ["P1"]}]})
        client = client_with(english, german)
        with patch.object(summariser, "OUTPUT_LANGUAGE", "de"):
            summary, _ = summariser.summarise_provision(client, "S", record("A", "Article 1", text=TEXT))
        self.assertIn("Write everything in German", client.chat.call_args.args[0][1]["content"])
        self.assertEqual([b["kind"] for b in summary["bullets"]], ["DUTY"])
        sentences = ["Die Richtlinie regelt Energieaudits.", "Es gibt keine konkrete Frist."]
        self.assertEqual(summariser.without_empty_statements(sentences, lambda x: x), sentences[:1])

    def test_english_overview_is_not_hidden_by_german_bullets(self):
        english = reply({"headline": "Compliance simulations for type C modules",
                         "overview": "The owner must carry out the simulations and give the results to the operator.",
                         "bullets": [{"kind": "DUTY", "text": "Der Betreiber muss die Simulationen durchführen.",
                                      "passages": ["P1"]}]})
        with patch.object(summariser, "OUTPUT_LANGUAGE", "de"), \
                self.assertRaisesRegex(summariser.Unclassified, "after one correction"):
            summariser.summarise_provision(client_with(english, english), "S", record("A", "Article 1", text=TEXT))

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
        bad_merge = reply({"headline": "Whole annex", "overview": "Due within 30 days."})
        client = client_with(first, second, bad_merge, bad_merge)
        summary, audit = summariser.summarise_provision(client, "S", saved)
        self.assertEqual(audit["parts"], ["2", "4"])
        self.assertEqual([b["kind"] for b in summary["bullets"]], ["WHO", "DUTY", "DEADLINE", "CONDITION"])
        self.assertEqual([b["text"] for b in summary["more_bullets"]], ["Duty two.", "Duty three."])
        self.assertEqual((summary["parts_read"], summary["parts_total"]), (["2", "4"], total))
        self.assertEqual({item["reason"] for item in summary["not_read"]}, {"NOT_RELEVANT"})
        self.assertEqual(len(summary["not_read"]), total - 2)
        self.assertEqual(summary["overview_covers"], "2")          # the join failed: say what the overview covers
        note = summariser.parts_note(summary, "de")
        self.assertIn("Teil 2, 4 von", note)
        self.assertEqual(note.count("bei der Prüfung nicht relevant"), 1)     # one line, not one per part
        sent = json.loads(client.chat.call_args_list[0].args[0][1]["content"].partition("\n\nTASK:")[0])
        self.assertEqual(sent["target"]["part"], f"2 of {total}")

    def test_connection_problem_is_not_a_summary(self):
        lost = ChatResult(ok=False, error_kind="timeout", error="Timed out")
        with self.assertRaises(summariser.ConnectionLost):
            summariser.summarise_provision(client_with(lost), "S", record("A", "Article 1", text=TEXT))


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

    def test_act_summary_uses_saved_facts_and_rejects_new_numbers(self):
        client = client_with(act())
        sentences, _ = summariser.summarise_act(client, "S", "A", "Directive 2023/1791", "energy efficiency",
                                                [{"locator": "Article 1", "headline": "Scope", "points": ["Enterprises."]}])
        self.assertEqual(len(sentences), 2)
        sent = json.loads(client.chat.call_args.args[0][1]["content"].partition("\n\nTASK:")[0])
        self.assertEqual(list(sent), ["act", "act_subject", "provisions"])
        with self.assertRaisesRegex(summariser.Unclassified, "after one correction"):
            bad = act("It sets rules.", "It applies from 2030.")
            summariser.summarise_act(client_with(bad, bad), "S", "A", "Directive 2023/1791", "x", [])

    def test_card_input_puts_direct_first_and_limits_the_list(self):
        sent = summariser.card_input(["Energiesysteme"], "A", "T", ["S."], self.PROVISIONS)
        self.assertEqual([p["locator"] for p in sent["provisions"]], ["Article 2", "Article 1"])
        self.assertEqual(sent["provisions"][0]["bullets"], ["Audit every 4 years."])
        many = [{**self.PROVISIONS[0], "locator": f"Article {n}"} for n in range(40)]
        sent = summariser.card_input(["X"], "A", "T", [], many)
        self.assertEqual((len(sent["provisions"]), sent["more_provisions"]), (15, 25))

    def test_department_summary_checks_locators_questions_and_numbers(self):
        sent = summariser.card_input(["Energiesysteme"], "A", "T", ["S."], self.PROVISIONS)
        good = card(("ATTENTION", "Read Article 2 first.", ["Article 2"]),
                    ("WHY_MAX_BOEGL", "Suppliers are affected.", ["Article 1"]),
                    ("TO_CONFIRM", "Is the use above 85 TJ?", ["Article 2"]),
                    ("TO_CONFIRM", "Another question?", ["Article 1"]))       # Article 1 has no open question
        bullets, _ = summariser.summarise_department(client_with(good), "S", sent)
        self.assertEqual([b["kind"] for b in bullets], ["WHY_MAX_BOEGL", "ATTENTION", "TO_CONFIRM"])
        bad = card(("ATTENTION", "Audit within 90 days.", ["Article 2"]))
        with self.assertRaisesRegex(summariser.Unclassified, "after one correction"):
            summariser.summarise_department(client_with(bad, bad), "S", sent)
        with self.assertRaisesRegex(summariser.Unclassified, "locators"):
            summariser.validate_department({"bullets": [{"kind": "ATTENTION", "text": "x", "provisions": ["Article 9"]}]},
                                           sent["provisions"], "")

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
        bad = provision(("DEADLINE", "Within 30 days.", ["P1"]))
        client = client_with(provision(), lost, bad, bad, provision(), act(), act(), card())
        client.discover_results = [{"ok": True}, {"ok": False, "error": "No model list found."}, {"ok": True}]
        code, sleep = self.main(client)
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] / 60 for call in sleep.call_args_list], [15])
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual((summary["provisions_summarised"], summary["provisions_failed"]), (2, 1))
        client = client_with(provision(headline="Scope"), act(), card())   # the failed provision, its act and card again
        self.assertEqual(self.main(client)[0], 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_failed"], 0)

    def test_changed_language_needs_a_new_folder_and_missing_classification_is_reported(self):
        self.main(client_with(provision(), provision(), provision(), act(), act(), card()))
        self.assertEqual(self.main(client_with(), "--language", "de")[0], 1)
        argv = ["summarise", "--runs", str(self.run_folder), "--classification-dir", str(self.root / "nothing"),
                "--output-dir", str(self.out)]
        with patch.object(sys, "argv", argv):
            self.assertEqual(summariser.main(), 1)


if __name__ == "__main__":
    unittest.main()
