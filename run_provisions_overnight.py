"""Run the provision corpus screening unattended, with a progress bar.

Use this command instead of run_eurlex_provision_corpus.py, with the same
options. It does not change the screening or the saved results.

What it does:

1. It continues where the run stopped. Finished batches are skipped at once.
   Targets that failed (Qwen gave an invalid answer twice) are NOT tried again
   on a restart; the run goes forward first.
2. When a request to Qwen fails, it checks whether Qwen answers. If yes, it
   resumes after 15 seconds. If not, it waits 15 minutes and checks again,
   then 30, then 45 minutes, and after that every 60 minutes until Qwen is
   back. The command stays running the whole time.
3. When every batch is through, it tries the failed targets once more and
   writes the final export.
4. It shows one progress line (share done, speed, expected finish) and writes
   every stop, wait and resume to overnight_log.txt in the output folder.

Stop with Ctrl+C at any time and run the same command again to resume.

    & .\\.venv\\Scripts\\python.exe .\\run_provisions_overnight.py `
        --manifest .\\runs\\eurlex_full_v10\\provision_manifest.json --enriched-dir .\\output_db_corpus `
        --no-document-hint --workers 4 --output-dir .\\runs\\eurlex_provisions_baseline_v3_2
"""

import argparse
from collections import deque
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "run_eurlex_provision_corpus.py"
BATCH_RUNNER = ROOT / "screen_eurlex_provisions_simple.py"
DEFAULT_OUTPUT = ROOT / "runs/eurlex_provisions_full_v3_2"
DEFAULT_ENV = ROOT / ".env"
WAIT_MINUTES = (15, 30, 45, 60)       # the last value repeats until Qwen answers again
QUICK_RESTARTS = 2                     # restarts without progress while Qwen answers
TARGET_LINE = re.compile(r"^\[(\d+)/(\d+)\]\s+(.*)$")
# Routine lines of each batch that the progress line already covers.
QUIET_LINE = re.compile(r"^Saved \d+ targets to |^\d+ targets? (is|are) not assessable\. See ")
BATCH_LINE = re.compile(r"^Batch (\d+)/(\d+)")
# Error text that points to the endpoint or the network, not to the screening code.
CONNECTION_ERROR = re.compile(
    r"urlopen error|WinError 100\d\d|timed out|timeout|connection|Verbindung|getaddrinfo|"
    r"Remote end closed|HTTP Error 5\d\d|Bad Gateway|Service Unavailable|Gateway Time|"
    r"server_error|rate_limited|Too Many Requests|No model list found", re.IGNORECASE)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def handled(summary):
    """Targets with a saved result: classified, not assessable or failed."""
    summary = summary or {}
    return sum(summary.get(key) or 0 for key in ("targets_exported", "targets_not_assessable", "targets_failed"))


