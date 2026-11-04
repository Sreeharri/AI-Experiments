"""Export a read-only PostgreSQL sample for inspecting stored EUR-Lex acts."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

SQL_PATH = Path(__file__).with_name("eurlex_structured_10.sql")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "inspection" / "eurlex_structured_10.json"


def write_json(output: Path, acts: list[dict]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=f"{output.name}.", suffix=".part", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(acts, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


REQUESTED_IDS = ['32024R3110', '32023R1230', '32024R1689', '32006R1907', '32008R1272', '32010L0075', '32008L0098', '32019R0943', '32023R1542', '32023R2053']

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, help="Optional .env containing DATABASE_URL")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    from export_eurlex import connect, database_url, psycopg

    try:
        query = SQL_PATH.read_text(encoding="utf-8")
        url, _ = database_url(args.env)
        with connect(url) as conn, conn.cursor() as cursor:
            cursor.execute(query)
            acts = [act for _, act in cursor.fetchall()]
        if not acts:
            parser.exit(1, "No EUR-Lex acts with stored English or German article text found.\n")
        write_json(args.out, acts)
    except psycopg.Error as exc:
        parser.exit(1, f"PostgreSQL query failed: {exc}\n")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")

    found = {act["celex"] for act in acts}
    missing = sorted(set(REQUESTED_IDS) - found)
    if missing:
        print("Missing from export (absent from DB or no eligible EN/DE article text): "
              + ", ".join(missing))
    for act in acts:
        types = Counter(element["element_type"] for element in act["elements"])
        recital_count = (act["recital_set"] or {}).get("recital_count", 0)
        articles = [e for e in act["elements"] if e["element_type"] == "ARTICLE"]
        numbers = Counter(e.get("number") for e in articles if e.get("number"))
        repeated = {number: count for number, count in numbers.items() if count > 1}
        missing_structure = [
            name for name in ("CHAPTER", "ARTICLE", "ANNEX") if not types[name]
        ]
        if not recital_count:
            missing_structure.append("RECITAL_SET")
        print(
            f"{act['celex']} [{act['version']['language']}] "
            f"chapters={types['CHAPTER']} articles={types['ARTICLE']} "
            f"recitals={recital_count} annexes={types['ANNEX']} "
            f"unique_article_numbers={len(numbers)}"
        )
        if repeated:
            print("  Repeated article numbers (inspect before deduplicating): "
                  + ", ".join(f"{n} ({count} rows)" for n, count in repeated.items()))
        if missing_structure:
            print("  Missing stored structure: " + ", ".join(missing_structure))
    print(f"Exported {len(acts)} of 10 requested acts to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
