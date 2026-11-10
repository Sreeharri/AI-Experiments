from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import Mock

from eurlex_provision.evidence import branch_batches
from eurlex_provision.grading import check_planned, grade, load_expected
from eurlex_provision.screening import (
    COMPANY, CONTEXT_ONLY, OPEN, OTHER_ACTOR, OUTSIDE, ModelError, combine, derive, finalize,
    held_reason, numbered_facts, response_schema, review_schema, score_batch, score_verified_batch,
    validate_answer,
)
from eurlex_provision.thresholds import numeric_checks
from lr_ai.qwen_client import ChatResult
from screen_eurlex_provisions import check_scenario, load_exports, write_relevance_export

FACTS = ["The company makes finished precast elements in Germany.",
         "The scenario covers only the finished product.",
         "No conformity file has been supplied."]


def payload(*branches, can_request=True):
    return {"document": {"celex": "32024R3110", "version_id": "pinned"},
            "target": "Article 2", "candidate_anchors": {"precast": {}, "supply": {}},
            "scenario": {"id": "test", "evidence_status": "SYNTHETIC_TEST_CASE",
                         "facts": numbered_facts(FACTS)},
            "branches": [{"locator": locator} for locator in (branches or ("Article 2(1)",))],
            "context": {"passages": [{"locator": "Article 3(1)", "text": "Definition fixture."}]},
            "footnotes": [], "retrieved_context": [], "retrieval_log": [],
            "can_request_context": can_request}


def branch(locator, status, facts=("Fact 1",), refs=()):
    return {"locator": locator, "reason": f"Fixture reason for {locator}.", "status": status,
            "fact_refs": list(facts), "evidence_refs": list(refs)}


def answer(*branches, area=("precast",)):
    return {"result": {"celex": "32024R3110", "source_version_id": "pinned", "target": "Article 2",
                       "branch_results": list(branches), "reason": "Fixture overall reason.",
                       "provision_function": "SCOPE", "area": list(area), "verification_notes": []},
            "context_requests": []}


def review(*issues, notes=()):
    return {"issues": list(issues), "notes": list(notes)}


def issue(locator, proposed, basis="Fact 2"):
    return {"locator": locator, "problem": "Fixture problem.", "basis": basis, "proposed_status": proposed}


def reply(data, finish_reason="stop"):
    return ChatResult(ok=True, finish_reason=finish_reason,
                      content=data if isinstance(data, str) else json.dumps(data))


def client_with(*data):
    client = Mock()
    client.chat.side_effect = [reply(item) for item in data]
    return client


def stored(target="Article 2", level="DIRECT", review_status="COMPLETE"):
    return {"celex": "32024R3110", "source_version_id": "pinned", "target": target,
            "provision_function": "SCOPE", "level": level, "reason": "Fixture reason.",
            "area": ["precast"] if level in ("DIRECT", "INDIRECT") else [],
            "evidence_refs": ["Article 2(1)"], "review_status": review_status,
            "missing_evidence": [] if review_status == "COMPLETE" else ["Article 2(1): gap"],
            "verification_notes": [],
            "branch_results": [branch("Article 2(1)", COMPANY, refs=["Article 2(1)"])]}