def duration(seconds):
    minutes = int(seconds // 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


class Progress:
    """One status line at the bottom of the terminal; other output scrolls above it."""

    def __init__(self, out_dir, stream=None):
        self.out_dir = out_dir
        self.stream = stream or sys.stdout
        self.sizes = []
        self.batch = 0
        self.batches = 0
        self.in_batch = 0
        self.label = ""
        self.done = handled(read_json(out_dir / "run_summary.json"))
        self.base = None           # targets done outside the current batch, when known exactly
        self.active = 0.0          # seconds in which the screening was running
        self.samples = deque(maxlen=400)
        self.shown = False

    @property
    def total(self):
        return sum(self.sizes)

    def load_plan(self):
        plan = read_json(self.out_dir / "batch_plan.json") or {}
        self.sizes = [batch["targets"] for batch in plan.get("batches", [])]
        self.batches = len(self.sizes) or self.batches

    def start_batch(self, number, batches, base=None):
        if not self.sizes:
            self.load_plan()
        self.batch, self.batches, self.in_batch, self.base = number, batches, 0, base
        self.done = base if base is not None else max(self.done, sum(self.sizes[:number - 1]))

    def target(self, index, label, now):
        self.in_batch = max(self.in_batch, index)
        self.label = label
        if self.base is not None:
            before = self.base
        else:
            before = sum(self.sizes[:self.batch - 1]) if self.sizes and self.batch else self.done
        self.done = max(self.done, before + self.in_batch)
        self.samples.append((now, self.done))

    def rate(self):
        """Targets per hour over the recent running time."""
        if len(self.samples) < 2:
            return None
        (t0, d0), (t1, d1) = self.samples[0], self.samples[-1]
        return (d1 - d0) / (t1 - t0) * 3600 if t1 - t0 >= 60 and d1 > d0 else None

    def line(self, status=None):
        width = max(60, shutil.get_terminal_size((120, 20)).columns - 1)
        total = self.total
        share = self.done / total if total else 0
        size = max(10, min(40, width - 85))
        filled = int(share * size)
        parts = [f"[{'#' * filled}{'-' * (size - filled)}] {share:6.1%}",
                 f"{self.done:,}/{total:,}" if total else f"{self.done:,} done"]
        if self.batch:
            parts.append(f"batch {self.batch}/{self.batches}")
        rate = self.rate()
        if status:
            parts.append(status)
        elif rate:
            left = (total - self.done) / rate * 3600 if total else 0
            finish = datetime.now() + timedelta(seconds=left)
            parts += [f"{rate:,.0f}/h", f"ETA {duration(left)} (~{finish:%a %H:%M})"]
        else:
            parts.append("measuring speed")
        if self.label and not status:
            parts.append(self.label)
        return " | ".join(parts)[:width].ljust(width)

    def draw(self, status=None):
        self.stream.write("\r" + self.line(status))
        self.stream.flush()
        self.shown = True

    def say(self, text):
        """Print a normal line above the progress line."""
        if self.shown:
            width = max(60, shutil.get_terminal_size((120, 20)).columns - 1)
            self.stream.write("\r" + " " * width + "\r")
        self.stream.write(text + "\n")
        self.stream.flush()
        self.shown = False


def run_child(command, progress, interrupted):
    """Run the corpus runner. Returns (exit code, saw a batch, last output lines)."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    tail, saw_batch, started = deque(maxlen=30), False, time.monotonic()
    try:
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            tail.append(line)
            if match := TARGET_LINE.match(line):
                progress.target(int(match[1]), match[3], time.monotonic())
                progress.draw()
                continue
            if match := BATCH_LINE.match(line):
                saw_batch = True
                progress.start_batch(int(match[1]), int(match[2]))
                progress.draw()
                continue
            if not QUIET_LINE.match(line):
                progress.say(line)
            progress.draw()
    except KeyboardInterrupt:
        # The runner got the same Ctrl+C. Let it save and stop, and show its last messages.
        interrupted.append(True)
        try:
            for raw in process.stdout:
                progress.say(raw.decode("utf-8", "replace").rstrip())
        except KeyboardInterrupt:
            pass
    code = process.wait()
    process.stdout.close()
    progress.active += time.monotonic() - started
    return code, saw_batch, list(tail)


def reachable(env_file):
    """True when the Qwen endpoint lists its model. Returns (ok, reason)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from lr_ai.qwen_client import QwenClient

        result = QwenClient.from_env(env_file).discover()
        return bool(result.get("ok")), result.get("error") or ""
    except Exception as exc:  # noqa: BLE001 - every failure means "not reachable"
        return False, str(exc)


def batch_state(directory, targets):
    """Return (targets with a saved outcome, batch is through) for one batch folder."""
    summary = read_json(directory / "audit" / "run_summary.json")
    if not summary or summary.get("targets_planned") != targets:
        return 0, False
    count = handled(summary)
    # Failed targets (invalid answers) count as through here; they get one more try in the final pass.
    return count, count == targets and not summary.get("error")


class Supervisor:
    def __init__(self, forward, out_dir, env_file, waits=WAIT_MINUTES, run=run_child, check=reachable,
                 sleep=time.sleep, now=datetime.now, stream=None, options=None):
        self.command = [sys.executable, str(RUNNER), *forward]
        self.out_dir, self.env_file, self.waits = out_dir, env_file, tuple(waits)
        self.run, self.check, self.sleep, self.now = run, check, sleep, now
        self.options = options     # settings for single batches; None: always use the corpus runner
        self.progress = Progress(out_dir, stream)
        self.log_file = out_dir / "overnight_log.txt"
        self.step = self.quick = 0

    def log(self, text, show=True):
        stamp = f"{self.now():%Y-%m-%d %H:%M}"
        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp}  {text}\n")
        except OSError:
            pass
        if show:
            self.progress.say(f"{stamp}  {text}")

    def wait(self, minutes, reason):
        """Wait with a countdown on the progress line. Returns False on Ctrl+C."""
        until = self.now() + timedelta(minutes=minutes)
        self.log(f"Qwen does not answer ({reason[:160]}). Next check at {until:%H:%M} ({minutes:g} min).")
        try:
            while (left := (until - self.now()).total_seconds()) > 0:
                self.progress.draw(f"waiting for Qwen, next check {until:%H:%M} (in {duration(left)})")
                self.sleep(min(30, left))
        except KeyboardInterrupt:
            self.log("Stopped by Ctrl+C while waiting.")
            return False
        return True

    def handle_stop(self, progressed, tail, code):
        """Decide what to do after a stop. Returns None to try again, or an exit code."""
        text = "\n".join(tail)
        last_error = next((line for line in reversed(tail) if CONNECTION_ERROR.search(line)),
                          tail[-1] if tail else "")
        if progressed:
            self.step, self.quick = 0, 0
        ok, why = self.check(self.env_file)
        if ok and (progressed or self.quick < QUICK_RESTARTS):
            self.quick += 0 if progressed else 1
            self.log(f"Run stopped ({last_error[:160]}). Qwen answers, resuming in "
                     f"{'15 s' if progressed else '1 min'}.")
            try:
                self.sleep(15 if progressed else 60)
            except KeyboardInterrupt:
                self.log("Stopped by Ctrl+C.")
                return 130
            return None
        if ok and not CONNECTION_ERROR.search(text):
            self.log("The run keeps stopping although Qwen answers, and the error does not look like a "
                     f"connection problem: {last_error[:300]}. Stopped; run the same command after fixing it.")
            return code or 1
        # Connection lost: 15, 30, 45 minutes, then every 60 minutes until Qwen answers.
        self.log(f"Run stopped: {last_error[:300]}")
        while True:
            minutes = self.waits[min(self.step, len(self.waits) - 1)]
            self.step += 1
            if not self.wait(minutes, why or last_error):
                return 130
            ok, why = self.check(self.env_file)
            if ok:
                self.log("Qwen answers again. Resuming.")
                self.quick = 0
                return None

    def batch_command(self, directory):
        o = self.options
        command = [sys.executable, str(BATCH_RUNNER), "pilot", "--manifest", str(directory / "manifest.json"),
                   "--enriched-dir", str(o["enriched_dir"]), "--anchors", str(o["anchors"]),
                   "--env-file", str(self.env_file), "--output-dir", str(directory)]
        if o.get("document_run"):
            command += ["--document-run", str(o["document_run"])]
        return command + ["--thinking", o["thinking"], "--confirm-thinking", o["confirm_thinking"],
                          "--workers", str(o["workers"])]

    def forward(self, plan):
        """Run every batch that is not through yet, in order. Failed targets are left for the final pass."""
        lock = self.out_dir / ".running"
        try:
            lock.touch(exist_ok=False)
        except FileExistsError:
            self.log("The file .running in the output folder says another run is active. Stop that run first. "
                     "If none is running, delete the file and start again.")
            return 1
        try:
            return self.run_open_batches(plan)
        finally:
            lock.unlink(missing_ok=True)

    def run_open_batches(self, plan):
        batches = plan["batches"]
        folders = [self.out_dir / "batches" / f"{number:04d}" for number in range(1, len(batches) + 1)]
        states = [batch_state(folder, batch["targets"]) for folder, batch in zip(folders, batches)]
        open_batches = [n for n, (_, through) in enumerate(states, 1) if not through]
        total_done = sum(count for count, _ in states)
        self.progress.load_plan()
        self.progress.done = total_done
        self.log(f"{total_done:,} of {plan['targets']:,} targets have a result. "
                 f"{len(open_batches)} of {len(batches)} batches to go"
                 + (f", starting at batch {open_batches[0]}." if open_batches else "."))
        for number in open_batches:
            folder, targets = folders[number - 1], batches[number - 1]["targets"]
            while True:
                count, through = states[number - 1]
                if through:
                    break
                folder.mkdir(parents=True, exist_ok=True)
                if read_json(folder / "manifest.json") != batches[number - 1]["manifest"]:
                    (folder / "manifest.json").write_text(
                        json.dumps(batches[number - 1]["manifest"], ensure_ascii=False, indent=2),
                        encoding="utf-8")
                base = sum(c for i, (c, _) in enumerate(states, 1) if i != number)
                self.progress.start_batch(number, len(batches), base)
                self.progress.samples.clear()
                interrupted = []
                code, _, tail = self.run(self.batch_command(folder), self.progress, interrupted)
                states[number - 1] = batch_state(folder, targets)
                self.progress.done = sum(c for c, _ in states)
                if interrupted:
                    self.log("Stopped by Ctrl+C. Run the same command to resume.")
                    return 130
                if states[number - 1][1]:
                    self.step, self.quick = 0, 0
                    break
                outcome = self.handle_stop(states[number - 1][0] > count, tail, code)
                if outcome is not None:
                    return outcome
        return "done"

    def corpus_pass(self, final):
        """Run the corpus runner once. Returns "again", or an exit code."""
        before = handled(read_json(self.out_dir / "run_summary.json"))
        self.progress.base = None
        self.progress.samples.clear()   # measure speed only while the screening runs
        interrupted = []
        code, saw_batch, tail = self.run(self.command, self.progress, interrupted)
        summary = read_json(self.out_dir / "run_summary.json") or {}
        after = handled(summary)
        if interrupted:
            self.log("Stopped by Ctrl+C. Run the same command to resume.")
            return 130
        if code == 0:
            counts = summary.get("counts", {})
            self.log(f"Finished: {after:,} targets. " + ", ".join(f"{k} {v:,}" for k, v in counts.items()))
            return 0
        if summary and summary.get("targets_pending") == 0 and not summary.get("stop_reason"):
            # Every batch was screened; only targets with invalid replies are left.
            if final:
                self.log(f"Finished. {summary.get('targets_failed', 0):,} targets still have no valid answer "
                         "after a second try; they are listed under failures in run_summary.json. "
                         "All other results are exported.")
                return 1
            return "retry failed"
        if not saw_batch:
            # The runner stopped before screening (settings, lock file, missing files). Waiting cannot help.
            self.log("The runner stopped before screening. Its last messages are above. Fix the cause "
                     "and run the same command again. If it names a .running file and no run is "
                     "active, delete that file.")
            return code or 1
        outcome = self.handle_stop(after > before, tail, code)
        return "again" if outcome is None else outcome

    def supervise(self):
        self.log("Overnight run started: " + " ".join(self.command[2:]))
        final = False
        while True:
            plan = read_json(self.out_dir / "batch_plan.json") if self.options else None
            if plan and not final:
                outcome = self.forward(plan)
                if outcome != "done":
                    return outcome
                final = True
                self.log("All batches are through. Final pass: one more try for failed targets, then the export.")
            # Without a plan (new output folder) the corpus runner checks the inputs and writes the plan.
            outcome = self.corpus_pass(final)
            if outcome == "retry failed":
                final = True
                self.log("Retrying the targets that failed once.")
            elif outcome != "again":
                return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=ROOT / "runs/eurlex_full_v10/provision_manifest.json")
    parser.add_argument("--enriched-dir", type=Path, default=ROOT / "output_db_corpus")
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--document-run", type=Path)
    parser.add_argument("--no-document-hint", action="store_true")
    parser.add_argument("--thinking", choices=("on", "off"), default="off")
    parser.add_argument("--confirm-thinking", choices=("on", "off"), default="off")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--wait-minutes", default=",".join(map(str, WAIT_MINUTES)),
                        help="Waits between connection checks; the last one repeats. Default: 15,30,45,60")
    args = parser.parse_args()
    try:
        waits = tuple(float(value) for value in args.wait_minutes.split(",") if value.strip())
    except ValueError:
        waits = ()
    if not waits or min(waits) <= 0:
        parser.error("--wait-minutes needs positive numbers, for example 15,30,45,60")
    # The same rule as in run_eurlex_provision_corpus.py.
    if args.document_run is None and not args.no_document_hint:
        export = args.manifest.parent / "relevance_export"
        if (export / "direct.json").is_file() and (export / "indirect.json").is_file():
            args.document_run = args.manifest.parent
    out_dir, env_file = args.output_dir.resolve(), args.env_file.resolve()
    forward = ["--manifest", str(args.manifest.resolve()), "--enriched-dir", str(args.enriched_dir.resolve()),
               "--anchors", str(args.anchors.resolve()), "--env-file", str(env_file),
               "--thinking", args.thinking, "--confirm-thinking", args.confirm_thinking,
               "--workers", str(args.workers), "--output-dir", str(out_dir)]
    if args.document_run:
        forward += ["--document-run", str(args.document_run.resolve())]
    if args.no_document_hint:
        forward.append("--no-document-hint")
    options = {"enriched_dir": args.enriched_dir.resolve(), "anchors": args.anchors.resolve(),
               "document_run": args.document_run.resolve() if args.document_run else None,
               "thinking": args.thinking, "confirm_thinking": args.confirm_thinking, "workers": args.workers}
    supervisor = Supervisor(forward, out_dir, env_file, waits, options=options)
    try:
        return supervisor.supervise()
    except KeyboardInterrupt:
        supervisor.log("Stopped by Ctrl+C. Run the same command to resume.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
