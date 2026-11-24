import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import fix_ai_results as fix
import inspect_ai_results as check
from lr_ai.qwen_client import ChatResult


def client_with(*replies):
    client = Mock()
    client.clean = lambda text: text
    queue = list(replies)

    def chat(messages, **options):
        if not queue:
            raise AssertionError("More requests than expected.")
        return queue.pop(0)

    client.chat.side_effect = chat
    return client


def reply(value):
    return ChatResult(ok=True, finish_reason="stop", content=json.dumps(value))


AUTHORITY_TEXT = "Article 47 Penalties By 24 November 2026, Member States shall lay down rules on penalties."
FORMAL_TEXT = "Article 4 This Decision is addressed to the Member States."
BUSINESS_TEXT = "Article 5 The manufacturer shall keep the technical file for 10 years."


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class HelperTests(unittest.TestCase):
    def test_amended_act_and_annex_start(self):
        self.assertEqual(fix.amended_act("ANNEX The Annex to Regulation (EC) No 440/2008 is amended as follows: x"),
                         "Ändert Verordnung (EG) Nr. 440/2008")
        self.assertEqual(fix.amended_act("Article 1 Directive 2009/28/EC is amended as follows:"),
                         "Ändert Richtlinie 2009/28/EG")
        self.assertEqual(fix.amended_act("Article 1 The manufacturer shall keep the file."), "")
        self.assertEqual(fix.annex_subject("Die Verordnung regelt Grenzwerte."), "Der Anhang regelt Grenzwerte.")

    def test_source_sentences_hold_the_whole_sentence_of_a_quote(self):
        text = "1. First rule applies. 2. Member States may require that taxable persons submit statements. 3. Last."
        self.assertEqual(fix.source_sentences(["may require that taxable persons"], text),
                         ["Member States may require that taxable persons submit statements."])
        self.assertEqual(fix.source_sentences(["not in the text"], text), ["not in the text"])


class AskTests(unittest.TestCase):
    item = {"step": "point", "target": "Article 268", "kind": "DUTY",
            "point": "Steuerpflichtige müssen Anmeldungen einreichen.",
            "source": ["Member States may require that taxable persons submit statements by 15 May."]}

    def test_a_permission_written_as_a_duty_is_corrected(self):
        client = client_with(reply({"problem": "PERMISSION_AS_DUTY", "point": "Die Mitgliedstaaten können verlangen, "
                                    "dass Steuerpflichtige Anmeldungen einreichen."}))
        answer = fix.ask_point(client, "system", self.item, [])
        self.assertEqual(answer["problem"], "PERMISSION_AS_DUTY")
        self.assertTrue(answer["point"].startswith("Die Mitgliedstaaten können"))

    def test_a_correction_with_a_new_number_is_not_used(self):
        wrong = reply({"problem": "PERMISSION_AS_DUTY", "point": "Bis 30. Juni können Anmeldungen verlangt werden."})
        answer = fix.ask_point(client_with(wrong, wrong), "system", self.item, [])
        self.assertEqual(answer["point"], self.item["point"])
        self.assertIn("not_used", answer)

    def test_none_keeps_the_point(self):
        answer = fix.ask_point(client_with(reply({"problem": "NONE", "point": "anders"})), "system", self.item, [])
        self.assertEqual(answer, {"problem": "NONE", "point": self.item["point"]})

    def test_a_translation_still_in_english_is_asked_once_more_then_not_used(self):
        item = {"target": "Article 18", "headline": "Cybersecurity risk assessment for the grid", "overview": ""}
        english = reply({"headline": "Cybersecurity risk assessment for the grid", "overview": ""})
        answer = fix.ask_language(client_with(english, english), "system", item, [])
        self.assertIn("not_used", answer)
        german = reply({"headline": "Bewertung der Cybersicherheitsrisiken für das Netz", "overview": ""})
        self.assertEqual(fix.ask_language(client_with(german), "system", item, [])["headline"],
                         "Bewertung der Cybersicherheitsrisiken für das Netz")


