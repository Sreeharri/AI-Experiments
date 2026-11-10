"""Verify the expected structures and relationships for the three pilot acts."""

import argparse
from pathlib import Path
import sys

from eurlex_enrichment.enrichment import evidence_for
from eurlex_enrichment.inputs import read_json


def require(condition, message):
    if not condition:
        raise ValueError(message)


def links(act, path, kind=None):
    ids = {key for key, value in act["elements"].items() if value["path"].endswith(path)}
    return [item for item in act["explicit_references"]
            if item["source_id"] in ids and (kind is None or item["kind"] == kind)]


def verify(output):
    expected = {
        "32024R3110": (96, 11, 4, 115),
        "32015L2193": (19, 3, 5, 35),
        "32013D0163": (2, 1, 84, 11),
    }
    fields = ("articles", "annexes", "xhtml_data_tables", "recitals")
    acts = {}
    for celex, counts in expected.items():
        data = read_json(output / f"{celex}_enriched.json")
        actual = tuple(data["summary"][key] for key in fields)
        require(actual == counts, f"{celex}: expected {counts}, got {actual}")
        require(all(table["rows"] for table in data["tables"].values()), f"{celex}: empty table")
        require({item["id"] for item in data["units"] if item["kind"] == "TABLE"}
                == set(data["tables"]), f"{celex}: table coverage mismatch")
        for ref in data["explicit_references"]:
            if ref["status"] == "RESOLVED":
                require(ref["id"] in data["incoming_reference_index"].get(ref["target_id"], []),
                        f"{celex}: reverse lookup missing {ref['id']}")
        acts[celex] = data
        print(f"PASS {celex}: {actual[0]} articles, {actual[1]} annexes, "
              f"{actual[2]} tables, {actual[3]} recitals")

    regulation = acts["32024R3110"]
    require(any(ref["status"] == "RESOLVED" and ref["target_locator"] == "Annex V"
                for ref in links(regulation, "article/15/paragraph/1", "CITES_ANNEX")),
            "Regulation Article 15(1) must resolve to Annex V")
    annex_viii_tables = evidence_for(regulation, "Annex VIII")["tables"]
    require(len(annex_viii_tables) == 1 and annex_viii_tables[0]["database_table_id"] is None,
            "Annex VIII must have its recovered table with XHTML provenance")
    require(all(table["database_table_id"] for table in regulation["tables"].values()
                if table["id"] != annex_viii_tables[0]["id"]),
            "Expected the other regulation tables to match their database records")
    refs = links(regulation, "article/2/paragraph/2/point/b", "CITES_ARTICLE")
    require(any(ref["target_instrument"] == "32020L2184" and ref["target_locator"] == "Article 11(8)"
                and ref["target_id"] is None for ref in refs),
            "Regulation Article 2(2)(b) must point to the external directive")
    require(not any(ref["target_instrument"] == "32024R3110" and ref["status"] == "RESOLVED"
                    for ref in refs), "External Article 11 must not link to the regulation")

    directive = acts["32015L2193"]
    refs = links(directive, "article/6/paragraph/2", "CITES_TABLE")
    def branch(date):
        return {ref["target_locator"] for ref in refs
                if ref["status"] == "RESOLVED" and date in ref["source_excerpt"]}
    require(branch("1 January 2025") == {"Annex II, Part 1, Table 2", "Annex II, Part 1, Table 3"},
            "Directive Article 6(2): wrong tables for the 2025 branch")
    require(branch("1 January 2030") == {"Annex II, Part 1, Table 1", "Annex II, Part 1, Table 3"},
            "Directive Article 6(2): wrong tables for the 2030 branch")
    part2 = {t["published_number"] for t in directive["tables"].values() if t["part"] == "2"}
    require(part2 == {"1", "2"}, "Part 2 must use published Table 1 and Table 2")
    require(any(x["kind"] == "EXPLICIT_CITATION" and directive["elements"][x["recital_id"]]["number"] == "33"
                for x in evidence_for(directive, "Article 5")["unit"]["recital_context"]),
            "Explicit Recital 33 context missing from Article 5")
    table2 = evidence_for(directive, "Annex II, Part 1, Table 2")
    require(table2["footnotes"] and not table2["missing_footnotes"],
            "Part 1, Table 2 must resolve its footnotes")
    require(any(ref["id"] in table2["unit"]["governing_article_reference_ids"]
                for ref in refs if ref["target_locator"] == "Annex II, Part 1, Table 2"),
            "Table 2 must link back to Article 6(2)")

    decision = acts["32013D0163"]
    article1 = evidence_for(decision, "Article 1")
    require(any(ref["kind"] == "CITES_ANNEX" and ref["status"] == "RESOLVED"
                for ref in article1["explicit_references"]), "Decision Article 1 must link to its annex")
    require({unit["label"] for unit in decision["units"] if unit["kind"] == "BAT"}
            == {f"BAT {number}" for number in range(1, 70)}, "Expected BAT 1 through BAT 69")
    bat17 = evidence_for(decision, "BAT 17")
    require(len(bat17["tables"]) == 1 and
            any("dry flue-gas cleaning" in block.get("text", "")
                for section in bat17["annex_sections"] for block in section["blocks"]),
            "BAT 17 must contain its technique table and source requirement text")
    require(decision["summary"]["formula_images"] == 2, "Expected two flagged formula images")
    print("PASS all pilot reference, table, recital, BAT and evidence checks")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    args = parser.parse_args()
    try:
        verify(args.output_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
