import json
from argparse import Namespace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import screen_new_articles as new  # noqa: E402
from eurlex_provision.simple import request_pack  # noqa: E402

CELEX = "32018L2001"
ARTICLE_15A = ("Article 15a Mainstreaming renewable energy in buildings 1. In order to promote the production and "
               "use of renewable energy in the building sector, Member States shall set an indicative national "
               "share of renewable energy in final energy consumption in buildings in 2030.")


def change(operation, depth, day="2023-11-20", by="32023L2413"):
    return {"role": "latest", "date": day, "operation": operation, "depth": depth, "changing_act": by,
            "where": "x", "place": "x", "status": "MATCHED_VERSION_COMPARISON", "complete": True}


def root(name, kind, number, changes, text=None, deleted=None):
    today = {"text": text, "heading": "", "version_date": "2026-01-01", "version_kind": "CONSOLIDATED",
             "link": "https://eur-lex.europa.eu"} if text else None
    return {"kind": kind, "number": number, "name": name, "today": today, "deleted": deleted, "changes": changes}


def write_export(folder):
    roots = {
        "article/15a": root("Article 15a", "article", "15a", [change("ADD", 0)], ARTICLE_15A),
        "article/10a": root("Article 10a", "article", "10a", [change("ADD", 0)], "Article 10a Text of ten a."),
        "article/3": root("Article 3", "article", "3", [change("ADD", 1)], "Article 3 Target."),  # a paragraph
        "article/22b": root("Article 22b", "article", "22b", [change("ADD", 0)], None),
        "article/9a": root("Article 9a", "article", "9a", [change("ADD", 0)], None, deleted={"date": "2025-01-01"}),
    }
    (folder / "acts").mkdir(parents=True)
    (folder / "acts" / f"{CELEX}.json").write_text(json.dumps({
        "celex": CELEX, "title": "Directive (EU) 2018/2001 on renewable energy",
        "today_version_celex": "02018L2001-20260101", "roots": roots}), encoding="utf-8")
    (folder / "acts" / "32020R0001.json").write_text(json.dumps({
        "celex": "32020R0001", "title": "Other", "roots": {
            "article/5a": root("Article 5a", "article", "5a", [change("ADD", 0)], "Article 5a Some text.")}}),
        encoding="utf-8")
    (folder / "index.json").write_text(json.dumps({CELEX: {}, "32020R0001": {}}), encoding="utf-8")


def write_run(folder):
    batch = folder / "batches" / "0001" / "audit"
    batch.mkdir(parents=True)
    (batch / "run_summary.json").write_text(json.dumps({
        "results": [{"celex": CELEX, "target": "Article 3", "level": "DIRECT"},
                    {"celex": CELEX, "target": "Article 10BIS [source path article/10bis]", "level": "IGNORE"}],
        "not_assessable_targets": [], "failures": []}), encoding="utf-8")


class SelectionTests(unittest.TestCase):
    def test_only_inserted_articles_that_no_run_screened_are_taken(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_export(tmp / "changed")
            write_run(tmp / "run")
            seen = new.screened_roots([tmp / "run"])
            found, rows = new.new_articles(tmp / "changed", seen)
        self.assertEqual(seen[CELEX], {("article", "3"), ("article", "10a")})
        self.assertEqual([item[2] for item in found], ["article/15a"])
        results = {row["provision"]: row["result"] for row in rows}
        self.assertEqual(results, {"Article 10a": "already screened", "Article 15a": "screened now",
                                   "Article 22b": "not found in today's text", "Article 9a": "deleted again",
                                   "Article 5a": "act not screened at provision level: left out"})
        self.assertEqual(new.normal("article", "10QUATER"), ("article", "10c"))
        self.assertEqual(new.normal("annex", "4"), ("annex", "iv"))

    def test_the_pack_can_be_sent_and_saved_like_any_other_target(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write_export(tmp / "changed")
            write_run(tmp / "run")
            found, _rows = new.new_articles(tmp / "changed", new.screened_roots([tmp / "run"]))
            decision = {"level": "DIRECT", "reason": "renewable energy", "anchors": []}
            entries = new.entries(found, {CELEX: decision})
            entry = entries[0]
            self.assertIsNone(entry["not_assessable"])
            self.assertEqual(entry["document_screening"], decision)
            sent, passages, _ = request_pack(entry["pack"], entry["pack"]["provision"])
            self.assertIn("inserted into the act later, by 32023L2413", json.dumps(sent))
            self.assertTrue(passages)
            profile = new.screening.load_profile(ROOT / "config/business_anchors_en.yaml")
            options = Namespace(env_file=None, prepare_only=True, thinking="off", confirm_thinking="off", workers=1)
            out = tmp / "screening" / "batches" / "0001"
            new.screening.run_packs(options, out, entries, "system", profile, [])
            summary = json.loads((out / "audit" / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["targets_planned"], 1)
            self.assertEqual(summary["results"][0]["target"], "Article 15a")
            self.assertTrue(new.batch_folders(tmp / "screening"))   # read by the UNSURE pass and classification


if __name__ == "__main__":
    unittest.main()
