"""Resume the v3.2 corpus with connection backoff and checkpoint progress.

Uses the original corpus runner without changing any fingerprinted files.
Accepts the same command-line options as run_eurlex_provision_corpus.py.
"""
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time

RETRY_SECONDS = (15 * 60, 30 * 60, 45 * 60, 60 * 60)


def retry_delay(failure_index):
    return RETRY_SECONDS[min(failure_index, len(RETRY_SECONDS) - 1)]


def connection_failure(message):
    text = message.lower()
    # Configuration and input problems need intervention.
    if re.search(r'(?:http(?: error)?|status(?: code)?)\D{0,8}(400|401|403|404|422)\b', text) or any(word in text for word in (
            'fingerprint', 'inputs changed', 'context limit', 'invalid response',
            'certificate_verify_failed', 'permission denied', 'no such file')):
        return False
    return bool(re.search(r'(?:http(?: error)?|status(?: code)?)\D{0,8}(408|429|500|502|503|504)\b', text)) or any(
        word in text for word in ('urlopen error', 'timed out', 'timeout', 'connection reset',
                                 'connection refused', 'connection aborted', 'remote end closed',
                                 'remote disconnected', 'winerror 10060', 'winerror 10054',
                                 'getaddrinfo failed', 'name resolution', 'network is unreachable',
                                 'server disconnected', 'temporarily unavailable'))


def read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def checkpoint_counts(directory):
    classified = unassessable = 0
    for path in (directory / 'audit').glob('*/*.json'):
        record = read_json(path)
        if record.get('failure'):
            continue
        if record.get('level') in ('DIRECT', 'INDIRECT', 'IGNORE', 'UNSURE'):
            classified += 1
        elif record.get('not_assessable'):
            unassessable += 1
    return classified, unassessable


class Progress:
    def __init__(self, root, directory):
        plan = read_json(root / 'batch_plan.json')
        self.total = plan.get('targets', 0)
        self.base_classified = self.base_unassessable = 0
        for path in (root / 'batches').glob('*/audit/run_summary.json'):
            if path.parent.parent == directory:
                continue
            summary = read_json(path)
            self.base_classified += summary.get('targets_exported', 0)
            self.base_unassessable += summary.get('targets_not_assessable', 0)
        self.directory = directory
        self.last = 0

    def show(self, status, force=False):
        if not force and time.monotonic() - self.last < 5:
            return
        self.last = time.monotonic()
        classified, unassessable = checkpoint_counts(self.directory)
        classified += self.base_classified
        unassessable += self.base_unassessable
        done = classified + unassessable
        fraction = min(done / self.total, 1) if self.total else 0
        bar = '#' * int(25 * fraction) + '-' * (25 - int(25 * fraction))
        line = (f'[{bar}] {fraction:6.2%} | {done:,}/{self.total:,} handled '
                f'| {classified:,} classified | {unassessable:,} unassessable '
                f'| batch {self.directory.name} | {status}')
        if sys.stdout.isatty():
            print('\r' + line.ljust(180), end='', flush=True)
        else:
            print(line, flush=True)


def wait_for_retry(seconds, progress):
    deadline = time.monotonic() + seconds
    while True:
        remaining = max(0, int(deadline - time.monotonic() + 0.999))
        progress.show(f'Qwen unavailable; retry in {remaining // 60:02d}:{remaining % 60:02d}', force=True)
        if remaining == 0:
            print()
            return
        time.sleep(min(5, remaining))


def run_attempt(command, cwd, log_path, progress):
    messages = queue.Queue()
    tail = []
    with log_path.open('a', encoding='utf-8') as log:
        log.write('\n--- Attempt ' + time.strftime('%Y-%m-%d %H:%M:%S') + ' ---\n')
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                   errors='replace', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})

        def read_output():
            try:
                for line in process.stdout:
                    messages.put(line)
            finally:
                messages.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            finished_output = False
            latest = 'Connecting / preparing evidence'
            while not finished_output or process.poll() is None:
                try:
                    line = messages.get(timeout=1)
                except queue.Empty:
                    line = ''
                if line is None:
                    finished_output = True
                elif line:
                    log.write(line)
                    log.flush()
                    tail.append(line)
                    tail = tail[-100:]
                    latest = line.strip()[-110:]
                progress.show(latest)
            code = process.wait()
        except KeyboardInterrupt:
            # Windows console Ctrl+C also reaches the child. On Unix explicitly forward it.
            if os.name != 'nt' and process.poll() is None:
                process.send_signal(signal.SIGINT)
            print('\nStopping; waiting for active requests to save their checkpoints...', flush=True)
            process.wait()
            raise
        finally:
            reader.join(timeout=2)
            process.stdout.close()
    progress.show('Batch attempt finished', force=True)
    print()
    return code, ''.join(tail)


def run_batch_with_recovery(runner, args, directory, manifest, prepare_only=False):
    manifest_path = directory / 'manifest.json'
    runner.write_json(manifest_path, manifest)
    command = [sys.executable, '-u', str(runner.ROOT / 'screen_eurlex_provisions_simple.py'),
               'pilot', '--manifest', str(manifest_path), '--enriched-dir', str(args.enriched_dir),
               '--anchors', str(args.anchors), '--env-file', str(args.env_file),
               '--output-dir', str(directory), '--thinking', args.thinking,
               '--confirm-thinking', args.confirm_thinking, '--workers', str(args.workers)]
    if args.document_run:
        command.extend(['--document-run', str(args.document_run)])
    if prepare_only:
        command.append('--prepare-only')
    progress = Progress(args.output_dir, directory)
    failure_index = 0
    while True:
        code, output = run_attempt(command, runner.ROOT, directory / 'overnight.log', progress)
        if code == 0:
            return code
        # Use this attempt's output, avoiding a stale summary from an earlier failure.
        error_lines = [line for line in output.splitlines() if 'screening stopped:' in line.lower()]
        error = error_lines[-1] if error_lines else output
        if prepare_only or not connection_failure(error):
            print(output[-4000:], flush=True)
            return code
        delay = retry_delay(failure_index)
        failure_index += 1
        print(f'Connection interrupted. Retry {failure_index} in {delay // 60} minutes. '
              f'Checkpoints saved; details in {directory / "overnight.log"}', flush=True)
        wait_for_retry(delay, progress)


def main():
    import run_eurlex_provision_corpus as runner
    runner.run_batch = lambda args, directory, manifest, prepare_only=False: run_batch_with_recovery(
        runner, args, directory, manifest, prepare_only)
    try:
        return runner.main()
    except KeyboardInterrupt:
        print('\nStopped by user. Run the same overnight command to resume.', flush=True)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
