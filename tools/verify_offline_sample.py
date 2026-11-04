"""Exercise the real HTTP client/runner against a local simulated Qwen server.

No company endpoint is contacted and no relevance labels are evaluated.
"""
import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lr_ai.qwen_client import QwenClient



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    calls, guard = [], threading.Lock()
    review_first = {'32008L0098', '32023R1230', '32024R1689', '32023R2053'}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, data):
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.send_json({'data': [{'id': 'local-simulation', 'max_model_len': 40960}]})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/tokenize':
                # Test estimate only. Production uses the company server's tokenizer.
                self.send_json({'count': len(json.dumps(payload['messages'], ensure_ascii=False)) // 4})
                return
            evidence = json.loads(payload['messages'][1]['content'])
            celex = evidence['celex']
            with guard:
                first = not any(c['celex'] == celex for c in calls)
                calls.append({'celex': celex, 'output_token_limit': payload['max_tokens'],
                              'excerpts': len(evidence['excerpts'])})
            level = 'NEEDS_REVIEW' if (first and celex in review_first) or celex == '32023R2053' else 'DIRECT'
            content = json.dumps({'reason': 'SCRIPTED LOCAL TEST RESPONSE, not a Qwen assessment.', 'anchor_ids': [], 'level': level})
            self.send_json({'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content},
                                         'finish_reason': 'stop'}]})


    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = QwenClient(f'http://127.0.0.1:{server.server_port}', model='local-simulation', retries=0)
    spec = importlib.util.spec_from_file_location('document_relevance', ROOT/'tools/document_relevance.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    argv = ['document_relevance.py', '--input', str(args.input.resolve()), '--out', str(args.out.resolve()),
            '--as-of', '2026-11-04', '--workers', '4', '--context-limit', '40960']
    try:
        with patch.object(runner.QwenClient, 'from_env', return_value=client), patch.object(sys, 'argv', argv):
            result = runner.main()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    manifest = json.loads((args.out/'manifest.json').read_text())
    expected = manifest['selected_celex']
    counts = Counter(c['celex'] for c in calls)
    report = {'test_type': 'local_simulated_http_endpoint', 'live_company_endpoint_used': False,
              'relevance_accuracy_measured': False, 'acts_expected': len(expected),
              'acts_reached_http_endpoint': len(counts), 'chat_requests': len(calls),
              'all_acts_reached_endpoint': set(expected) == set(counts),
              'requests_per_act': dict(counts),
              'only_model_review_triggered_second_call': all(counts[c] == (2 if c in review_first else 1) for c in expected),
              'labels_are_scripted_test_responses': True,
              'runner_exit_code': result}
    (args.out/'offline_verification.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if result == 0 and report['all_acts_reached_endpoint'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
