from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest

import run_provisions_overnight as overnight


class FakeClock:
    def __init__(self):
        self.time = datetime(2026, 11, 12, 22, 0)
        self.slept = []

    def now(self):
        return self.time

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.time += timedelta(seconds=seconds)


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out = Path(self.directory.name)
        (self.out / "batch_plan.json").write_text(json.dumps({"batches": [{"targets": 100}] * 10}))
        self.stream = io.StringIO()
        self.clock = FakeClock()

    def summary(self, done, pending=None, stop="Batch 3 stopped.", failed=0, counts=None):
        pending = 1000 - done - failed if pending is None else pending
        (self.out / "run_summary.json").write_text(json.dumps({
            "targets_exported": done, "targets_not_assessable": 0, "targets_failed": failed,
            "targets_pending": pending, "stop_reason": stop, "counts": counts or {"DIRECT": done}}))

    def supervisor(self, runs, checks, waits=(15, 30, 45, 60)):
        """runs: list of (exit code, saw batch, tail, targets done afterwards, extra summary fields)."""
        runs, checks = list(runs), list(checks)
        self.calls = []

        def run(command, progress, interrupted):
            code, saw, tail, done, extra = runs.pop(0)
            self.calls.append(command)
            if done is not None:
                self.summary(done, **{k: v for k, v in extra.items() if k != "ctrl_c"})
            if extra.get("ctrl_c"):
                interrupted.append(True)
            return code, saw, tail

        def check(env_file):
            return checks.pop(0)

        sup = overnight.Supervisor(["--workers", "4"], self.out, self.out / ".env", waits, run=run, check=check,
                                   sleep=self.clock.sleep, now=self.clock.now, stream=self.stream)
        self.remaining = (runs, checks)
        return sup

    def log(self):
        return (self.out / "overnight_log.txt").read_text(encoding="utf-8")


