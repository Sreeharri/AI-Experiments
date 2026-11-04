"""Export a read-only PostgreSQL sample for inspecting stored EUR-Lex acts."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile

SQL_PATH = Path(__file__).with_name("eurlex_sample_10.sql")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "inspection" / "eurlex_sample_10.json"


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

    for act in acts:
        types = Counter(element["element_type"] for element in act["elements"])
        recital_count = (act["recital_set"] or {}).get("recital_count", 0)
        print(
            f"{act['celex']} [{act['version']['language']}] "
            f"chapters={types['CHAPTER']} articles={types['ARTICLE']} "
            f"recitals={recital_count} annexes={types['ANNEX']}"
        )
    print(f"Exported {len(acts)} of 10 requested acts to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
