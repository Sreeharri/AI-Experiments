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
import classify_eurlex_provisions as classifier  # noqa: E402

CONFIG = ROOT / "config"
DOMAINS = classifier.load_domains(CONFIG / "domains_en.yaml", CONFIG / "domains_de.yaml",
                                  CONFIG / "business_anchors_en.yaml")
IDS = DOMAINS["ids"]


def answer(primary="energy_sustainability", secondary="none", **changes):
    value = {"subject": "energy audits for enterprises", "primary_domain": primary,
             "primary_reason": "The article obliges enterprises to carry out energy audits.",
             "secondary_domain": secondary,
             "secondary_reason": "" if secondary == "none" else "It also sets rules for public tenders."}
    return ChatResult(ok=True, finish_reason="stop", content=json.dumps({**value, **changes}))


def client_with(*replies, discovered=True):
    """A stand-in for the Qwen client. Like the real one, it refuses requests before discover()."""
    client = Mock()
    client.clean = lambda text: text
    queue = list(replies)
    state = {"ready": discovered}

    def chat(messages, **options):
        if not state["ready"]:
            raise RuntimeError("Call discover() first")
        if not queue:
            raise AssertionError("More requests than expected.")
        return queue.pop(0)

    def discover():
        result = client.discover_results.pop(0) if client.discover_results else {"ok": True}
        state["ready"] = state["ready"] or result["ok"]
        return result

    client.discover_results = []
    client.chat.side_effect = chat
    client.discover.side_effect = discover
    return client


def pack(celex="A", target="Article 1", text="Enterprises shall carry out an energy audit every four years."):
    return {"document": {"celex": celex, "version_id": "v", "title": f"Test act {celex}"},
            "provision": {"locator": target, "text": text}, "direct_references": [],
            "essential_context": [], "footnotes": [], "unavailable_references": []}


def record(celex, target, level="DIRECT", area=("energy_efficiency_operations",), **text):
    return {"celex": celex, "target": target, "kind": "ARTICLE", "target_id": f"{celex}:{target}",
            "level": level, "reason": "Screening reason.", "area": list(area),
            "evidence_pack": pack(celex, target, **text)}


def make_run(root, name, batches):
    run = root / name
    for number, records in enumerate(batches, 1):
        folder = run / "batches" / f"{number:04d}"
        results = []
        for index, saved in enumerate(records):
            file = f"audit/{saved['celex']}/article_{index}.json"
            (folder / file).parent.mkdir(parents=True, exist_ok=True)
            (folder / file).write_text(json.dumps(saved), encoding="utf-8")
            results.append({key: saved[key] for key in ("celex", "target", "kind", "target_id", "level",
                                                         "reason", "area")} | {"file": file})
        (folder / "manifest.json").write_text(json.dumps({r["celex"]: "v" for r in records}), encoding="utf-8")
        (folder / "audit" / "run_summary.json").write_text(json.dumps({"results": results}), encoding="utf-8")
    return run


class CatalogueTests(unittest.TestCase):
    def test_real_domain_files_load_with_26_domains_and_the_customs_override(self):
        self.assertEqual(len(IDS), 26)
        self.assertIn("customs_trade", IDS)
        self.assertEqual(DOMAINS["anchor_domain"]["imports_trade_defence"], "customs_trade")
        self.assertNotIn("funding_investment", DOMAINS["anchor_domain"])      # funding follows what is funded
        self.assertEqual(DOMAINS["titles"]["waste_management"], "Abfallwirtschaft")
        self.assertEqual(DOMAINS["departments"]["customs_trade"], [])

    def test_system_prompt_is_fixed_and_lists_every_domain_with_its_boundary(self):
        instructions = classifier.PROMPT.read_text(encoding="utf-8")
        system = classifier.system_prompt(instructions, DOMAINS)
        self.assertEqual(system, classifier.system_prompt(instructions, DOMAINS))
        for domain in IDS:
            self.assertIn(f"- {domain} | ", system)
        self.assertIn("- no_fitting_domain", system)
        self.assertIn("Boundary: ", system)
        self.assertIn("RULES\n- ", system)
        self.assertNotIn("departments", system)
        self.assertLess(len(system), 30000)

    def test_schema_offers_only_known_answers(self):
        schema = classifier.reply_schema(IDS)
        self.assertEqual(schema["properties"]["primary_domain"]["enum"][-1], "no_fitting_domain")
        self.assertEqual(schema["properties"]["secondary_domain"]["enum"][-1], "none")
        self.assertEqual(list(schema["properties"]), schema["required"])