class VerifyTests(unittest.TestCase):
    source = ["Article 33 1. The customs authorities shall inform the importer of the reporting obligation."]

    def item(self, problem="PERMISSION_AS_DUTY"):
        return {"target": "Article 33", "problem": problem, "source": self.source,
                "point": "Die Information über die Meldepflicht muss bei der Freigabe erfolgen.",
                "proposed": "Die Information über die Meldepflicht kann bei der Freigabe erfolgen."}

    def test_a_permission_correction_is_not_used_when_the_sentence_says_shall(self):
        answer = fix.ask_verify(client_with(reply({"evidence": self.source[0], "choice": "B"})), "s", self.item(), [])
        self.assertFalse(answer["used"])
        self.assertEqual(answer["why_not"], "the sentence states a duty")

    def test_a_copied_sentence_that_is_not_in_the_text_is_not_used(self):
        answer = fix.ask_verify(client_with(reply({"evidence": "The importer may inform the authorities later on.",
                                                   "choice": "B"})), "s", self.item(), [])
        self.assertFalse(answer["used"])

    def test_supported_corrections_are_used(self):
        self.assertEqual(fix.supports("Member States may require that taxable persons submit statements.",
                                      "PERMISSION_AS_DUTY",
                                      ["2. Member States may require that taxable persons submit statements."]),
                         (True, ""))
        answer = fix.ask_verify(client_with(reply({"evidence": self.source[0], "choice": "B"})), "s",
                                self.item("ACTOR_MISSING"), [])
        self.assertTrue(answer["used"])
        self.assertFalse(fix.ask_verify(client_with(reply({"evidence": self.source[0], "choice": "A"})), "s",
                                        self.item("ACTOR_MISSING"), [])["used"])


class SavedSummaryTests(unittest.TestCase):
    def test_a_saved_act_summary_is_written_again_only_when_its_corrections_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            corrections = {"provision_levels": {}, "v1": {"32020R0001": {"Article 1": {"headline": "Neu"}}}, "v2": {}}
            basis = fix.act_basis(corrections, "32020R0001")
            write(fix.act_file(out, "32020R0001"), {"version": fix.ACT_FIX, "celex": "32020R0001", "sentences": ["x"]})
            # Saved before the fingerprint existed: right unless a correction of the act is no longer used.
            self.assertIsNotNone(fix.saved_act(out, "32020R0001", basis, legacy_ok=True))
            self.assertIsNone(fix.saved_act(out, "32020R0001", basis, legacy_ok=False))
            write(fix.act_file(out, "32020R0001"), {"version": fix.ACT_FIX, "celex": "32020R0001", "sentences": ["x"],
                                                    "basis": basis})
            self.assertIsNotNone(fix.saved_act(out, "32020R0001", basis, legacy_ok=False))
            other = fix.act_basis({**corrections, "v1": {}}, "32020R0001")
            self.assertIsNone(fix.saved_act(out, "32020R0001", other))