class SupervisorTests(Base):
    LOST = ["[7/100] 32006R1870 Article 9",
            "Provision screening stopped: 32006R1870 Article 9: <urlopen error [WinError 10060] Ein "
            "Verbindungsversuch ist fehlgeschlagen>", "Batch 3 stopped. See the terminal output and batch audit."]

    def test_outage_waits_15_30_45_then_hourly_and_resumes(self):
        self.summary(200)
        sup = self.supervisor(
            runs=[(1, True, self.LOST, 250, {}), (0, True, ["done"], 1000, {"stop": None, "pending": 0})],
            checks=[(False, "No model list found.")] * 5 + [(True, "")])
        self.assertEqual(sup.supervise(), 0)
        waited = sum(self.clock.slept) / 60
        self.assertEqual(waited, 15 + 30 + 45 + 60 + 60)
        log = self.log()
        for text in ("Run stopped: Provision screening stopped: 32006R1870 Article 9: <urlopen error",
                     "(15 min)", "(30 min)", "(45 min)", "(60 min)", "Qwen answers again. Resuming.",
                     "Finished: 1,000 targets."):
            self.assertIn(text, log)
        self.assertEqual(log.count("(60 min)"), 2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.remaining, ([], []))

    def test_single_timeout_resumes_at_once_when_qwen_answers(self):
        self.summary(200)
        sup = self.supervisor(runs=[(1, True, self.LOST, 260, {}), (0, True, [], 1000, {"stop": None, "pending": 0})],
                              checks=[(True, "")])
        self.assertEqual(sup.supervise(), 0)
        self.assertEqual(self.clock.slept, [15])
        self.assertIn("Qwen answers, resuming in 15 s", self.log())

    def test_schedule_starts_again_after_progress(self):
        self.summary(200)
        sup = self.supervisor(
            runs=[(1, True, self.LOST, 250, {}), (1, True, self.LOST, 300, {}),
                  (0, True, [], 1000, {"stop": None, "pending": 0})],
            checks=[(False, "down"), (False, "down"), (True, ""), (False, "down"), (True, "")])
        self.assertEqual(sup.supervise(), 0)
        # First outage: 15 and 30 minutes. After the run made progress, the next outage starts at 15 again.
        self.assertEqual(sum(self.clock.slept) / 60, 15 + 30 + 15)
        self.assertEqual(self.log().count("(15 min)"), 2)
        self.assertEqual(self.log().count("(30 min)"), 1)

    def test_requests_failing_while_model_list_answers_fall_back_to_waiting(self):
        self.summary(200)
        sup = self.supervisor(
            runs=[(1, True, self.LOST, 200, {}), (1, True, self.LOST, 200, {}), (1, True, self.LOST, 200, {}),
                  (0, True, [], 1000, {"stop": None, "pending": 0})],
            checks=[(True, ""), (True, ""), (True, ""), (True, "")])
        self.assertEqual(sup.supervise(), 0)
        self.assertEqual(self.log().count("resuming in 1 min"), 2)
        self.assertIn("(15 min)", self.log())

    def test_other_errors_stop_instead_of_waiting_forever(self):
        self.summary(200)
        tail = ["[3/100] 32006R1870 Article 9", "KeyError: 'level'"]
        sup = self.supervisor(runs=[(1, True, tail, 200, {})] * 3, checks=[(True, "")] * 3)
        self.assertEqual(sup.supervise(), 1)
        self.assertIn("does not look like a connection problem: KeyError: 'level'", self.log())

    def test_startup_problem_stops_without_checking_qwen(self):
        sup = self.supervisor(runs=[(1, False, ["Provision screening stopped: lock exists."], None, {})], checks=[])
        self.assertEqual(sup.supervise(), 1)
        self.assertIn("stopped before screening", self.log())

    def test_failed_targets_are_retried_once(self):
        self.summary(200)
        done = {"stop": None, "pending": 0, "failed": 5}
        sup = self.supervisor(runs=[(1, True, [], 995, done), (1, True, [], 995, done)], checks=[])
        self.assertEqual(sup.supervise(), 1)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("5 targets still have no valid answer", self.log())

    def test_ctrl_c_stops_during_a_run_and_during_a_wait(self):
        self.summary(200)
        sup = self.supervisor(runs=[(1, True, [], 220, {"ctrl_c": True})], checks=[])
        self.assertEqual(sup.supervise(), 130)

        def interrupt(seconds):
            raise KeyboardInterrupt

        sup = self.supervisor(runs=[(1, True, self.LOST, 230, {})], checks=[(False, "down")])
        sup.sleep = interrupt
        self.assertEqual(sup.supervise(), 130)
        self.assertIn("Stopped by Ctrl+C while waiting.", self.log())

    def test_options_are_passed_to_the_runner(self):
        sup = self.supervisor(runs=[], checks=[])
        self.assertEqual(sup.command[1], str(overnight.RUNNER))
        self.assertEqual(sup.command[2:], ["--workers", "4"])


class ProgressTests(Base):
    def test_progress_line_counts_batches_and_targets(self):
        progress = overnight.Progress(self.out, self.stream)
        progress.start_batch(4, 10)
        progress.target(20, "32006R1870 Article 9", 0)
        progress.target(80, "32006R1870 Article 12", 600)
        self.assertEqual(progress.done, 380)
        line = progress.line()
        self.assertIn("38.0%", line)
        self.assertIn("380/1,000", line)
        self.assertIn("batch 4/10", line)
        self.assertIn("360/h", line)
        self.assertIn("ETA 1h 43m", line)
        self.assertIn("waiting for Qwen", progress.line("waiting for Qwen"))

    def test_resume_starts_from_the_saved_count(self):
        self.summary(450)
        progress = overnight.Progress(self.out, self.stream)
        self.assertEqual(progress.done, 450)
        progress.start_batch(5, 10)
        progress.target(3, "x", 0)
        self.assertEqual(progress.done, 450)