class LevelTests(unittest.TestCase):
    def test_level_and_review_status_come_from_branch_statuses(self):
        for statuses, expected in (
            ((COMPANY, OUTSIDE), ("DIRECT", "COMPLETE")),
            ((COMPANY, OPEN), ("DIRECT", "NEEDS_REVIEW")),
            ((OTHER_ACTOR, CONTEXT_ONLY), ("INDIRECT", "COMPLETE")),
            ((OTHER_ACTOR, OPEN), (None, "NEEDS_REVIEW")),
            ((OUTSIDE, CONTEXT_ONLY), ("IGNORE", "COMPLETE")),
            ((OPEN,), (None, "NEEDS_REVIEW")),
        ):
            with self.subTest(statuses=statuses):
                self.assertEqual(derive(statuses), expected)

    def test_supplier_branches_give_indirect_whatever_label_qwen_would_pick(self):
        # v5 BAT 17: both replies described the supplier's kiln but said DIRECT.
        inputs = payload("BAT 17", "BAT 17, row 1")
        client = client_with(
            answer(branch("BAT 17", OTHER_ACTOR, facts=["Fact 1", "Fact 2"]),
                   branch("BAT 17, row 1", CONTEXT_ONLY, facts=[])),
            review(),
        )
        result, _, checks = score_verified_batch(client, "score", "review", Mock(), inputs)
        self.assertEqual((result["level"], result["review_status"]), ("INDIRECT", "COMPLETE"))
        self.assertEqual(result["area"], ["precast"])
        self.assertEqual(result["evidence_refs"], ["BAT 17"])
        self.assertEqual(checks["initial_blocking"], [])
        self.assertEqual(client.chat.call_count, 2)

    def test_open_branch_lists_its_gap_and_leaves_the_level_open(self):
        inputs = payload("Article 2(1)", "Article 2(2)")
        value = answer(branch("Article 2(1)", OTHER_ACTOR), branch("Article 2(2)", OPEN, facts=[]))
        validate_answer(value, inputs)
        result = finalize(value["result"])
        self.assertIsNone(result["level"])
        self.assertEqual(result["area"], [])
        self.assertEqual(result["missing_evidence"], ["Article 2(2): Fixture reason for Article 2(2)."])

    def test_own_locator_is_added_and_ignore_clears_area(self):
        value = answer(branch("Article 2(1)", OUTSIDE, refs=["Article 3(1)"]))
        validate_answer(value, payload())
        result = finalize(value["result"])
        self.assertEqual((result["level"], result["area"]), ("IGNORE", []))
        self.assertEqual(result["branch_results"][0]["evidence_refs"], ["Article 2(1)", "Article 3(1)"])

    def test_rollup_preserves_review_and_direct_result(self):
        direct = stored()
        pending = deepcopy(direct)
        pending.update(level=None, area=[], missing_evidence=["Role missing"], review_status="NEEDS_REVIEW")
        combined = combine([direct, pending], "Article 2 / Scope")
        self.assertEqual(combined["level"], "DIRECT")
        self.assertEqual(combined["review_status"], "NEEDS_REVIEW")
        self.assertEqual(combined["missing_evidence"], ["Role missing"])
        indirect = {**direct, "level": "INDIRECT"}
        self.assertIsNone(combine([indirect, pending], "Article 2 / Scope")["level"])