class SecondRunTests(unittest.TestCase):
    def test_helpers_found_in_the_first_run(self):
        self.assertEqual(fix.annex_subject("Die Verordnung (EG) Nr. 595/2009 legt Grenzwerte fest."),
                         "Der Anhang legt Grenzwerte fest.")
        self.assertEqual(fix.annex_subject("Die Richtlinie 2014/25/EU enthält eine Tabelle."),
                         "Der Anhang enthält eine Tabelle.")
        self.assertEqual(fix.amended_act("ANNEX XI Annex II to Regulation (EU) 2017/2400 is amended as follows",
                                         "32017R2400"), "")
        self.assertEqual(fix.act_file(Path("o"), "32008D0070(01)"), Path("o") / "acts" / "32008D0070_01.json")

    def test_formal_clauses_answered_as_authority_rules_are_asked_once_more(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            items = [{"step": "recheck", "id": "a1", "celex": "32011D0014", "target": "Article 2"},
                     {"step": "recheck", "id": "b2", "celex": "32012R1025", "target": "Article 19"}]
            write(out / "answers" / "recheck" / "a1.json", {"version": fix.VERSION, "answer": {
                "who_must_act": "AUTHORITY_ONLY", "reason": "The Decision is addressed to the Member States."}})
            write(out / "answers" / "recheck" / "b2.json", {"version": fix.VERSION, "answer": {
                "who_must_act": "AUTHORITY_ONLY", "reason": "The Commission must protect financial interests."}})
            fix.second_round(out, items)
            self.assertEqual([item["id"] for item in items], ["a1-2", "b2"])

    def test_first_run_summaries_stay_unless_the_corrections_of_their_act_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = {"provision_levels": {}, "v1": {"32020R0001": {"Article 1": {"headline": "Neu"}}}, "v2": {},
                     "acts_redone": {"32020R0002": {}}}
            write(out / "corrections.json", first)
            legacy = fix.legacy_bases(out)
            self.assertTrue((out / fix.BEFORE).is_file())
            later = {**first, "provision_levels": {"32020R0002": {"Article 4": {"before": "INDIRECT",
                                                                                "after": "IGNORE"}}}}
            self.assertEqual(legacy["32020R0001"], fix.act_basis(later, "32020R0001"))   # unchanged: stays
            self.assertNotEqual(legacy["32020R0002"], fix.act_basis(later, "32020R0002"))   # changed: again

    def test_english_points_of_department_summaries_in_use_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "v1" / "departments" / "32022L1999_k1.json", {"celex": "32022L1999", "bullets": [
                {"kind": "TO_CONFIRM", "text": "Does Max Bögl transport dangerous goods by road?"},
                {"kind": "WHY_DEPARTMENT", "text": "Die Richtlinie betrifft Gefahrguttransporte."}]})
            write(root / "v1" / "departments" / "32018L2001_k2.json", {"celex": "32018L2001", "bullets": [
                {"kind": "TO_CONFIRM", "text": "Does Max Bögl produce biogas for the grid?"}]})
            write(root / "out" / "departments" / "32018L2001_k3.json", {"celex": "32018L2001", "bullets": [
                {"kind": "TO_CONFIRM", "text": "Does Max Bögl operate heat pumps in its buildings?"}]})
            items = fix.wording_items(root / "out", root / "v1", {"32018L2001": {"k3"}})
            self.assertEqual([(i["origin"], i["file"]) for i in items],
                             [("fix", "32018L2001_k3"), ("v1", "32022L1999_k1")])


class RunTests(unittest.TestCase):
    def test_the_run_asks_only_the_flagged_items_and_writes_the_corrections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch = root / "screening" / "batches" / "0001"
            results = []
            for number, (target, text, level) in enumerate([("Article 47", AUTHORITY_TEXT, "DIRECT"),
                                                            ("Article 4", FORMAL_TEXT, "INDIRECT"),
                                                            ("Article 5", BUSINESS_TEXT, "DIRECT")]):
                name = f"audit/32024R1252/article_{number}.json"
                write(batch / name, {"evidence_pack": {"document": {"title": "Regulation on raw materials"},
                                                       "provision": {"text": text}}, "details": {"actor": ""}})
                results.append({"celex": "32024R1252", "target": target, "kind": "ARTICLE", "target_id": target,
                                "file": name, "level": level})
            write(batch / "audit" / "run_summary.json", {"results": results})
            v1 = root / "v1" / "provisions" / "targets" / "32024R1252"
            write(v1 / "a.json", {"celex": "32024R1252", "target": "Article 47", "text": AUTHORITY_TEXT, "summary": {
                "headline": "Sanktionen", "overview": "Regeln für Sanktionen.", "bullets": [
                    {"kind": "DEADLINE", "text": "Die Regeln für Sanktionen müssen bis 24. November 2026 erlassen werden.",
                     "quotes": ["By 24 November 2026, Member States shall lay down rules on penalties."]}]}})
            write(v1 / "b.json", {"celex": "32024R1252", "target": "Annex I", "text": "ANNEX I list", "summary": {
                "headline": "List of raw materials", "overview": "Die Verordnung listet Rohstoffe.", "bullets": []}})
            out = root / "out"
            replies = [reply({"who_must_act": "FORMAL_ONLY", "reason": "Addressees only."}),        # Article 4
                       reply({"who_must_act": "AUTHORITY_ONLY", "reason": "Member States lay down penalties."}),
                       reply({"problem": "ACTOR_MISSING", "point": "Die Mitgliedstaaten müssen bis 24. November 2026 "
                              "Regeln für Sanktionen erlassen."}),
                       reply({"evidence": "By 24 November 2026, Member States shall lay down rules on penalties.",
                              "choice": "B"}),
                       reply({"headline": "Liste der Rohstoffe", "overview": "Die Verordnung listet Rohstoffe."})]
            client = client_with(*replies)
            saved = (check.DEFAULT_RUNS, check.DEFAULT_UNSURE, check.NEW_ARTICLES, check.SUMMARIES_V1,
                     check.SUMMARIES_V2)
            try:
                check.DEFAULT_RUNS, check.DEFAULT_UNSURE = [root / "screening"], root / "none"
                check.NEW_ARTICLES, check.SUMMARIES_V1, check.SUMMARIES_V2 = root / "none", root / "v1", root / "none"
                with patch.object(fix.QwenClient, "from_env", return_value=client), \
                        patch.object(fix, "reachable", return_value=(True, None)):
                    steps = ["--steps", "recheck", "point", "verify", "language"]
                    self.assertEqual(fix.main(["--output-dir", str(out), "--workers", "1", *steps]), 0)
                    # Everything is saved: the same command asks nothing again.
                    self.assertEqual(fix.main(["--output-dir", str(out), "--workers", "1", *steps]), 0)
            finally:
                (check.DEFAULT_RUNS, check.DEFAULT_UNSURE, check.NEW_ARTICLES, check.SUMMARIES_V1,
                 check.SUMMARIES_V2) = saved
            self.assertEqual(client.chat.call_count, 5)
            result = json.loads((out / "corrections.json").read_text(encoding="utf-8"))
            self.assertEqual(result["provision_levels"]["32024R1252"]["Article 47"]["after"], "INDIRECT")
            self.assertEqual(result["provision_levels"]["32024R1252"]["Article 4"]["after"], "IGNORE")
            self.assertNotIn("Article 5", result["provision_levels"]["32024R1252"])
            self.assertTrue(result["v1"]["32024R1252"]["Article 47"]["bullets"]["0"]["after"].startswith(
                "Die Mitgliedstaaten müssen"))
            annex = result["v1"]["32024R1252"]["Annex I"]
            self.assertEqual(annex["headline"], "Liste der Rohstoffe")
            self.assertTrue(annex["overview_starts_with_annex"])
            review = (out / "review.csv").read_text(encoding="utf-8-sig")
            self.assertIn("recheck;32024R1252;Article 47;level;DIRECT;INDIRECT", review)