class ValidateTests(unittest.TestCase):
    def check(self, **changes):
        return classifier.validate(json.loads(answer(**changes).content), IDS)

    def test_plain_answer(self):
        result = self.check()
        self.assertEqual((result["primary_domain"], result["secondary_domain"], result["secondary_reason"]),
                         ("energy_sustainability", "none", ""))

    def test_same_domain_twice_is_one_domain(self):
        result = self.check(secondary="energy_sustainability")
        self.assertEqual((result["secondary_domain"], result["secondary_reason"]), ("none", ""))

    def test_no_fit_with_a_secondary_becomes_that_domain(self):
        result = self.check(primary="no_fitting_domain", secondary="public_procurement")
        self.assertEqual((result["primary_domain"], result["secondary_domain"]), ("public_procurement", "none"))
        self.assertIn("public tenders", result["primary_reason"])

    def test_reason_without_secondary_is_dropped(self):
        self.assertEqual(self.check(secondary_reason="Stray text.")["secondary_reason"], "")

    def test_rejected_answers(self):
        for changes, message in (({"secondary": "public_procurement", "secondary_reason": ""}, "own reason"),
                                 ({"primary_reason": " "}, "must not be empty"),
                                 ({"primary": "steel"}, "primary_domain"),
                                 ({"secondary": "no_fitting_domain"}, "secondary_domain"),
                                 ({"subject": "x" * 300}, "at most")):
            with self.assertRaisesRegex(classifier.Unclassified, message):
                self.check(**changes)
        with self.assertRaisesRegex(classifier.Unclassified, "exactly"):
            classifier.validate({"primary_domain": "waste_management"}, IDS)