class ChildProcessTests(Base):
    def test_output_is_turned_into_one_progress_line(self):
        script = self.out / "fake_runner.py"
        script.write_text(textwrap.dedent('''
            import sys
            print("1,000 targets from 50 acts in 10 batches")
            print("Batch 2/10 · 100 targets")
            for n in range(1, 4):
                print(f"[{n}/100] 32006L0112 Article {n}")
            print("Saved 100 targets to somewhere")
            print("3 targets are not assessable. See not_assessable_targets in audit/run_summary.json.")
            print("Provision screening stopped: 32006L0112 Article 3: <urlopen error timed out>")
            sys.exit(1)
        '''), encoding="utf-8")
        progress = overnight.Progress(self.out, self.stream)
        code, saw_batch, tail = overnight.run_child([sys.executable, str(script)], progress, [])
        self.assertEqual((code, saw_batch), (1, True))
        self.assertEqual(progress.done, 103)
        shown = self.stream.getvalue()
        self.assertIn("1,000 targets from 50 acts", shown)
        self.assertIn("urlopen error timed out", shown)
        self.assertNotIn("Saved 100 targets", shown)
        self.assertNotIn("\n[2/100]", shown)
        self.assertIn("urlopen error", tail[-1])



FAKE_BATCH_RUNNER = r"""
import json, sys
from pathlib import Path
out = Path(sys.argv[sys.argv.index("--output-dir") + 1])
manifest = json.loads(Path(sys.argv[sys.argv.index("--manifest") + 1]).read_text())
total = manifest["targets"]
audit = out / "audit"; audit.mkdir(parents=True, exist_ok=True)
state = audit / "run_summary.json"
done = json.loads(state.read_text())["targets_exported"] if state.exists() else 0
failed = manifest.get("failed", 0)
outage = out.parent.parent / "outage"
error = None
for index in range(done + 1, total - failed + 1):
    print(f"[{index}/{total}] ACT Article {index}", flush=True)
    if outage.exists() and index == int(outage.read_text()):
        outage.unlink()
        error = "ACT Article: <urlopen error [WinError 10060] timed out>"
        break
    done = index
state.write_text(json.dumps({"targets_planned": total, "targets_exported": done, "targets_not_assessable": 0,
                             "targets_failed": failed if not error else 0, "error": error}))
(out.parent.parent / "calls.txt").open("a").write(out.name + "\n")
if error:
    sys.exit("Provision screening stopped: " + error)
sys.exit(1 if failed else 0)
"""