def classification(domain="energy_sustainability", departments=("Energiemanagement",)):
    return {"level": "DIRECT", "primary_domain": domain, "primary_title": "Energie", "primary_reason": "r",
            "secondary_domain": None, "secondary_title": "", "secondary_reason": "",
            "departments": list(departments), "secondary_departments": [], "check": []}


def provision_record(target, level, headline, bullet, quote=""):
    return {"celex": "32018L2001", "target": target, "kind": "ARTICLE", "target_id": f"32018L2001:{target}",
            "level": level, "title": "Directive (EU) 2018/2001 on renewable energy", "text": f"{target} text",
            "classification": classification(), "facts": {"why_relevant": "w", "indirect_path": None,
                                                          "to_confirm": ""},
            "summary": {"headline": headline, "overview": "Überblick.",
                        "bullets": [{"kind": "DUTY", "text": bullet, "passages": ["P1"], "quotes": [quote]}]}}


class ActTests(unittest.TestCase):
    def test_act_and_department_summaries_are_written_again_from_the_corrected_provisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            v1 = root / "v1" / "provisions" / "targets" / "32018L2001"
            write(v1 / "a.json", provision_record("Article 3", "DIRECT", "Ziel 32 %", "Die Union muss 32 % erreichen."))
            write(v1 / "b.json", provision_record("Article 9", "DIRECT", "Gemeinsame Projekte", "Projekte müssen x."))
            write(v1 / "c.json", provision_record("Article 4", "INDIRECT", "Förderregelungen", "Förderung muss y."))
            v2 = root / "v2"
            write(v2 / "run_config.json", {"changed_dir": str(root / "changed")})
            summary_today = {"headline": "Ziel 42,5 %", "overview": "Heute.", "bullets": [
                {"kind": "DUTY", "text": "Die Mitgliedstaaten müssen gemeinsam 42,5 % erreichen.", "passages": ["P1"],
                 "quotes": ["Member States shall collectively ensure 42,5 %."]}]}
            write(v2 / "units" / "32018L2001" / "article_3.json", {
                "celex": "32018L2001", "key": "article/3", "name": "Article 3", "kind": "summary",
                "title": "Directive (EU) 2018/2001", "summary": summary_today,
                "provisions": [{"locator": "Article 3", "level": "DIRECT"}]})
            write(v2 / "units" / "32018L2001" / "article_15a.json", {
                "celex": "32018L2001", "key": "article/15a", "name": "Article 15a", "kind": "summary",
                "title": "Directive (EU) 2018/2001", "summary": {**summary_today, "headline": "Gebäude"},
                "provisions": [{"locator": "Article 15a", "level": "INDIRECT", "inserted_later": True}]})
            write(v2 / "units" / "32018L2001" / "article_4.json", {
                "celex": "32018L2001", "key": "article/4", "name": "Article 4", "kind": "deleted",
                "provisions": [{"locator": "Article 4", "level": "INDIRECT"}]})
            corrections = {"provision_levels": {"32018L2001": {"Article 9": {"before": "DIRECT", "after": "IGNORE"}}},
                           "v1": {}, "v2": {}}
            pack = root / "pack.json"
            write(pack, {"reason": "Buildings use renewable energy.", "details": {"indirect_path": "market_funding"}})
            extra = {"classified": {"32018L2001": {"Article 15a": {**classification(), "level": "INDIRECT"}}},
                     "provisions": {("32018L2001", "Article 15a"): {"file": pack, "kind": "ARTICLE",
                                                                    "target_id": "x:15a", "resolved": None}},
                     "acts": {}}
            records, facts = fix.act_records("32018L2001", corrections, root / "v1", v2, extra)
            self.assertEqual(sorted(r["target"] for r in records), ["Article 15a", "Article 3"])
            article_3 = next(r for r in records if r["target"] == "Article 3")
            self.assertEqual(article_3["summary"]["headline"], "Ziel 42,5 %")   # from today's text
            self.assertEqual(facts["left out (IGNORE or deleted)"], 2)
            self.assertEqual(facts["inserted later, added"], 1)
            self.assertEqual(fix.affected_acts(corrections, root / "v1", v2), ["32018L2001"])

            out = root / "out"
            client = client_with(reply({"sentences": ["Die Richtlinie regelt erneuerbare Energien."]}),
                                 reply({"bullets": [{"kind": "WHY_DEPARTMENT", "text": "Betrifft Energie.",
                                                     "provisions": ["Article 3"]}]}))
            saved = (check.DEFAULT_RUNS, check.DEFAULT_UNSURE, check.NEW_ARTICLES, check.SUMMARIES_V1,
                     check.SUMMARIES_V2, check.CLASSIFICATION)
            try:
                check.DEFAULT_RUNS, check.DEFAULT_UNSURE, check.NEW_ARTICLES = [], root / "none", root / "none"
                check.SUMMARIES_V1, check.SUMMARIES_V2, check.CLASSIFICATION = root / "v1", v2, root / "none"
                with patch.object(fix.QwenClient, "from_env", return_value=client), \
                        patch.object(fix, "reachable", return_value=(True, None)), \
                        patch.object(fix, "act_records", return_value=(records, facts)):
                    self.assertEqual(fix.main(["--output-dir", str(out), "--workers", "1",
                                               "--steps", "acts", "departments"]), 0)
            finally:
                (check.DEFAULT_RUNS, check.DEFAULT_UNSURE, check.NEW_ARTICLES, check.SUMMARIES_V1,
                 check.SUMMARIES_V2, check.CLASSIFICATION) = saved
            self.assertEqual(client.chat.call_count, 2)
            act = json.loads((out / "acts" / "32018L2001.json").read_text(encoding="utf-8"))
            self.assertEqual(act["provisions"], 2)
            [key] = act["department_keys"]
            department = json.loads((out / "departments" / f"32018L2001_{key}.json").read_text(encoding="utf-8"))
            self.assertEqual(department["departments"], ["Energiemanagement"])
            result = json.loads((out / "corrections.json").read_text(encoding="utf-8"))
            self.assertEqual(result["acts_redone"]["32018L2001"]["departments"], [key])


if __name__ == "__main__":
    unittest.main()
