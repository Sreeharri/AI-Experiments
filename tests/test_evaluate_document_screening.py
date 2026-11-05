import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "screening_eval", Path(__file__).resolve().parents[1] / "tools/evaluate_document_screening.py")
ev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ev)


class EvaluationTests(unittest.TestCase):
    def test_known_confusion_matrix_and_metrics(self):
        m = ev.calculate_metrics(65, 31, 5, 99)
        self.assertAlmostEqual(m["precision"], 65 / 96)
        self.assertAlmostEqual(m["recall"], 65 / 70)
        self.assertAlmostEqual(m["accuracy"], 164 / 200)
        self.assertAlmostEqual(m["f1"], 130 / 166)
        self.assertLess(m["recall_ci95"]["lower"], m["recall"])
        self.assertGreater(m["recall_ci95"]["upper"], m["recall"])

    def test_undefined_denominators_are_null(self):
        self.assertIsNone(ev.calculate_metrics(0, 0, 0, 4)["recall"])
        self.assertIsNone(ev.calculate_metrics(0, 0, 0, 4)["precision"])
        self.assertIsNone(ev.calculate_metrics(0, 0, 0, 0)["accuracy"])
        self.assertEqual(ev.calculate_metrics(0, 0, 4, 4)["f1"], 0)

    def test_only_label_column_used_and_continuations_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "labels.csv"
            path.write_text('ID,Title,Evidence pack,Label,LLM label,Astro label\n'
                            'B,Act B,text,DIRECT,IGNORE,IGNORE\n'
                            ',,continued,,,\nA,Act A,text,IGNORE,DIRECT,DIRECT\n', encoding="utf-8")
            labels = ev.read_labels(path)
            self.assertEqual(len(labels), 2)
            self.assertEqual(labels["B"]["label"], "DIRECT")
            self.assertEqual(labels["A"]["label"], "IGNORE")

    def test_bad_or_duplicate_truth_fails(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "labels.csv"
            for text in ('ID,Label\nA,DIRECT\na,IGNORE\n', 'ID,Label\nA,\n',
                         'ID,Label\nA,NEEDS_REVIEW\n', 'ID,Label\n,DIRECT\n'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    ev.read_labels(path)

    def setup_run(self, folder, predictions):
        labels = {}
        for identifier, (truth, pred) in predictions.items():
            labels[identifier] = {"id": identifier, "title": identifier,
                                  "label": truth, "label_row": len(labels) + 2}
            if pred is not None:
                (folder / f"{identifier}.json").write_text(json.dumps(
                    {"status": "ok", "celex": identifier,
                     "decision": {"level": pred, "reason": "example", "anchor_ids": []}}))
        return labels

    def test_join_by_id_and_direct_indirect_equal(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            labels = self.setup_run(folder, {"B": ("INDIRECT", "DIRECT"), "A": ("DIRECT", "INDIRECT"),
                                            "D": ("IGNORE", "IGNORE"), "C": ("IGNORE", "DIRECT")})
            report, rows = ev.evaluate(labels, folder)
            self.assertEqual(report["confusion_matrix"], {"TP": 2, "FP": 1, "FN": 0, "TN": 1})
            self.assertEqual([r["id"] for r in rows], ["B", "A", "D", "C"])

    def test_missing_failed_invalid_and_mismatched_predictions_stay_unresolved(self):
        for bad in (None, {"status": "failed", "decision": {"level": "IGNORE"}},
                    {"status": "ok", "celex": "OTHER", "decision": {"level": "DIRECT"}},
                    {"status": "ok", "decision": {"level": "NEEDS_REVIEW"}},
                    {"status": "prepared"}, ["not an object"], "corrupt"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as td:
                folder = Path(td)
                labels = self.setup_run(folder, {"A": ("DIRECT", None), "B": ("IGNORE", "IGNORE")})
                if bad is not None:
                    (folder / "A.json").write_text(bad if bad == "corrupt" else json.dumps(bad))
                report, rows = ev.evaluate(labels, folder)
                self.assertFalse(report["complete"])
                self.assertIsNone(report["metrics"])
                self.assertEqual(report["unresolved_relevant_acts"], 1)
                self.assertEqual(report["end_to_end_relevant_retention"], 0)
                self.assertEqual(report["confusion_matrix"]["TN"], 1)
                self.assertEqual(rows[0]["outcome"], "UNRESOLVED")

    def test_manifest_selection_prevents_scoring_stale_files(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            labels = self.setup_run(folder, {"A": ("DIRECT", "DIRECT")})
            (folder / "manifest.json").write_text(json.dumps({"selected_celex": []}))
            report, rows = ev.evaluate(labels, folder)
            self.assertFalse(report["complete"])
            self.assertIn("manifest", rows[0]["issue"])

    def test_cli_outputs_reports_without_changing_sources(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            truth = folder / "labels.csv"
            truth.write_text("ID,Title,Label\nA,Act A,DIRECT\nB,Act B,IGNORE\n")
            self.setup_run(folder, {"A": ("DIRECT", "IGNORE"), "B": ("IGNORE", "DIRECT")})
            before = ev.sha256(truth)
            code = ev.main(["--labels", str(truth), "--run", str(folder), "--out", str(folder / "out")])
            self.assertEqual(code, 0)
            self.assertEqual(ev.sha256(truth), before)
            self.assertEqual(json.loads((folder / "out/metrics.json").read_text())["confusion_matrix"],
                             {"TP": 0, "FP": 1, "FN": 1, "TN": 0})
            for name in ("per_act.csv", "false_negatives.csv", "false_positives.csv", "report.md",
                         "ground_truth_snapshot.csv", "unresolved.csv"):
                self.assertTrue((folder / "out" / name).exists())


if __name__ == "__main__":
    unittest.main()
