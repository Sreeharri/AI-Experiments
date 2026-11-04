from __future__ import annotations

import copy
import csv
from datetime import date
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import prepare_eurlex_dev400 as prep
from document_relevance import profile, read_input

AS_OF = date(2026, 11, 4)
BUSINESS = {"anchors": {}, "roles": {}}


def make_act(index=1):
    celex = f"32022R{index:04d}"
    version_id = f"version-{index}"
    elements = []

    def add(kind, number, text, heading=""):
        elements.append({"version_id": version_id, "path": f"{kind.lower()}/{number}",
                         "element_type": kind, "number": str(number), "heading": heading,
                         "body_text": text, "details": {"aggregate_body": True},
                         "ordinal": len(elements)})
    add("CHAPTER", 1, "", "General provisions")
    for n in range(1, 10):
        add("ARTICLE", n, f"Article {n}. Operators shall keep records for requirement {n}.",
            "Scope" if n == 1 else "Duties")
    for n in range(1, 4):
        add("RECITAL", n, f"Purpose and context of requirement {n}.")
    for n in range(1, 3):
        add("ANNEX", n, f"Technical requirements and table {n}.")
    return {"celex": celex, "document": {"title_en": f"Test act {index}",
                                           "document_date": "2022-01-01"},
            "instrument": {"title": f"Test act {index}"},
            "version": {"id": version_id, "language": "EN", "version_kind": "ORIGINAL",
                        "quality_status": "ACCEPTED", "version_date": None},
            "elements": elements, "recital_set": None}


class MemorySource:
    def __init__(self, acts):
        self.acts = {a["version"]["id"]: a for a in acts}

    def candidates(self, excluded, as_of):
        return [{"celex": a["celex"], "version_id": key} for key, a in self.acts.items()]

    def act(self, candidate):
        return self.acts[candidate["version_id"]]