class CitationTests(unittest.TestCase):
    def test_schema_offers_only_supplied_locators_fact_ids_and_identity(self):
        schema = response_schema(payload("Article 2(1)", "Article 2(2)"))
        decision = schema["properties"]["result"]["anyOf"][0]
        variants = decision["properties"]["branch_results"]["items"]["anyOf"]
        with_facts = variants[0]["properties"]
        self.assertEqual(with_facts["evidence_refs"]["items"]["enum"],
                         ["Article 2(1)", "Article 2(2)", "Article 3(1)"])
        self.assertEqual(with_facts["fact_refs"]["items"]["enum"], ["Fact 1", "Fact 2", "Fact 3"])
        self.assertEqual(with_facts["fact_refs"]["minItems"], 1)
        self.assertEqual(variants[1]["properties"]["status"]["enum"], [CONTEXT_ONLY, OPEN])
        self.assertEqual(variants[1]["properties"]["fact_refs"]["minItems"], 0)
        self.assertEqual(decision["properties"]["target"]["enum"], ["Article 2"])
        self.assertNotIn("level", decision["properties"])
        order = list(decision["properties"])
        self.assertLess(order.index("branch_results"), order.index("reason"))
        self.assertLess(list(with_facts).index("reason"), list(with_facts).index("status"))

    def test_without_retrieval_budget_the_schema_requires_a_result(self):
        schema = response_schema(payload(can_request=False))
        self.assertNotIn("anyOf", schema["properties"]["result"])
        self.assertEqual(schema["properties"]["context_requests"]["maxItems"], 0)

    def test_scenario_sentence_in_evidence_refs_is_rejected(self):
        # v5 BAT 17: Qwen cited full scenario sentences as legal locators.
        value = answer(branch("Article 2(1)", COMPANY, refs=[FACTS[0]]))
        with self.assertRaisesRegex(ValueError, "Cite scenario facts in fact_refs"):
            validate_answer(value, payload())

    def test_fact_ids_required_for_applies_and_outside_but_not_context(self):
        for status in (COMPANY, OTHER_ACTOR, OUTSIDE):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "fact IDs"):
                validate_answer(answer(branch("Article 2(1)", status, facts=[])), payload())
        validate_answer(answer(branch("Article 2(1)", CONTEXT_ONLY, facts=[])), payload())

    def test_threshold_conflict_rejects_an_applies_status(self):
        inputs = payload()
        inputs["branches"][0]["text"] = "Rated thermal input of less than or equal to 5 MW."
        inputs["numeric_checks"] = numeric_checks(inputs["branches"], ["A boiler with 8 MW rated thermal input."])
        self.assertEqual(inputs["numeric_checks"][0]["status"], "NOT_MET")
        with self.assertRaisesRegex(ValueError, "does not meet the threshold"):
            validate_answer(answer(branch("Article 2(1)", COMPANY)), inputs)
        validate_answer(answer(branch("Article 2(1)", OUTSIDE)), inputs)

    def test_invalid_anchor_identity_and_missing_branch_rejected(self):
        good = answer(branch("Article 2(1)", COMPANY))
        for key, value in (("area", ["invented_anchor"]), ("area", []),
                           ("branch_results", []), ("source_version_id", "other-version")):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_answer({**good, "result": {**good["result"], key: value}}, payload())


