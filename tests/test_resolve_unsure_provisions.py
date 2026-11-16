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
import resolve_unsure_provisions as resolver  # noqa: E402

PATHS = ["client_requirements", "suppliers_prices", "purchased_products", "market_funding"]


def answer(link="suppliers_prices", **changes):
    value = {"regulated_thing": "steel products", "reason": "The duty changes the price of steel.",
             "link": link, "connection": "" if link == "NONE" else "steel for structures"}
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


def record(celex, target, level="UNSURE", text="Importers shall pay the duty on steel.", parts=None,
           question="Does Max Bögl import steel itself?"):
    details = {"topic": "imports_trade_defence", "subject": "duty on steel", "actor": "importers",
               "open_question": question}
    return {
        "celex": celex, "target": target, "kind": "ARTICLE", "target_id": f"{celex}:{target}",
        "level": level, "reason": "First reason. To check: " + question, "area": ["imports_trade_defence"],
        "details": details,
        "model_attempt": {"parts": parts or [{"part": "1", "decision": {"level": level}, "evidence": details}]},
        "evidence_pack": {"document": {"celex": celex, "version_id": "v", "title": "A test act"},
                          "provision": {"locator": target, "text": text}, "direct_references": [],
                          "essential_context": [], "footnotes": [], "unavailable_references": []},
    }


def make_run(root, name, batches):
    """batches: list of lists of records. Writes the folder layout of a screening run."""
    run = root / name
    for number, records in enumerate(batches, 1):
        folder = run / "batches" / f"{number:04d}"
        results = []
        for index, saved in enumerate(records):
            file = f"audit/{saved['celex']}/article_{index}.json"
            (folder / file).parent.mkdir(parents=True, exist_ok=True)
            (folder / file).write_text(json.dumps(saved), encoding="utf-8")
            results.append({"celex": saved["celex"], "target": saved["target"], "kind": saved["kind"],
                            "target_id": saved["target_id"], "file": file, "level": saved["level"],
                            "reason": saved["reason"], "area": saved["area"]})
        (folder / "manifest.json").write_text(json.dumps({r["celex"]: "v" for r in records}), encoding="utf-8")
        (folder / "audit" / "run_summary.json").write_text(json.dumps({"results": results}), encoding="utf-8")
    return run