class EvidenceRequirements(unittest.TestCase):
    def test_complete_pack_uses_current_selector(self):
        act = make_act()
        before = copy.deepcopy(act)
        audit, blocks = prep.audit_act(act, AS_OF)
        selected, problems = prep.verify_selected_pack(blocks, BUSINESS)
        self.assertEqual([], audit["problems"] + problems)
        self.assertEqual(14, len(selected))
        self.assertEqual(before, act)

    def test_title_chapter_and_real_text_are_required(self):
        for missing in ("title", "CHAPTER", "ARTICLE", "RECITAL", "ANNEX"):
            with self.subTest(missing=missing):
                act = make_act()
                if missing == "title":
                    act["document"]["title_en"] = " "
                    act["instrument"]["title"] = ""
                else:
                    for element in act["elements"]:
                        if element["element_type"] == missing:
                            element["body_text"] = ""
                            element["heading"] = ""
                self.assertTrue(prep.audit_act(act, AS_OF)[0]["problems"])

    def test_child_text_is_usable_without_aggregate(self):
        act = make_act()
        article = act["elements"][1]
        article["details"] = {}
        article["body_text"] = ""
        act["elements"].append({"version_id": act["version"]["id"],
                                "path": article["path"] + "/paragraph/1",
                                "element_type": "PARAGRAPH", "body_text": "A concrete duty."})
        self.assertFalse(prep.audit_act(act, AS_OF)[0]["problems"])

    def test_duplicate_article_boundary_does_not_make_nine_articles(self):
        act = make_act()
        act["elements"] = [e for e in act["elements"] if e["path"] != "article/9"]
        repeated = copy.deepcopy(act["elements"][1])
        repeated["path"] += "~duplicate2"
        repeated["body_text"] = "A different quoted provision at a duplicated boundary."
        act["elements"].append(repeated)
        audit, _ = prep.audit_act(act, AS_OF)
        self.assertEqual(8, audit["articles"])
        self.assertTrue(any("9 distinct" in p for p in audit["problems"]))

    def test_reject_mixed_version_elements_or_recitals(self):
        for source in ("element", "recital_set"):
            act = make_act()
            if source == "element":
                act["elements"][1]["version_id"] = "different-version"
            else:
                act["recital_set"] = {"version_id": "different-version", "recitals_json": []}
            self.assertTrue(prep.audit_act(act, AS_OF)[0]["problems"])

    def test_recital_set_fallback_same_version(self):
        act = make_act()
        act["elements"] = [e for e in act["elements"] if e["element_type"] != "RECITAL"]
        act["recital_set"] = {"version_id": act["version"]["id"],
                              "recitals_json": [{"n": n, "text": f"Recital purpose {n}"}
                                                for n in range(1, 4)]}
        audit, blocks = prep.audit_act(act, AS_OF)
        self.assertFalse(audit["problems"])
        self.assertEqual(3, audit["recitals"])
        self.assertFalse(prep.verify_selected_pack(blocks, BUSINESS)[1])

    def test_future_consolidated_version_rejected(self):
        act = make_act()
        act["version"].update(version_kind="CONSOLIDATED", effective_from="2027-01-01")
        self.assertIn("Future consolidated text", prep.audit_act(act, AS_OF)[0]["problems"])

    def test_single_annex_requires_explicit_option_and_zero_is_rejected(self):
        act = make_act()
        act["elements"] = [e for e in act["elements"] if e["path"] != "annex/2"]
        self.assertTrue(prep.audit_act(act, AS_OF)[0]["problems"])
        audit, blocks = prep.audit_act(act, AS_OF, min_annex_chunks=1)
        self.assertFalse(audit["problems"])
        self.assertFalse(prep.verify_selected_pack(blocks, BUSINESS, min_annex_chunks=1)[1])
        act["elements"] = [e for e in act["elements"] if e["element_type"] != "ANNEX"]
        self.assertTrue(prep.audit_act(act, AS_OF, min_annex_chunks=1)[0]["problems"])

    def test_published_original_future_start_is_not_excluded(self):
        act = make_act()
        act["version"]["effective_from"] = "2027-01-01"
        self.assertFalse(prep.audit_act(act, AS_OF)[0]["problems"])

    def test_future_document_and_rejected_version_are_rejected(self):
        for case in ("future", "rejected"):
            act = make_act()
            if case == "future":
                act["document"]["document_date"] = "2027-01-01"
            else:
                act["version"]["quality_status"] = "REJECTED"
            self.assertTrue(prep.audit_act(act, AS_OF)[0]["problems"])