class ClassifyTests(unittest.TestCase):
    def test_request_holds_text_and_task_but_no_relevance_result(self):
        client = client_with(answer())
        result, audit = classifier.classify(client, "SYSTEM", IDS, pack())
        self.assertEqual(result["primary_domain"], "energy_sustainability")
        messages = client.chat.call_args.args[0]
        self.assertEqual(messages[0], {"role": "system", "content": "SYSTEM"})
        evidence, _, task = messages[1]["content"].partition("\n\nTASK:")
        sent = json.loads(evidence)
        self.assertIn("[P1] Enterprises shall carry out", sent["target"]["text"])
        self.assertEqual(sent["act"], {"celex": "A", "title": "Test act A"})
        self.assertNotIn("level", evidence)
        self.assertNotIn("Screening reason", evidence)
        self.assertTrue(task.strip().endswith("described in the instructions."))
        self.assertEqual((audit["parts_total"], audit["parts_asked"]), (1, 1))

    def test_one_correction_then_failure(self):
        bad = answer(secondary="public_procurement", secondary_reason="")
        result, _ = classifier.classify(client_with(bad, answer()), "S", IDS, pack())
        self.assertEqual(result["primary_domain"], "energy_sustainability")
        with self.assertRaisesRegex(classifier.Unclassified, "after one correction"):
            classifier.classify(client_with(bad, bad), "S", IDS, pack())
        cut = ChatResult(ok=True, finish_reason="length", content='{"subject": "x')
        with self.assertRaisesRegex(classifier.Unclassified, "cut off"):
            classifier.classify(client_with(cut, cut), "S", IDS, pack())

    def test_connection_problems_are_not_classifications(self):
        lost = ChatResult(ok=False, error_kind="timeout", error="Timed out")
        with self.assertRaises(classifier.ConnectionLost):
            classifier.classify(client_with(lost), "S", IDS, pack())
        with self.assertRaises(classifier.ConnectionLost):
            classifier.classify(client_with(answer(), discovered=False), "S", IDS, pack())

    def test_long_provision_uses_its_first_parts_and_the_majority(self):
        text = " ".join(f"Sentence number {n} about the energy audit of enterprises." for n in range(2600))
        long_pack = pack(text=text)
        total = len(classifier.split_target(long_pack["provision"], classifier.TARGET_CHARS))
        self.assertGreater(total, classifier.MAX_PARTS)
        client = client_with(answer(), answer("public_procurement"), answer(), answer(),
                             answer(secondary="public_procurement"))
        result, audit = classifier.classify(client, "S", IDS, long_pack)
        self.assertEqual(client.chat.call_count, classifier.MAX_PARTS)
        self.assertEqual((audit["parts_total"], audit["parts_asked"]), (total, 5))
        self.assertEqual((result["primary_domain"], result["secondary_domain"], result["parts_disagree"]),
                         ("energy_sustainability", "public_procurement", True))
        second = json.loads(client.chat.call_args_list[1].args[0][1]["content"].partition("\n\nTASK:")[0])
        self.assertEqual(second["target"]["part"], f"2 of {total}")
        self.assertEqual(second["position"][0]["relation"], "opening words of the whole target")

    def test_one_stray_part_out_of_five_gives_no_secondary_domain(self):
        answers = [json.loads(answer().content) for _ in range(4)] + [json.loads(answer("waste_management").content)]
        result = classifier.combine(answers)
        # One of five is below a quarter of the parts... but a quarter of five rounds up to two.
        self.assertEqual((result["primary_domain"], result["secondary_domain"]), ("energy_sustainability", "none"))
        self.assertTrue(result["parts_disagree"])

    def test_parts_without_any_domain_stay_without_domain(self):
        answers = [json.loads(answer("no_fitting_domain").content) for _ in range(3)]
        self.assertEqual(classifier.combine(answers)["primary_domain"], "no_fitting_domain")

    def test_too_long_part_is_made_smaller(self):
        limit = ChatResult(ok=False, error_kind="context_limit", error="maximum context length")
        text = " ".join(f"Sentence number {n} about the energy audit." for n in range(250))
        client = client_with(limit, answer(), answer())
        result, audit = classifier.classify(client, "S", IDS, pack(text=text))
        self.assertEqual([part["part"] for part in audit["parts"]], ["1.1", "1.2"])
        self.assertEqual(result["primary_domain"], "energy_sustainability")