class ReviewTests(unittest.TestCase):
    def test_issue_that_leaves_the_result_unchanged_is_only_a_note(self):
        # v5 Article 2: the check objected to the introductory line; the level was right.
        inputs = payload("Article 2(1)", "Article 2(2)")
        draft = answer(branch("Article 2(1)", COMPANY), branch("Article 2(2)", CONTEXT_ONLY, facts=[]))
        client = client_with(draft, review(issue("Article 2(2)", OUTSIDE), issue("Article 2(1)", COMPANY),
                                           notes=["Reason could cite Article 3(1)."]))
        result, _, checks = score_verified_batch(client, "score", "review", Mock(), inputs)
        self.assertEqual((result["level"], result["review_status"]), ("DIRECT", "COMPLETE"))
        self.assertEqual(checks["initial_blocking"], [])
        self.assertEqual(len(checks["initial_issues"]), 2)
        self.assertEqual(client.chat.call_count, 2)

    def test_level_changing_issue_gets_one_correction_with_the_draft(self):
        inputs = payload("BAT 17")
        wrong = answer(branch("BAT 17", COMPANY))
        fixed = answer(branch("BAT 17", OTHER_ACTOR, facts=["Fact 1", "Fact 2"]))
        dispute = issue("BAT 17", OTHER_ACTOR)
        client = client_with(wrong, review(dispute), fixed, review())
        result, _, checks = score_verified_batch(client, "score", "review", Mock(), inputs)
        self.assertEqual((result["level"], result["review_status"]), ("INDIRECT", "COMPLETE"))
        self.assertEqual(checks["initial_blocking"], [dispute])
        correction = json.loads(client.chat.call_args_list[2].args[0][1]["content"])
        self.assertEqual(correction["scenario_review_feedback"], [dispute])
        self.assertEqual(correction["previous_draft"]["level"], "DIRECT")
        second_review = json.loads(client.chat.call_args_list[3].args[0][1]["content"])
        self.assertNotIn("scenario_review_feedback", second_review)

    def test_unresolved_dispute_holds_only_the_disputed_branch(self):
        inputs = payload("Article 2(1)", "Article 2(1)(a)")
        draft = answer(branch("Article 2(1)", COMPANY), branch("Article 2(1)(a)", OUTSIDE))
        dispute = issue("Article 2(1)", OUTSIDE, basis="Article 2(1)(a)")
        client = client_with(draft, review(dispute), draft, review(dispute))
        result, _, checks = score_verified_batch(client, "score", "review", Mock(), inputs)
        self.assertEqual((result["level"], result["review_status"]), (None, "NEEDS_REVIEW"))
        held, kept = result["branch_results"]
        self.assertEqual((held["status"], held["draft_status"]), (OPEN, COMPANY))
        self.assertIn("Article 2(1)(a)", held["reason"])
        self.assertEqual(kept["status"], OUTSIDE)
        self.assertEqual(held_reason({**result, "scenario_reviews": [checks]}), "SCENARIO_CHECK")

    def test_review_runs_with_thinking_unless_switched_off(self):
        for thinking, temperature, tokens in ((True, 0.6, 8192), (False, 0, 900)):
            with self.subTest(thinking=thinking):
                client = client_with(answer(branch("Article 2(1)", COMPANY)), review())
                score_verified_batch(client, "score", "review", Mock(), payload(), thinking)
                scoring, checking = client.chat.call_args_list
                self.assertFalse(scoring.kwargs["thinking"])
                self.assertEqual(checking.kwargs["thinking"], thinking)
                self.assertEqual(checking.kwargs["sampling"]["temperature"], temperature)
                self.assertEqual(checking.kwargs["max_tokens"], tokens)

    def test_review_basis_is_limited_to_fact_ids_and_supplied_locators(self):
        schema = review_schema(payload("Article 2(1)"))
        item = schema["properties"]["issues"]["items"]["properties"]
        self.assertEqual(item["basis"]["enum"], ["Fact 1", "Fact 2", "Fact 3", "Article 2(1)", "Article 3(1)"])
        self.assertEqual(item["locator"]["enum"], ["Article 2(1)"])

    def test_cut_off_review_is_held_instead_of_stopping_the_run(self):
        client = Mock()
        client.chat.side_effect = [reply(answer(branch("Article 2(1)", COMPANY))),
                                   reply('{"issues":', finish_reason="length")]
        result, _, checks = score_verified_batch(client, "score", "review", Mock(), payload())
        self.assertIsNone(result["level"])
        self.assertIn("Invalid Qwen scenario check", checks["validation_error"])
        self.assertEqual(held_reason({**result, "scenario_reviews": [checks]}), "INVALID_REPLY")