class DatasetWorkflow(unittest.TestCase):
    def test_export_records_explicit_one_annex_policy(self):
        acts = [make_act(n) for n in range(1, 5)]
        for act in acts:
            act["elements"] = [e for e in act["elements"] if e["path"] != "annex/2"]
        with TemporaryDirectory() as temporary, patch("sys.stdout", new=io.StringIO()):
            out = Path(temporary) / "sets"
            manifest = prep.prepare_sets(MemorySource(acts), out, set(), AS_OF, BUSINESS,
                                         per_set=2, min_annex_chunks=1)
            self.assertEqual(1, manifest["input_rules"]["annex_chunks_min"])
            self.assertEqual(2, len(read_input(out / "dev_A_2.json")[0]))

    def generate(self, directory, acts, per_set=2, exclusions=None):
        with patch("sys.stdout", new=io.StringIO()):
            return prep.prepare_sets(MemorySource(acts), directory, exclusions or set(), AS_OF,
                                     BUSINESS, per_set=per_set)

    def test_two_sets_400_unique_unlabelled_and_runner_compatible(self):
        # End-to-end 200 + 200 fixture export. No endpoint/database dependency.
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / "datasets"
            manifest = self.generate(out, [make_act(n) for n in range(1, 405)], per_set=200)
            ids = []
            for name in ("dev_A_200", "dev_B_200"):
                acts, _ = read_input(out / f"{name}.json")
                self.assertEqual(200, len(acts))
                ids.extend(a["celex"] for a in acts)
                with (out / f"{name}_labels.csv").open(encoding="utf-8-sig", newline="") as stream:
                    labels = list(csv.DictReader(stream))
                self.assertEqual(200, len(labels))
                self.assertTrue(all(not row["label"] and not row["anchor_ids"] for row in labels))
            self.assertEqual(400, len(set(ids)))
            self.assertEqual(400, manifest["unique_acts"])
            for name, metadata in manifest["files"].items():
                self.assertEqual(metadata["sha256"], prep.hash_file(out / name))

    def test_selection_and_split_reproduce_despite_candidate_order(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            acts = [make_act(n) for n in range(1, 10)]
            self.generate(root / "first", acts)
            self.generate(root / "second", list(reversed(acts)))
            for name in ("dev_A_2.json", "dev_B_2.json"):
                self.assertEqual((root / "first" / name).read_bytes(),
                                 (root / "second" / name).read_bytes())

    def test_exclusions_and_missing_text_are_backfilled(self):
        acts = [make_act(n) for n in range(1, 8)]
        for element in acts[0]["elements"]:
            if element["element_type"] == "ANNEX":
                element["body_text"] = ""
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / "sets"
            self.generate(out, acts, exclusions={acts[1]["celex"]})
            selected = json.loads((out / "selected_acts.json").read_text())
            self.assertFalse({acts[0]["celex"], acts[1]["celex"]} & {a["celex"] for a in selected})
            self.assertEqual(4, len(selected))

    def test_older_or_german_complete_version_is_fallback(self):
        acts = [make_act(n) for n in range(1, 5)]
        rejected = copy.deepcopy(acts[0])
        rejected["version"]["id"] = "preferred-incomplete"
        rejected["elements"] = []
        acts[0]["version"]["language"] = "DE"
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / "sets"
            self.generate(out, [rejected, *acts])
            records = json.loads((out / "selected_acts.json").read_text())
            record = next(r for r in records if r["celex"] == acts[0]["celex"])
            self.assertEqual("DE", record["language"])
            self.assertEqual(acts[0]["version"]["id"], record["version_id"])

    def test_shortage_never_publishes_partial_sets(self):
        acts = [make_act(n) for n in range(1, 5)]
        acts[0]["elements"] = []
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / "sets"
            with self.assertRaisesRegex(ValueError, "Only 3 acts passed"):
                self.generate(out, acts)
            self.assertFalse(out.exists())
            self.assertTrue(out.with_name("sets_insufficient.json").is_file())
            diagnostic = json.loads(out.with_name("sets_insufficient.json").read_text())
            cache = Path(diagnostic["eligible_cache"])
            saved, _ = read_input(cache / "eligible_acts.json")
            self.assertEqual(3, len(saved))
            self.assertEqual("insufficient", json.loads((cache / "eligible_manifest.json").read_text())["status"])

    def test_shortlist_shortage_writes_no_sets(self):
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / "sets"
            with self.assertRaisesRegex(ValueError, "Only 1 shortlisted"):
                self.generate(out, [make_act()])
            self.assertFalse(out.exists())

    def test_existing_directory_protected(self):
        with TemporaryDirectory() as temporary:
            out = Path(temporary)
            marker = out / "keep.txt"
            marker.write_text("existing data")
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.generate(out, [make_act(n) for n in range(1, 5)])
            self.assertEqual("existing data", marker.read_text())

    def test_additional_exclusion_formats(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            text, csv_path = root / "ids.txt", root / "ids.csv"
            text.write_text("# note\n32022R0001\n")
            csv_path.write_text("celex,label\n32022R0002,\n")
            self.assertEqual({"32022R0001", "32022R0002"}, prep.read_exclusions([text, csv_path]))
            text.write_text("not-a-celex\n")
            with self.assertRaises(ValueError):
                prep.read_exclusions([text])

    def test_real_profile_works_without_qwen(self):
        business, _ = profile(ROOT / "config/business_anchors_en.yaml", ROOT / "config/domains.yaml")
        _, blocks = prep.audit_act(make_act(), AS_OF)
        self.assertFalse(prep.verify_selected_pack(blocks, business)[1])


if __name__ == "__main__":
    unittest.main()