class CollectTests(unittest.TestCase):
    def test_only_unsure_and_later_run_replaces_the_act(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_run(root, "first", [[record("A", "Article 1"), record("A", "Article 2", level="DIRECT")],
                                             [record("B", "Article 1"), record("B", "Article 9")]])
            second = make_run(root, "second", [[record("B", "Article 5"), record("B", "Article 6", level="IGNORE")]])
            found = resolver.collect([first, second])
            self.assertEqual([(i["celex"], i["target"], i["run"]) for i in found],
                             [("A", "Article 1", "first"), ("B", "Article 5", "second")])
            self.assertEqual(resolver.sample(found, 1), resolver.sample(found, 1))
            with self.assertRaisesRegex(ValueError, "No screened batches"):
                resolver.collect([root / "missing"])


class ResolveTests(unittest.TestCase):
    def test_path_means_indirect_and_none_means_ignore(self):
        decision, audit = resolver.resolve(client_with(answer()), "SYSTEM", PATHS, record("A", "Article 1"))
        self.assertEqual((decision["level"], decision["link"]), ("INDIRECT", "suppliers_prices"))
        decision, _ = resolver.resolve(client_with(answer("NONE")), "SYSTEM", PATHS, record("A", "Article 1"))
        self.assertEqual((decision["level"], decision["link"], decision["connection"]), ("IGNORE", "NONE", ""))
        self.assertEqual(len(audit["requests"]), 1)

    def test_request_has_the_text_the_first_findings_and_the_task_last(self):
        client = client_with(answer())
        resolver.resolve(client, "SYSTEM", PATHS, record("A", "Article 1"))
        messages = client.chat.call_args.args[0]
        self.assertEqual(messages[0], {"role": "system", "content": "SYSTEM"})
        evidence, _, task = messages[1]["content"].partition("\n\nTASK:")
        sent = json.loads(evidence)
        self.assertIn("Importers shall pay", sent["target"]["text"])
        self.assertEqual(sent["first_pass"]["open_question"], "Does Max Bögl import steel itself?")
        self.assertTrue(task.strip().endswith("described in the instructions."))
        schema = client.chat.call_args.kwargs["schema"]
        self.assertEqual(schema["properties"]["link"]["enum"], [*PATHS, "NONE"])

    def test_invalid_answer_gets_one_correction_then_fails(self):
        bad = answer(connection="")                      # a path without the part of the business it touches
        decision, _ = resolver.resolve(client_with(bad, answer()), "S", PATHS, record("A", "Article 1"))
        self.assertEqual(decision["level"], "INDIRECT")
        with self.assertRaisesRegex(resolver.Unresolved, "after one correction"):
            resolver.resolve(client_with(bad, bad), "S", PATHS, record("A", "Article 1"))
        cut = ChatResult(ok=True, finish_reason="length", content='{"regulated_thing": "x')
        with self.assertRaisesRegex(resolver.Unresolved, "cut off"):
            resolver.resolve(client_with(cut, cut), "S", PATHS, record("A", "Article 1"))

    def test_connection_failure_is_not_a_decision(self):
        lost = ChatResult(ok=False, error_kind="timeout", error="Timed out")
        with self.assertRaises(resolver.ConnectionLost):
            resolver.resolve(client_with(lost), "S", PATHS, record("A", "Article 1"))

    def test_long_target_asks_only_the_unsure_parts_and_stops_at_the_first_link(self):
        text = " ".join(f"Sentence number {n} about importers of steel products." for n in range(900))
        saved = record("A", "Annex I", text=text)
        count = len(resolver.split_target(saved["evidence_pack"]["provision"], resolver.TARGET_CHARS))
        self.assertGreaterEqual(count, 3)
        saved["model_attempt"]["parts"] = [
            {"part": str(n), "decision": {"level": "UNSURE" if n in (2, 3) else "IGNORE"}, "evidence": {}}
            for n in range(1, count + 1)]
        client = client_with(answer("NONE"), answer())
        decision, audit = resolver.resolve(client, "S", PATHS, saved)
        self.assertEqual([part["part"] for part in audit["parts"]], ["2", "3"])
        self.assertEqual(decision["level"], "INDIRECT")
        self.assertIn(f"part 3 of {count}", decision["reason"])
        client = client_with(answer())
        resolver.resolve(client, "S", PATHS, saved)
        self.assertEqual(client.chat.call_count, 1)      # part 2 already reaches the business


class RunTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.run_folder = make_run(self.root, "first", [[
            record("A", "Article 1"), record("A", "Article 2", question=""), record("B", "Article 7")]])
        self.out = self.root / "out"

    def main(self, client, *options):
        argv = ["resolve", "--runs", str(self.run_folder), "--output-dir", str(self.out), "--workers", "1",
                *options]
        with patch.object(sys, "argv", argv), patch.object(resolver.QwenClient, "from_env", return_value=client), \
                patch.object(resolver.time, "sleep") as sleep:
            code = resolver.main()
        return code, sleep

    def test_full_run_writes_results_summary_and_review_list(self):
        client = client_with(answer(), answer("purchased_products"), answer("NONE"))
        code, _ = self.main(client)
        self.assertEqual(code, 0)
        results = json.loads((self.out / "results.json").read_text(encoding="utf-8"))
        first = results["A"]["Article 1"]
        self.assertEqual((first["level"], first["indirect_path"], first["previous_level"]),
                         ("INDIRECT", "suppliers_prices", "UNSURE"))
        self.assertEqual(first["to_confirm"], "Does Max Bögl import steel itself?")
        self.assertEqual(first["area"], ["imports_trade_defence"])
        self.assertNotIn("to_confirm", results["A"]["Article 2"])
        self.assertEqual((results["B"]["Article 7"]["level"], results["B"]["Article 7"]["area"]), ("IGNORE", []))
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["counts"], summary["provisions_open"]), ({"INDIRECT": 2, "IGNORE": 1}, 0))
        with (self.out / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=";"))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][4], "new_level")
        # Starting again asks nothing.
        again = client_with()
        self.assertEqual(self.main(again)[0], 0)
        again.chat.assert_not_called()

    def test_sample_then_full_run_continues(self):
        code, _ = self.main(client_with(answer()), "--sample", "1")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_decided"], 1)
        client = client_with(answer(), answer())
        self.assertEqual(self.main(client)[0], 0)
        self.assertEqual(client.chat.call_count, 2)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_decided"], 3)

    def test_outage_waits_and_continues_without_losing_results(self):
        lost = ChatResult(ok=False, error_kind="connection", error="WinError 10060")
        client = client_with(answer(), lost, answer(), answer("NONE"))
        client.discover_results = [{"ok": True}, {"ok": False, "error": "No model list found."},
                                   {"ok": False, "error": "No model list found."}, {"ok": True}]
        code, sleep = self.main(client)
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] / 60 for call in sleep.call_args_list], [15, 30])
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["counts"],
                         {"INDIRECT": 2, "IGNORE": 1})

    def test_failed_provision_is_reported_and_retried_on_the_next_start(self):
        bad = answer(link="somewhere")
        code, _ = self.main(client_with(answer(), bad, bad, answer()))
        self.assertEqual(code, 0)
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual((summary["provisions_decided"], summary["provisions_failed"]), (2, 1))
        client = client_with(answer())
        self.main(client)
        self.assertEqual(client.chat.call_count, 1)
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_failed"], 0)

    def test_changed_prompt_needs_a_new_folder(self):
        self.main(client_with(answer(), answer(), answer()))
        with patch.object(resolver, "TASK", "TASK: something else."):
            code, _ = self.main(client_with())
        self.assertEqual(code, 1)

    def test_run_starts_by_finding_the_endpoint_and_waits_when_it_is_down(self):
        client = client_with(answer(), answer(), answer(), discovered=False)
        client.discover_results = [{"ok": False, "error": "No model list found."}, {"ok": True}]
        code, sleep = self.main(client)
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] / 60 for call in sleep.call_args_list], [15])
        self.assertEqual(json.loads((self.out / "summary.json").read_text())["provisions_decided"], 3)

    def test_request_that_fails_once_while_qwen_answers_continues_quickly(self):
        lost = ChatResult(ok=False, error_kind="timeout", error="Timed out")
        code, sleep = self.main(client_with(answer(), lost, answer(), answer()))
        self.assertEqual(code, 0)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [15])

    def test_list_only_asks_nothing(self):
        client = client_with()
        self.assertEqual(self.main(client, "--list-only")[0], 0)
        client.chat.assert_not_called()
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
