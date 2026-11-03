"""Generate resumable English recital briefs for one selected EUR-Lex set."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile, ZipFile

from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lr_ai import qwen_client, relevance_model

SETS = {"dev": "set_1_development", "holdout": "set_2_holdout"}


def load_acts(path, split):
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive:
            names = [
                name for name in archive.namelist()
                if name.rsplit("/", 1)[-1] == f"{SETS[split]}.json"
            ]
            if len(names) != 1:
                raise ValueError(f"Expected one {SETS[split]}.json inside the ZIP")
            raw = archive.read(names[0])
    else:
        raw = path.read_bytes()
    acts = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(acts, list) or not acts:
        raise ValueError("Expected a nonempty array of acts")
    seen = set()
    for act in acts:
        celex = act.get("id") if isinstance(act, dict) else None
        if not isinstance(celex, str) or not re.fullmatch(r"[0-9A-Z][0-9A-Z()._-]*", celex):
            raise ValueError("Invalid or missing CELEX ID")
        if celex in seen:
            raise ValueError(f"Duplicate CELEX ID: {celex}")
        seen.add(celex)
        title = act.get("title_en")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Missing English title for {celex}")
        recitals = act.get("recitals")
        if not isinstance(recitals, dict):
            raise ValueError(f"Missing recital object for {celex}")
        for lang in ("en", "de"):
            items = recitals.get(lang)
            if not isinstance(items, list) or any(
                not isinstance(item, dict) or not isinstance(item.get("text"), str)
                or (item.get("n") is not None and type(item["n"]) not in (int, str))
                for item in items
            ):
                raise ValueError(f"Invalid {lang} recital array for {celex}")
    return acts, hashlib.sha256(raw).hexdigest()


def evidence(act):
    for language in ("en", "de"):
        rows = [
            {"ref": f"R{i}", "number": row.get("n"), "text": row["text"]}
            for i, row in enumerate(act["recitals"][language], 1)
            if row["text"].strip()
        ]
        if rows:
            return language, rows
    return None, []


def select_acts(acts, limit):
    if limit is None or limit >= len(acts):
        return acts
    ranked = sorted(
        acts, key=lambda act: (sum(len(r["text"]) for r in evidence(act)[1]), act["id"])
    )
    if limit == 1:
        return [ranked[-1]]
    return [ranked[i * (len(ranked) - 1) // (limit - 1)] for i in range(limit)]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(args):
    acts, source_hash = load_acts(args.input, args.split)
    selected = select_acts(acts, args.limit)
    prepared = [(act, *evidence(act)) for act in selected]
    client = None
    if any(rows for _, _, rows in prepared):
        client = qwen_client.QwenClient.from_env(args.env, retries=2)
        found = client.discover()
        if not found["ok"]:
            raise ValueError(found.get("error") or "Qwen model discovery failed")
        if args.context_limit is not None:
            client.max_model_len = min(int(client.max_model_len or args.context_limit), args.context_limit)
        if not client.max_model_len:
            raise ValueError("Server did not advertise its context size. Supply the confirmed --context-limit.")
        if client.tokenize(messages=[{"role": "user", "content": "Token count check"}]) is None:
            raise ValueError("The configured Qwen server did not provide /tokenize.")
    settings = {
        "input_sha256": source_hash,
        "split": args.split,
        "selected_ids": [act["id"] for act in selected],
        "model": client.model if client else None,
        "context_limit": int(client.max_model_len) if client else None,
        "endpoint_sha256": hashlib.sha256(client.api_base.encode()).hexdigest() if client else None,
        "code_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (Path(__file__), Path(relevance_model.__file__), Path(qwen_client.__file__))
        },
    }
    if args.output.exists():
        checkpoint = json.loads(args.output.read_text(encoding="utf-8"))
        if checkpoint.get("settings") != settings:
            raise ValueError("Input, run selection, code or model settings changed. Use a new output path.")
    else:
        checkpoint = {"settings": settings, "acts": {}}

    records = checkpoint["acts"]
    for index, (act, language, rows) in enumerate(prepared, 1):
        celex = act["id"]
        if records.get(celex, {}).get("status") in {"ok", "no_recitals"}:
            continue
        record = {
            "id": celex, "title_en": act["title_en"], "source_language": language,
            "summary_language": "en", "status": "no_recitals", "points": [],
            "recital_numbers": {row["ref"]: row["number"] for row in rows},
        }
        if rows:
            try:
                record["points"] = relevance_model.summarize(client, act["title_en"], "EN", rows)
                record["status"] = "ok"
            except (ValueError, ValidationError) as exc:
                record["status"] = "failed"
                record["error"] = "Invalid model response" if isinstance(exc, ValidationError) else str(exc)
        records[celex] = record
        save(args.output, checkpoint)
        print(f"[{index}/{len(selected)}] {celex}: {record['status']}", flush=True)
    counts = Counter(record["status"] for record in records.values())
    print(f"{args.output} | ok={counts['ok']} no_recitals={counts['no_recitals']} failed={counts['failed']}")
    return 1 if counts["failed"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="selected_acts.zip or one extracted set JSON")
    parser.add_argument("--split", choices=SETS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--limit", type=int, help="Pilot size, spread across recital lengths")
    parser.add_argument("--context-limit", type=int, help="Confirmed context window or lower cap")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.context_limit is not None and args.context_limit <= 2560:
        parser.error("--context-limit must leave room for the prompt and output")
    if args.output.resolve() == args.input.resolve():
        parser.error("--output must differ from --input")
    try:
        return run(args)
    except (OSError, ValueError, BadZipFile) as exc:
        parser.exit(1, f"Summarization failed: {exc}\n")


if __name__ == "__main__":
    sys.exit(main())