class ForwardTests(unittest.TestCase):
    """The batch loop with real child processes and a stand-in for the batch screening."""

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.out = Path(self.directory.name)
        runner = self.out / "fake_batch.py"
        runner.write_text(FAKE_BATCH_RUNNER)
        self.old = overnight.BATCH_RUNNER
        overnight.BATCH_RUNNER = runner
        self.addCleanup(setattr, overnight, "BATCH_RUNNER", self.old)
        self.clock, self.stream = FakeClock(), io.StringIO()
        self.corpus_calls = []

    def plan(self, batches):
        plan = {"targets": sum(b["targets"] for b in batches),
                "batches": [{"targets": b["targets"], "manifest": b} for b in batches]}
        (self.out / "batch_plan.json").write_text(json.dumps(plan))

    def saved(self, number, done, failed=0, error=None, targets=10):
        audit = self.out / "batches" / f"{number:04d}" / "audit"
        audit.mkdir(parents=True, exist_ok=True)
        (audit / "run_summary.json").write_text(json.dumps({
            "targets_planned": targets, "targets_exported": done, "targets_not_assessable": 0,
            "targets_failed": failed, "error": error}))

    def supervisor(self, checks, corpus_exit=0):
        checks = list(checks)

        def run(command, progress, interrupted):
            if Path(command[1]).name == "run_eurlex_provision_corpus.py":
                self.corpus_calls.append(command)
                (self.out / "run_summary.json").write_text(json.dumps({
                    "targets_exported": 99, "targets_not_assessable": 0, "targets_failed": corpus_exit,
                    "targets_pending": 0, "stop_reason": None, "counts": {"DIRECT": 99}}))
                return corpus_exit, True, []
            return overnight.run_child(command, progress, interrupted)

        options = {"enriched_dir": self.out, "anchors": self.out / "a.yaml", "document_run": None,
                   "thinking": "off", "confirm_thinking": "off", "workers": 4}
        return overnight.Supervisor(["--workers", "4"], self.out, self.out / ".env", run=run,
                                    check=lambda env: checks.pop(0), sleep=self.clock.sleep, now=self.clock.now,
                                    stream=self.stream, options=options)

    def calls(self):
        path = self.out / "calls.txt"
        return path.read_text().split() if path.exists() else []

    def log(self):
        return (self.out / "overnight_log.txt").read_text()

    def test_restart_goes_forward_and_leaves_failed_targets_for_the_end(self):
        self.plan([{"targets": 10}, {"targets": 10}, {"targets": 10}, {"targets": 10}])
        self.saved(1, 10)                 # finished
        self.saved(2, 8, failed=2)        # through, two invalid answers
        self.saved(3, 4, error="ACT: <urlopen error timed out>")   # stopped by the connection
        sup = self.supervisor(checks=[])
        self.assertEqual(sup.supervise(), 0)
        self.assertEqual(self.calls(), ["0003", "0004"])   # batch 2 is not run again before the final pass
        self.assertEqual(len(self.corpus_calls), 1)
        log = self.log()
        self.assertIn("24 of 40 targets have a result. 2 of 4 batches to go, starting at batch 3.", log)
        self.assertIn("All batches are through. Final pass", log)

    def test_outage_in_a_batch_waits_and_continues_the_same_batch(self):
        self.plan([{"targets": 10}, {"targets": 10}])
        (self.out / "outage").write_text("6")
        sup = self.supervisor(checks=[(False, "No model list found."), (False, "down"), (True, "")])
        self.assertEqual(sup.supervise(), 0)
        self.assertEqual(self.calls(), ["0001", "0001", "0002"])
        self.assertEqual(sum(self.clock.slept) / 60, 15 + 30)
        self.assertIn("Run stopped: Provision screening stopped: ACT Article: <urlopen error", self.log())
        self.assertIn("Qwen answers again. Resuming.", self.log())
        self.assertIn("20/20", self.stream.getvalue())

    def test_failed_targets_in_a_new_batch_do_not_stop_the_run(self):
        self.plan([{"targets": 10, "failed": 3}, {"targets": 10}])
        sup = self.supervisor(checks=[], corpus_exit=1)
        self.assertEqual(sup.supervise(), 1)
        self.assertEqual(self.calls(), ["0001", "0002"])
        self.assertIn("still have no valid answer", self.log())

    def test_everything_through_goes_straight_to_the_final_pass(self):
        self.plan([{"targets": 10}])
        self.saved(1, 10)
        self.assertEqual(self.supervisor(checks=[]).supervise(), 0)
        self.assertEqual(self.calls(), [])
        self.assertEqual(len(self.corpus_calls), 1)

    def test_running_file_stops_before_any_batch(self):
        self.plan([{"targets": 10}])
        (self.out / ".running").write_text("x")
        self.assertEqual(self.supervisor(checks=[]).supervise(), 1)
        self.assertEqual(self.calls(), [])

    def test_batch_command_matches_the_corpus_runner(self):
        import argparse
        from unittest import mock
        import run_eurlex_provision_corpus as corpus
        folder = self.out / "batches" / "0001"
        folder.mkdir(parents=True)
        sup = self.supervisor(checks=[])
        overnight.BATCH_RUNNER = self.old
        for document_run in (None, self.out / "doc"):
            sup.options["document_run"] = document_run
            args = argparse.Namespace(enriched_dir=self.out, anchors=self.out / "a.yaml", env_file=self.out / ".env",
                                      document_run=document_run, thinking="off", confirm_thinking="off", workers=4)
            with mock.patch.object(corpus.subprocess, "run") as run:
                run.return_value.returncode = 0
                corpus.run_batch(args, folder, {"X": "1"})
            self.assertEqual(run.call_args.args[0], sup.batch_command(folder))


if __name__ == "__main__":
    unittest.main()