class FlowTests(unittest.TestCase):
    def test_truncation_splits_and_scores_each_branch(self):
        inputs = payload("Article 2(1)", "Article 2(2)")
        client = Mock()
        client.chat.side_effect = [
            ChatResult(ok=True, finish_reason="length", content='{"result":', usage={"completion_tokens": 1400}),
            reply(answer(branch("Article 2(1)", COMPANY))), reply(review()),
            reply({**answer(), "result": {**answer()["result"], "branch_results": [
                branch("Article 2(2)", CONTEXT_ONLY, facts=[])]}}), reply(review()),
        ]
        result, _, checks = score_verified_batch(client, "score", "review", Mock(), inputs)
        self.assertEqual(result["level"], "DIRECT")
        self.assertEqual(len(checks["split_after_truncation"]), 2)
        self.assertEqual([len(a["branches"]) for a in inputs["_attempts"] if a["phase"] == "scoring"], [2, 1, 1])
        self.assertEqual(inputs["_attempts"][0]["finish_reason"], "length")
        self.assertEqual(inputs["_attempts"][0]["raw_reply"], '{"result":')

    def test_invalid_branch_reply_retries_and_is_held(self):
        inputs = payload("Article 2(1)", "Annex table, row 2")
        client = client_with(answer(branch("Article 2(1)", COMPANY)), answer(branch("Article 2(1)", COMPANY)))
        result, _, checks = score_verified_batch(client, "score", "review", Mock(), inputs)
        self.assertEqual(client.chat.call_count, 2)
        self.assertEqual((result["level"], result["review_status"]), (None, "NEEDS_REVIEW"))
        self.assertIn("Missing branch results for Annex table, row 2", checks["validation_error"])
        feedback = json.loads(client.chat.call_args.args[0][1]["content"])
        self.assertIn("Annex table, row 2", feedback["response_validation_feedback"])
        self.assertEqual(feedback["rejected_answer"]["result"]["branch_results"][0]["locator"], "Article 2(1)")
        self.assertEqual(len(result["branch_results"]), 2)
        branches = client.chat.call_args.kwargs["schema"]["properties"]["result"]["anyOf"][0]["properties"]["branch_results"]
        self.assertEqual((branches["minItems"], branches["maxItems"]), (2, 2))

    def test_api_failure_still_stops_for_resume(self):
        client = Mock()
        response = Mock(ok=False, reasoning="", error_kind="timeout", error="Timed out")
        response.parse_json.return_value = None, "Timed out"
        client.chat.return_value = response
        with self.assertRaisesRegex(ModelError, "Qwen request failed"):
            score_verified_batch(client, "score", "review", Mock(), payload())

    def test_model_requests_passage_and_reassesses_original_target(self):
        evidence = Mock()
        passage = {"locator": "Article 17", "text": "Test fixture.", "note_ids": []}
        evidence.retrieve.return_value = passage, None
        evidence.note_evidence.return_value = []
        client = client_with(
            {"result": None, "context_requests": [
                {"locator": "Article 17", "instrument": "", "why": "Need marking context."}]},
            answer(branch("Article 2(1)", COMPANY, refs=["Article 17"])),
        )
        value, trace = score_batch(client, "prompt", evidence, payload())
        self.assertEqual(value["target"], "Article 2")
        self.assertEqual(trace[0]["status"], "RETRIEVED")
        self.assertEqual(value["branch_results"][0]["evidence_refs"], ["Article 2(1)", "Article 17"])
        second = json.loads(client.chat.call_args.args[0][1]["content"])
        self.assertEqual(second["retrieved_context"], [passage])
        variants = client.chat.call_args.kwargs["schema"]["properties"]["result"]["anyOf"][0][
            "properties"]["branch_results"]["items"]["anyOf"]
        self.assertIn("Article 17", variants[0]["properties"]["evidence_refs"]["items"]["enum"])

    def test_unavailable_material_context_stays_pending(self):
        evidence = Mock()
        evidence.retrieve.return_value = None, "External excerpt was not supplied."
        evidence.note_evidence.return_value = []
        request = {"result": None, "context_requests": [
            {"locator": "ref:26", "instrument": "32020L2184", "why": "Need exemption scope."}]}
        client = client_with(request, request)
        value, trace = score_batch(client, "prompt", evidence, payload())
        self.assertEqual((value["level"], value["review_status"]), (None, "NEEDS_REVIEW"))
        self.assertEqual([r["status"] for r in trace], ["UNAVAILABLE", "BUDGET_REACHED"])
        self.assertNotIn("anyOf", client.chat.call_args.kwargs["schema"]["properties"]["result"])

    def test_batching_never_drops_branches(self):
        branches = [{"locator": str(i), "text": "x" * 1000} for i in range(18)]
        batches = list(branch_batches(branches))
        self.assertGreater(len(batches), 1)
        self.assertEqual([b for batch in batches for b in batch], branches)


