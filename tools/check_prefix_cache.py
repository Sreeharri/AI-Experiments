"""Check whether the Qwen endpoint reuses the shared start of our requests.

Sends four tiny requests that all carry the full system prompt (instructions
and business catalog) and compares how long the server needs:

  1. cold    a system prompt the server has never seen (a random line on top)
  2. first   the real system prompt
  3. same    the same request again
  4. other   the real system prompt with a different target

If prefix caching works, requests 3 and 4 are clearly faster than request 1.
Run from the AI-Experiments folder:

    .\\.venv\\Scripts\\python.exe .\\tools\\check_prefix_cache.py
"""

import argparse
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from eurlex_enrichment.inputs import read_json  # noqa: E402
from eurlex_provision.profile import company_profile, system_prompt  # noqa: E402
from eurlex_provision.simple import SCORE_TASK, cached_tokens  # noqa: E402
from lr_ai.qwen_client import QwenClient  # noqa: E402


def user_message(text):
    sent = {"act": {"celex": "CACHE-CHECK", "title": "Prefix cache check"},
            "target": {"locator": "Article 1", "text": f"[P1] {text}"}}
    return f"{json.dumps(sent, ensure_ascii=False)}\n\n{SCORE_TASK}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--anchors", type=Path, default=ROOT / "config/business_anchors_en.yaml")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()

    facts_file = ROOT / "config/eurlex_provision_anchor_facts.json"
    profile = company_profile(yaml.safe_load(args.anchors.read_text(encoding="utf-8-sig")),
                              read_json(facts_file) if facts_file.is_file() else None)
    system = system_prompt(
        (ROOT / "prompts/eurlex_provision_simple.md").read_text(encoding="utf-8"),
        (ROOT / "prompts/eurlex_provision_ignore_review.md").read_text(encoding="utf-8"), profile)
    client = QwenClient.from_env(args.env_file, retries=0)
    discovered = client.discover()
    if not discovered["ok"]:
        raise SystemExit(f"Endpoint not reachable: {discovered['error']}")

    first = user_message("Operators of asphalt plants shall keep a record of fuel use.")
    other = user_message("Member States shall inform the Commission of the measures taken.")
    plan = [("cold", f"Check {uuid.uuid4().hex}\n{system}", first), ("first", system, first),
            ("same", system, first), ("other", system, other)]
    rows = []
    for name, system_text, user_text in plan:
        reply = client.chat([{"role": "system", "content": system_text}, {"role": "user", "content": user_text}],
                            thinking=False, stream=False, seed=42, sampling={"temperature": 0}, max_tokens=1)
        if not reply.ok:
            raise SystemExit(f"Request '{name}' failed ({reply.error_kind}): {client.clean(reply.error or '')}")
        rows.append((name, reply.seconds, (reply.usage or {}).get("prompt_tokens"), cached_tokens(reply.usage)))
        print(f"{name:6} {reply.seconds:7.2f} s   prompt tokens: {rows[-1][2]}   cached tokens: "
              f"{'not reported' if rows[-1][3] is None else rows[-1][3]}", flush=True)

    cold, same, other_time = rows[0][1], rows[2][1], rows[3][1]
    print(f"\nSystem prompt: {len(system):,} characters.")
    if any(row[3] for row in rows[1:]):
        print("Result: the server reports cached tokens. Prefix caching is working.")
    elif cold > 0 and max(same, other_time) < 0.6 * cold:
        print(f"Result: repeated requests took {same:.2f} s and {other_time:.2f} s against {cold:.2f} s cold. "
              "Prefix caching appears to be working.")
    else:
        print("Result: no clear speed-up. Ask IT whether prefix caching is switched off "
              "(--no-enable-prefix-caching) or whether several model copies run behind the endpoint.")
    if all(row[3] is None for row in rows):
        print("The server does not report cached tokens. IT can switch that on with "
              "--enable-prompt-tokens-details, or read the prefix cache counters on the server's /metrics page.")


if __name__ == "__main__":
    main()
