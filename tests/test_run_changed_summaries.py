import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_changed_summaries as run  # noqa: E402


class RunTests(unittest.TestCase):
    def test_the_four_steps_run_in_order_with_their_folders(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            changed = tmp / "20261123T190000Z"
            changed.mkdir()
            (changed / "index.json").write_text("{}")
            calls = []

            def call(name, *arguments):
                arguments = [str(a) for a in arguments]
                calls.append((name, arguments))
                if name == "screen_new_articles.py":
                    out = Path(arguments[arguments.index("--output-dir") + 1])
                    (out / "batches" / "0001").mkdir(parents=True)
                if name == "classify_eurlex_provisions.py":
                    out = Path(arguments[arguments.index("--output-dir") + 1])
                    out.mkdir(parents=True)
                    (out / "results.json").write_text(json.dumps({}))
                return 0

            with patch.object(run, "NEW", tmp / "new"), patch.object(run, "call", call):
                self.assertEqual(run.main(["--changed-dir", str(changed), "--sample", "20"]), 0)
        names = [name for name, _ in calls]
        self.assertEqual(names, ["screen_new_articles.py", "resolve_unsure_provisions.py",
                                 "classify_eurlex_provisions.py", "summarize_changed_provisions.py"])
        summary = calls[-1][1]
        self.assertIn("--extra-classification-dir", summary)
        self.assertTrue(summary[summary.index("--extra-classification-dir") + 1].endswith(
            str(Path("20261123T190000Z") / "classification")))
        self.assertEqual(summary[-2:], ["--sample", "20"])

    def test_nothing_inserted_goes_straight_to_the_summaries(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            changed = tmp / "export"
            changed.mkdir()
            (changed / "index.json").write_text("{}")
            calls = []
            with patch.object(run, "NEW", tmp / "new"), \
                    patch.object(run, "call", lambda name, *a: calls.append(name) or 0):
                run.main(["--changed-dir", str(changed)])
        self.assertEqual(calls, ["screen_new_articles.py", "summarize_changed_provisions.py"])

    def test_a_failed_screening_is_tried_again(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            changed = tmp / "export"
            changed.mkdir()
            (changed / "index.json").write_text("{}")
            codes = iter([1, 0, 0])
            calls = []
            with patch.object(run, "NEW", tmp / "new"), patch.object(run, "WAIT_SECONDS", 0), \
                    patch.object(run, "call", lambda name, *a: calls.append(name) or next(codes)):
                run.main(["--changed-dir", str(changed)])
        self.assertEqual(calls, ["screen_new_articles.py", "screen_new_articles.py",
                                 "summarize_changed_provisions.py"])

    def test_only_relevance_stops_before_the_summaries(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            changed = tmp / "export"
            changed.mkdir()
            (changed / "index.json").write_text("{}")
            calls = []

            def call(name, *arguments):
                calls.append(name)
                if name == "screen_new_articles.py":
                    out = Path(str(arguments[list(map(str, arguments)).index("--output-dir") + 1]))
                    (out / "batches" / "0001").mkdir(parents=True)
                return 0

            with patch.object(run, "NEW", tmp / "new"), patch.object(run, "call", call):
                self.assertEqual(run.main(["--changed-dir", str(changed), "--only-relevance"]), 0)
        self.assertEqual(calls, ["screen_new_articles.py", "resolve_unsure_provisions.py",
                                 "classify_eurlex_provisions.py"])

    def test_a_folder_without_the_export_stops(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(run.main(["--changed-dir", tmp]), 1)


if __name__ == "__main__":
    unittest.main()