class FinishTests(unittest.TestCase):
    def item(self, **changes):
        return {"level": "DIRECT", "area": ["energy_efficiency_operations"], **changes}

    def result(self, **changes):
        return classifier.combine([classifier.validate(json.loads(answer(**changes).content), IDS)])

    def test_departments_titles_and_no_flag_when_domain_matches_the_business_area(self):
        done = classifier.finish(self.result(), self.item(), DOMAINS)
        self.assertEqual(done["primary_title"], "Energie & Nachhaltigkeit")
        self.assertIn("Energiesysteme", done["departments"])
        self.assertEqual((done["secondary_domain"], done["secondary_departments"], done["check"]), (None, [], []))
        self.assertEqual(done["business_area_domains"], ["energy_sustainability"])

    def test_flags(self):
        other = classifier.finish(self.result(primary="waste_management"), self.item(), DOMAINS)
        self.assertEqual(other["check"], ["differs_from_business_area"])
        agrees = classifier.finish(self.result(primary="waste_management", secondary="energy_sustainability"),
                                   self.item(), DOMAINS)
        self.assertEqual(agrees["check"], [])
        nothing = classifier.finish(self.result(primary="no_fitting_domain"), self.item(), DOMAINS)
        self.assertEqual((nothing["check"], nothing["departments"]), (["no_fitting_domain"], []))
        customs = classifier.finish(self.result(primary="customs_trade"),
                                    self.item(area=["imports_trade_defence"], to_confirm="Imports itself?"), DOMAINS)
        self.assertEqual((customs["check"], customs["departments"], customs["to_confirm"]),
                         ([], [], "Imports itself?"))
        unknown_area = classifier.finish(self.result(primary="waste_management"), self.item(area=[]), DOMAINS)
        self.assertEqual(unknown_area["check"], [])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.first = make_run(self.root, "first", [[
            record("A", "Article 1"), record("A", "Article 2", level="IGNORE"),
            record("A", "Article 3", level="UNSURE", area=["imports_trade_defence"]),
            record("A", "Article 4", level="UNSURE"), record("A", "Article 5", level="UNSURE"),
            record("B", "Article 1", level="INDIRECT", area=["waste_management"])],
            [record("C", "Article 1"), record("C", "Article 2", level="INDIRECT")]])
        self.second = make_run(self.root, "second", [[record("C", "Article 9", area=["public_procurement"])]])
        self.unsure = self.root / "unsure"
        self.unsure.mkdir()
        (self.unsure / "results.json").write_text(json.dumps({"A": {
            "Article 3": {"level": "INDIRECT", "area": ["imports_trade_defence"],
                          "to_confirm": "Does Max Bögl import steel itself?"},
            "Article 4": {"level": "IGNORE", "area": []}}}), encoding="utf-8")
        self.out = self.root / "out"

    def main(self, client, *options):
        argv = ["classify", "--runs", str(self.first), str(self.second), "--unsure-dir", str(self.unsure),
                "--output-dir", str(self.out), "--workers", "1", *options]
        with patch.object(sys, "argv", argv), patch.object(classifier.QwenClient, "from_env", return_value=client), \
                patch.object(classifier.time, "sleep") as sleep:
            code = classifier.main()
        return code, sleep

    def test_collect_takes_kept_provisions_the_unsure_decisions_and_the_later_run(self):
        items, facts = classifier.collect([self.first, self.second], self.unsure)
        self.assertEqual([(i["celex"], i["target"], i["level"]) for i in items],
                         [("A", "Article 1", "DIRECT"), ("A", "Article 3", "INDIRECT"),
                          ("B", "Article 1", "INDIRECT"), ("C", "Article 9", "DIRECT")])
        self.assertEqual(items[1]["to_confirm"], "Does Max Bögl import steel itself?")
        self.assertEqual((facts["DIRECT"], facts["INDIRECT"], facts["from_unsure_pass"],
                          facts["unsure_without_decision"]), (2, 2, 1, 1))

    def test_full_run_writes_all_outputs(self):
        client = client_with(answer(), answer("customs_trade"),
                             answer("waste_management", "dangerous_goods"), answer("energy_sustainability"))
        code, _ = self.main(client)
        self.assertEqual(code, 0)
        results = json.loads((self.out / "results.json").read_text(encoding="utf-8"))
        first = results["A"]["Article 1"]
        self.assertEqual((first["level"], first["primary_domain"], first["secondary_domain"], first["check"]),
                         ("DIRECT", "energy_sustainability", None, []))
        self.assertEqual(results["A"]["Article 3"]["to_confirm"], "Does Max Bögl import steel itself?")
        self.assertEqual(results["B"]["Article 1"]["secondary_domain"], "dangerous_goods")
        self.assertEqual(results["C"]["Article 9"]["check"], ["differs_from_business_area"])
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["provisions_classified"], summary["provisions_failed"],
                          summary["with_secondary_domain"], summary["to_check"]),
                         (4, 0, 1, {"differs_from_business_area": 1}))
        self.assertEqual(summary["departments"]["Umweltmanagement"],
                         {"acts": 1, "provisions": 2, "direct": 0, "indirect": 2})
        with (self.out / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertEqual((len(rows), rows[0][5]), (5, "primary_domain"))
        # The department sees the act once, with only its provisions.
        view = json.loads((self.out / "departments" / "Umweltmanagement.json").read_text(encoding="utf-8"))
        self.assertEqual([(act["celex"], act["title"]) for act in view["acts"]], [("B", "Test act B")])
        self.assertEqual([(p["provision"], p["role"], p["domain"]) for p in view["acts"][0]["provisions"]],
                         [("Article 1", "primary", "waste_management"), ("Article 1", "secondary", "dangerous_goods")])
        open_customs = json.loads((self.out / "departments" / "no_department_yet.json").read_text(encoding="utf-8"))
        self.assertEqual(open_customs["acts"][0]["provisions"][0]["domain"], "customs_trade")
        # Starting again asks nothing.
        again = client_with()
        self.assertEqual(self.main(again)[0], 0)
        again.chat.assert_not_called()

    def test_count_only_levels_and_sample(self):
        client = client_with()
        self.assertEqual(self.main(client, "--count-only")[0], 0)
        client.chat.assert_not_called()
        self.assertFalse(self.out.exists())
        client = client_with(answer(), answer())
        self.assertEqual(self.main(client, "--levels", "DIRECT")[0], 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["levels"], {"DIRECT": 2})
        client = client_with(answer(), answer())
        self.assertEqual(self.main(client)[0], 0)                      # the two INDIRECT ones are added
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_classified"], 4)

    def test_sample_mixes_levels_and_is_stable(self):
        items, _ = classifier.collect([self.first, self.second], self.unsure)
        chosen = classifier.sample(items, 3)
        self.assertEqual(chosen, classifier.sample(items, 3))
        self.assertEqual(sorted(i["level"] for i in chosen), ["DIRECT", "DIRECT", "INDIRECT"])
        self.assertEqual(len(classifier.sample(items, 100)), 4)

    def test_outage_waits_then_continues(self):
        lost = ChatResult(ok=False, error_kind="connection", error="WinError 10060")
        client = client_with(answer(), lost, answer(), answer(), answer())
        client.discover_results = [{"ok": True}, {"ok": False, "error": "No model list found."},
                                   {"ok": False, "error": "No model list found."}, {"ok": True}]
        code, sleep = self.main(client)
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] / 60 for call in sleep.call_args_list], [15, 30])
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_classified"], 4)

    def test_endpoint_down_at_the_start_waits(self):
        client = client_with(answer(), answer(), answer(), answer(), discovered=False)
        client.discover_results = [{"ok": False, "error": "No model list found."}, {"ok": True}]
        code, sleep = self.main(client)
        self.assertEqual((code, [call.args[0] / 60 for call in sleep.call_args_list]), (0, [15]))

    def test_failed_and_unreadable_provisions_do_not_stop_the_run(self):
        bad = answer(primary_reason="")
        source = next((self.first / "batches" / "0001" / "audit" / "B").glob("*.json"))
        source.write_text("{not json", encoding="utf-8")
        code, _ = self.main(client_with(answer(), bad, bad, answer()))
        self.assertEqual(code, 0)
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual((summary["provisions_classified"], summary["provisions_failed"]), (2, 2))
        self.assertEqual({f["celex"] for f in summary["failures"]}, {"A", "B"})

    def test_changed_domains_need_a_new_folder(self):
        self.main(client_with(answer(), answer(), answer(), answer()))
        with patch.object(classifier, "TASK", "TASK: something else."):
            self.assertEqual(self.main(client_with())[0], 1)

    def test_missing_run_folder_is_reported(self):
        argv = ["classify", "--runs", str(self.root / "nothing"), "--output-dir", str(self.out)]
        with patch.object(sys, "argv", argv):
            self.assertEqual(classifier.main(), 1)


if __name__ == "__main__":
    unittest.main()