class RunnerTests(unittest.TestCase):
    def test_exports_have_only_three_fields_and_exclude_review(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            direct = stored()
            indirect = stored("Article 3", "INDIRECT")
            ignored = stored("Article 4", "IGNORE")
            unverified = stored("Article 5", "DIRECT", "NEEDS_REVIEW")
            pending = stored("Article 6", None, "NEEDS_REVIEW")
            write_relevance_export(path, [direct, indirect, ignored, unverified, pending])
            for item in (direct, indirect, ignored):
                data = json.loads((path / f"{item['level'].lower()}.json").read_text(encoding="utf-8"))
                self.assertEqual(data, {item["celex"]: {item["target"]: {
                    key: item[key] for key in ("level", "reason", "area")
                }}})

    def test_document_results_join_by_celex_and_allow_missing(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)
            direct = {"32024R3110": {"level": "DIRECT", "reason": "fixture", "area": ["precast"]}}
            (path / "direct.json").write_text(json.dumps(direct), encoding="utf-8")
            (path / "indirect.json").write_text("{}", encoding="utf-8")
            self.assertEqual(load_exports(path, {"32024R3110": "", "32015L2193": ""}), direct)

    def test_scenario_may_not_carry_labels(self):
        scenario = {"id": "test", "evidence_status": "SYNTHETIC_TEST_CASE", "facts": FACTS}
        check_scenario("32024R3110", scenario)
        with self.assertRaisesRegex(ValueError, "expected labels"):
            check_scenario("32024R3110", {**scenario, "branch_bindings": {}})

    def test_held_reason_names_why_a_target_was_not_exported(self):
        self.assertIsNone(held_reason(stored()))
        open_item = {**stored(review_status="NEEDS_REVIEW"), "scenario_reviews": [{"initial_blocking": []}]}
        self.assertEqual(held_reason(open_item), "OPEN_BRANCH")


class GradingTests(unittest.TestCase):
    LABELS = {"acts": {"32024R3110": {"source_version_id": "pinned", "targets": {
        "Article 2": {"level": "DIRECT", "branches": {"Article 2(1)": COMPANY, "Article 2(2)": CONTEXT_ONLY}},
    }}, "32015L2193": {"source_version_id": "other", "targets": {
        "Article 6(2)": {"level": "DIRECT", "branches": {"Article 6(2), sentence 1": COMPANY}},
    }}}}

    def test_labels_load_for_selected_acts_and_check_version(self):
        expected = load_expected(self.LABELS, {"32024R3110": "pinned"})
        self.assertEqual(list(expected), [("32024R3110", "Article 2")])
        self.assertEqual(expected[("32024R3110", "Article 2")]["review_status"], "COMPLETE")
        with self.assertRaisesRegex(ValueError, "different source version"):
            load_expected(self.LABELS, {"32015L2193": "pinned"})
        self.assertEqual(load_expected(None, {"32024R3110": "pinned"}), {})

    def test_labels_must_name_planned_branches(self):
        expected = load_expected(self.LABELS, {"32024R3110": "pinned"})
        check_planned(expected, "32024R3110", "Article 2", ["Article 2(1)", "Article 2(2)"])
        with self.assertRaisesRegex(ValueError, "Article 2\\(2\\)"):
            check_planned(expected, "32024R3110", "Article 2", ["Article 2(1)"])

    def test_grade_reports_matches_and_the_scorer_status_of_held_branches(self):
        expected = load_expected(self.LABELS, {"32024R3110": "pinned"})
        item = {**stored(level=None, review_status="NEEDS_REVIEW"), "branch_results": [
            {**branch("Article 2(1)", OPEN), "draft_status": COMPANY},
            branch("Article 2(2)", CONTEXT_ONLY, facts=[]),
        ]}
        report = grade(expected, {("32024R3110", "Article 2"): item}, "hash")
        self.assertEqual((report["targets_graded"], report["result_matches"]), (1, 0))
        self.assertEqual((report["branches_labelled"], report["branch_matches"]), (2, 1))
        self.assertEqual(report["targets"][0]["branch_mismatches"], [
            {"locator": "Article 2(1)", "expected": COMPANY, "actual": OPEN, "scorer": COMPANY}])
        self.assertEqual(grade(expected, {})["labelled_targets_not_run"], ["32024R3110 Article 2"])
