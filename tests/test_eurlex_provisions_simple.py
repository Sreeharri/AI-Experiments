import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

from eurlex_enrichment.enrichment import build
from eurlex_provision.evidence import Evidence
from eurlex_provision.profile import NO_MATCH, NONE, NULL_ALIASES, choices, company_profile, system_prompt
from eurlex_provision.quality import (ENDS_MID_SENTENCE, IMAGE_ONLY, NO_TEXT, PDF_PAGE_TABLE, REFERENCE_ONLY,
                                      STARTS_MID_SENTENCE, TABLE_OF_CONTENTS, assess)
from eurlex_provision.simple import (REPLY_FIELDS, REVIEW_TASK, SCORE_TASK, TARGET_CHARS, ContextLimitError,
                                     ModelError, TargetScreeningError, build_pack, derive, normalise,
                                     number_passages, request_pack, reply_schema, screen, size, split_target,
                                     split_text, target_text, validate_decision)
from lr_ai.qwen_client import ChatResult
import run_eurlex_provision_corpus as corpus
import screen_eurlex_provisions_simple as runner
from test_corpus_source_recovery import CELEX, PDF, VERSION, data, element

ROOT = Path(runner.__file__).resolve().parent
CATALOG = yaml.safe_load((ROOT / "config/business_anchors_en.yaml").read_text(encoding="utf-8-sig"))
PROFILE = company_profile(CATALOG, {"industrial_emissions": ["Max Bögl manufactures cement."]})
OPTIONS = choices(PROFILE)
PROMPT = (ROOT / "prompts/eurlex_provision_simple.md").read_text(encoding="utf-8")
REVIEW_PROMPT = (ROOT / "prompts/eurlex_provision_ignore_review.md").read_text(encoding="utf-8")
SYSTEM = system_prompt(PROMPT, REVIEW_PROMPT, PROFILE)


ANCHOR = "construction_materials_production"
STATE = "STATE_OR_AUTHORITY"
ANSWERS = {
    "DIRECT": {"company_role": "plant_operator"},
    "UNSURE": {"company_role": "plant_operator", "open_question": "Is the plant above 50 MW?"},
    "INDIRECT": {"indirect_path": "suppliers_prices"},
    "IGNORE": {"actor": "Member States", "actor_kind": STATE, "formal_function": "ADDRESSEES", "topic": NONE},
}


def reply(level="DIRECT", **changes):
    """Answers that lead to the given level."""
    value = {"actor": "operators of plants", "actor_kind": "BUSINESS", "subject": "keeping of records",
             "target_passage": "P1", "formal_function": NONE, "topic": ANCHOR, "other_anchors": [],
             "company_role": NONE, "indirect_path": NONE, "open_question": "", "reason": "Fixture reason."}
    return {**value, **ANSWERS[level], **changes}


def chat_result(value, **usage):
    return ChatResult(ok=True, finish_reason="stop", usage=usage,
                      content=value if isinstance(value, str) else json.dumps(value))


def client_with(*values):
    client = Mock()
    client.clean = lambda text: text
    client.chat.side_effect = [value if isinstance(value, ChatResult) else chat_result(value) for value in values]
    return client


def sent_parts(call):
    """Return (system, evidence, task) of one request."""
    system, user = (message["content"] for message in call.args[0])
    evidence, _, task = user.partition("\n\nTASK:")
    return system, json.loads(evidence), "TASK:" + task


def pack_for(provision, **parts):
    return {"document": {"celex": "32000L0001", "version_id": "v", "title": "A test act"},
            "provision": provision, "direct_references": [], "essential_context": [], "footnotes": [],
            "unavailable_references": [], **parts}


def grid(rows, page=3):
    return {"source": {"page": page, "bbox": [0, 0, 10, 10]}, "rows": [{
        "row": number, "row_group": 1, "group_type": "tbody", "cells": [{
            "column": column, "text": text, "header": False, "rowspan": 1, "colspan": 1,
            "source": {"page": page, "bbox": [0, 0, 1, 1]}, "links": [], "footnote_ids": [], "image_sources": [],
        } for column, text in enumerate(row, 1)],
    } for number, row in enumerate(rows, 1)]}


def enriched_act(extra=()):
    """A PDF-stored act: one article that cites Annex I, the annex text and one PDF page table."""
    source = data(PDF)
    source["legal_elements"].extend(extra)
    tables = {"annex": [grid([["Record", "Years"], ["Operating log", "5"]])]}
    with patch("eurlex_enrichment.enrichment.pdf_tables", return_value=(tables, {"candidates": 1})):
        return build(source, PDF, "STORED_SNAPSHOT", recover_from_database=True)


class QualityTests(unittest.TestCase):
    def test_stored_fragments_from_the_review_sample_are_not_assessable(self):
        cases = (
            ("ANNEX", "Annex I", "Annex I, Section 3.1.1.", REFERENCE_ONLY),
            ("ARTICLE", "Article 2", "Article 2(1) Article 4(1)", REFERENCE_ONLY),
            ("ARTICLE", "Article 25", "Article 25(1), (3) and (4), Article 26, Article 28(1), Article 28a, "
                                      "Article 29, Article 31, Article 34a and", REFERENCE_ONLY),
            ("ARTICLE", "Article 16", "Article 16 - Sufficiency of tender prices .......... 61", TABLE_OF_CONTENTS),
            ("ARTICLE", "Article 1", "Article 1", NO_TEXT),
            ("ANNEX", "Annex", 'ANNEX NOMENCLATURE OF INDUSTRIES Branches and Groups >PIC FILE= "T9001291"> '
                               '>PIC FILE= "T9001292">', IMAGE_ONLY),
        )
        for kind, label, text, expected in cases:
            with self.subTest(text=text):
                skip, _ = assess(kind, label, [text])
                self.assertEqual(skip and skip["code"], expected)

    def test_pdf_page_table_is_left_to_its_annex(self):
        skip, _ = assess("TABLE", "Annex, unnumbered table 8", ["Rawhidesandskinsofgoats | 4103.90.10"],
                         table_origin="PDF_PAGE_GRID")
        self.assertEqual(skip["code"], PDF_PAGE_TABLE)
        self.assertEqual(assess("TABLE", "Annex I, Table 2", ["Dust | 10 mg/Nm3"]), (None, []))

    def test_real_provisions_are_sent(self):
        cases = (
            ("ARTICLE", "Article 16", "Article 16 Addressees This Directive is addressed to the Member States."),
            ("ARTICLE", "Article 63", "Article 63 The chargeable event shall occur and VAT shall become "
                                      "chargeable when the goods or the services are supplied."),
            ("ANNEX", "Annex III", "Annex III is replaced by the text set out in Annex III to this Regulation;"),
            ("ARTICLE", "Article 5", "Article 5 Articles 3 and 4 shall apply."),
            ("ARTICLE", "Article 3", "Article 3 The procedure for attesting conformity as set out in Annex III "
                                     "shall be indicated in mandates for harmonised standards."),
        )
        for kind, label, text in cases:
            with self.subTest(text=text):
                self.assertEqual(assess(kind, label, [text]), (None, []))

    def test_cut_off_text_is_sent_with_a_warning(self):
        _, warnings = assess("ANNEX", "Annex", ["Annex, does not meet the essential requirements of such "
                                                "legislation, it shall inform the Association Council."])
        self.assertEqual(warnings, [STARTS_MID_SENTENCE])
        _, warnings = assess("ARTICLE", "Article 13", ["Article 13 Procedure for partly completed machinery 1. "
                                                       "The manufacturer shall ensure that the documentation "
                                                       "described in"])
        self.assertEqual(warnings, [ENDS_MID_SENTENCE])


class ProfileTests(unittest.TestCase):
    def test_system_prompt_holds_the_whole_catalog_and_nothing_about_a_target(self):
        for part in ("agriculture_fisheries", "statistics", "suppliers_prices", "plant_operator",
                     "Max Bögl is generally not", "bridges_tunnels", "Max Bögl manufactures cement.",
                     "CONFIRMATION PASS"):
            self.assertIn(part, SYSTEM)
        self.assertEqual(SYSTEM, system_prompt(PROMPT, REVIEW_PROMPT, company_profile(
            CATALOG, {"industrial_emissions": ["Max Bögl manufactures cement."]})))
        self.assertNotIn("celex", SYSTEM.casefold())

    def test_choices_come_from_the_catalog(self):
        self.assertEqual(OPTIONS["anchors"], list(CATALOG["anchors"]))
        self.assertEqual(OPTIONS["topics"], [NONE, *CATALOG["anchors"], *CATALOG["negative_anchors"],
                                             NO_MATCH, *NULL_ALIASES])
        self.assertEqual(OPTIONS["roles"], [NONE, *CATALOG["roles"], *NULL_ALIASES])
        self.assertEqual(OPTIONS["indirect_paths"], [NONE, *CATALOG["indirect_paths"], *NULL_ALIASES])
        # A reply that starts with "no" must be able to end as "none" and not only as an anchor.
        self.assertTrue({"none", "noise_vibration"} <= set(OPTIONS["topics"]))
        clash = {**CATALOG, "negative_anchors": {"site_safety": {"label": "x", "text": "y"}}}
        with self.assertRaisesRegex(ValueError, "share IDs"):
            choices(company_profile(clash))

    def test_incomplete_catalog_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "negative_anchors"):
            company_profile({"anchors": CATALOG["anchors"]})
        with self.assertRaisesRegex(ValueError, "unknown anchors"):
            company_profile(CATALOG, {"no_such_anchor": ["x"]})


class PassageAndSplitTests(unittest.TestCase):
    def test_target_text_is_sent_once_with_markers(self):
        words = " ".join(f"word{n}" for n in range(80))
        provision = {"locator": "Article 1", "text": words, "source_location": {"page": 1}}
        target, passages = number_passages(provision)
        self.assertEqual(list(passages), ["P1", "P2", "P3"])
        self.assertEqual(" ".join(passages.values()), words)
        self.assertTrue(target["text"].startswith("[P1] word0 "))
        self.assertNotIn("source_location", target)
        sent, _, _ = request_pack(pack_for(provision), provision)
        self.assertEqual(json.dumps(sent).count("word42"), 1)

    def test_table_rows_get_one_marker_and_captions_lose_source_fields(self):
        table = {"locator": "Annex I, Table 1",
                 "caption": [{"text": "Emission levels", "source": {"xpath": "/a/b"}}],
                 "rows": [[{"column": 1, "text": "Pollutant", "header": True}, {"column": 2, "text": "Limit"}],
                          [{"column": 1, "text": "Dust"}, {"column": 2, "text": "10 mg/Nm3"}]]}
        target, passages = number_passages(table)
        self.assertEqual(passages, {"P1": "Emission levels", "P2": "Pollutant | Limit", "P3": "Dust | 10 mg/Nm3"})
        self.assertEqual(target["rows"][1][0]["text"], "[P3] Dust")
        self.assertEqual(target["caption"], [{"text": "[P1] Emission levels"}])

    def test_split_text_keeps_every_word_and_prefers_paragraph_starts(self):
        text = " ".join(f"{n}. Operators shall keep record number {n} for the authority." for n in range(1, 200))
        parts = split_text(text, 1500)
        self.assertGreater(len(parts), 5)
        self.assertEqual(" ".join(parts).split(), text.split())
        self.assertTrue(all(len(part) <= 1500 for part in parts))
        self.assertTrue(all(part.split(". ")[0].isdigit() for part in parts))

    def test_long_article_annex_and_table_are_split_without_loss(self):
        article = {"locator": "Article 5", "heading": "Duties",
                   "text": " ".join(f"{n}. The operator shall check item {n}." for n in range(1, 900))}
        rows = [[{"column": 1, "text": "Substance", "header": True}, {"column": 2, "text": "Limit", "header": True}]]
        rows += [[{"column": 1, "text": f"Substance {n}"}, {"column": 2, "text": f"{n} mg"}] for n in range(600)]
        annex = {"locator": "Annex I", "sections": [
            {"locator": f"Annex I, section {n}", "blocks": [{"text": f"Requirement {n}. " * 150}]} for n in range(12)
        ] + [{"locator": "Annex I, table section", "blocks": [{"table": {"locator": "Table 1", "caption": [],
                                                                        "rows": rows}}]}]}
        for provision in (article, annex):
            with self.subTest(target=provision["locator"]):
                parts = split_target(provision, TARGET_CHARS)
                self.assertGreater(len(parts), 1)
                self.assertTrue(all(size(part) <= TARGET_CHARS for part in parts))
                self.assertTrue(all(part["locator"] == provision["locator"] for part in parts))
                joined = " ".join(text for part in parts for text in target_text(part)).split()
                original = " ".join(target_text(provision)).split()
                if provision is article:
                    self.assertEqual(joined, original)
                else:
                    # The table header row is repeated in every table part; nothing else may differ.
                    repeated = len(joined) - len(original)
                    self.assertEqual(repeated % 2, 0)
                    self.assertGreater(repeated, 0)
                    self.assertEqual({word: joined.count(word) for word in ("Requirement", "mg")},
                                     {word: original.count(word) for word in ("Requirement", "mg")})
        table_parts = split_target({"locator": "Table 1", "caption": [], "rows": rows}, 3000)
        self.assertTrue(all(part["rows"][0] == rows[0] for part in table_parts))
        self.assertEqual(sum(len(part["rows"]) - 1 for part in table_parts), 600)

    def test_row_span_is_never_cut(self):
        rows = [[{"column": 1, "text": "Group A", "rowspan": 3}, {"column": 2, "text": "x" * 900}],
                [{"column": 2, "text": "y" * 900}], [{"column": 2, "text": "z" * 900}],
                [{"column": 1, "text": "Group B"}, {"column": 2, "text": "w" * 900}]]
        parts = split_target({"locator": "Table 2", "caption": [], "rows": rows}, 2000)
        self.assertEqual([len(part["rows"]) for part in parts], [3, 1])


class DecisionRuleTests(unittest.TestCase):
    PASSAGES = {"P1": "Operators shall keep records."}

    def test_level_follows_from_the_answers(self):
        cases = (
            (dict(company_role="importer"), ("DIRECT", "ROLE")),
            (dict(company_role="importer", actor_kind="NOT_STATED"), ("DIRECT", "ROLE")),
            (dict(company_role="importer", open_question="Does it import this itself?"), ("UNSURE", "OPEN_QUESTION")),
            (dict(indirect_path="purchased_products"), ("INDIRECT", "PATH")),
            # The actor is a business in a business area, but no link was named: too uncertain to remove.
            (dict(), ("UNSURE", "TOPIC_ONLY")),
            (dict(actor_kind="NOT_STATED"), ("UNSURE", "TOPIC_ONLY")),
            (dict(actor_kind=STATE), ("IGNORE", "NO_PATH")),
            (dict(actor_kind="PRIVATE_PERSON"), ("IGNORE", "NO_PATH")),
            (dict(actor_kind=STATE, open_question="Is it registered?"), ("UNSURE", "OPEN_QUESTION")),
            # A role named for a state or an EU body is not a direct duty of the company.
            (dict(actor_kind=STATE, company_role="construction_product_manufacturer",
                  indirect_path="purchased_products"), ("INDIRECT", "PATH")),
            (dict(actor_kind="EU_OR_INTERNATIONAL_BODY", company_role="employer"), ("UNSURE", "ROLE_ACTOR_CONFLICT")),
            # Formal clauses are removed, unless the reply itself names a business as the actor.
            (dict(formal_function="ADDRESSEES", actor_kind=STATE, company_role="employer"), ("IGNORE", "FORMAL")),
            (dict(formal_function="COMMITTEE_PROCEDURE", indirect_path="purchased_products"),
             ("UNSURE", "FORMAL_CONFLICT")),
            # A negative anchor as topic removes the target whatever role was named next to it.
            (dict(topic="food_feed", company_role="importer"), ("IGNORE", "OUTSIDE_BUSINESS")),
            (dict(topic="eu_administration_budget", company_role="employer", indirect_path="market_funding"),
             ("IGNORE", "OUTSIDE_BUSINESS")),
            (dict(topic=NO_MATCH), ("IGNORE", "NO_MATCH")),
            (dict(topic=NO_MATCH, indirect_path="market_funding"), ("UNSURE", "LINK_WITHOUT_TOPIC")),
        )
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertEqual(derive(normalise(reply("INDIRECT", **{"indirect_path": NONE, **changes})), OPTIONS), expected)

    def test_every_spelling_of_no_value_means_the_same(self):
        for word in (NONE, NO_MATCH, *NULL_ALIASES):
            with self.subTest(word=word):
                other = NONE if word == NO_MATCH else word  # NO_MATCH is a spelling for the topic only
                value = reply("IGNORE", formal_function=other, topic=word, company_role=other, indirect_path=other)
                decision, details = validate_decision(value, self.PASSAGES, OPTIONS)
                self.assertEqual((decision["level"], details["basis"], details["topic"], details["company_role"],
                                  details["formal_function"]), ("IGNORE", "NO_MATCH", NO_MATCH, NONE, NONE))

    def test_export_fields_and_details(self):
        for level in ("DIRECT", "INDIRECT", "UNSURE", "IGNORE"):
            with self.subTest(level=level):
                decision, details = validate_decision(reply(level), self.PASSAGES, OPTIONS)
                self.assertEqual((decision["level"], decision["area"]),
                                 (level, [] if level == "IGNORE" else [ANCHOR]))
                self.assertEqual(details["target_quote"], "Operators shall keep records.")
                self.assertEqual(details["ignore_reason"], "FORMAL" if level == "IGNORE" else NONE)
                self.assertEqual(details["formal_function"], "ADDRESSEES" if level == "IGNORE" else NONE)
        decision, details = validate_decision(reply("UNSURE"), self.PASSAGES, OPTIONS)
        self.assertEqual(decision["reason"], "Fixture reason. To check: Is the plant above 50 MW?")
        decision, details = validate_decision(
            reply("DIRECT", other_anchors=["site_safety", ANCHOR]), self.PASSAGES, OPTIONS)
        self.assertEqual(decision["area"], [ANCHOR, "site_safety"])
        decision, details = validate_decision(reply("IGNORE", formal_function=NONE, topic="statistics",
                                                    other_anchors=["site_safety"]), self.PASSAGES, OPTIONS)
        self.assertEqual((decision["area"], details["negative_anchor"], details["ignore_reason"]),
                         ([], "statistics", "OUTSIDE_BUSINESS"))

    def test_malformed_replies_are_rejected(self):
        cases = (
            (reply(target_passage="P9"), "target_passage"),
            (reply(reason=" "), "non-empty reason"),
            (reply(actor=""), "non-empty actor"),
            (reply(topic="invented_anchor"), "listed values"),
            (reply(company_role="astronaut"), "listed values"),
            (reply(actor_kind="ROBOT"), "listed values"),
            (reply(formal_function="BORING"), "listed values"),
            (reply(other_anchors=["statistics"]), "other_anchors"),
            (reply(open_question="x" * 300), "open_question"),
            ({**reply(), "level": "DIRECT"}, "exactly these fields"),
        )
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ModelError, message):
                validate_decision(value, self.PASSAGES, OPTIONS)

    def test_schema_has_no_level_and_asks_actor_and_subject_first(self):
        schema = reply_schema({"P1": "a", "P2": "b"}, OPTIONS)
        fields = schema["properties"]
        self.assertEqual(list(fields), list(REPLY_FIELDS))
        self.assertEqual(schema["required"], list(REPLY_FIELDS))
        self.assertNotIn("level", fields)
        self.assertEqual(list(fields)[:3], ["actor", "actor_kind", "subject"])
        self.assertLess(list(fields).index("formal_function"), list(fields).index("topic"))
        self.assertIn("TRANSPOSITION_DEADLINE", fields["formal_function"]["enum"])
        self.assertEqual(fields["target_passage"]["enum"], ["P1", "P2"])
        self.assertIn("statistics", fields["topic"]["enum"])
        self.assertIn(ANCHOR, fields["topic"]["enum"])
        self.assertNotIn("statistics", fields["other_anchors"]["items"]["enum"])


class RequestLayoutTests(unittest.TestCase):
    PROVISION = {"locator": "Article 4", "text": "Operators of asphalt plants shall keep records of fuel use."}

    def test_request_is_cacheable_and_review_repeats_the_first_request(self):
        pack = pack_for(self.PROVISION)
        client = client_with(reply("IGNORE"), reply("IGNORE"))
        decision, first = screen(client, SYSTEM, PROFILE, pack)
        screen(client, SYSTEM, PROFILE, pack, first=first)
        (system1, evidence1, task1), (system2, evidence2, task2) = map(sent_parts, client.chat.call_args_list)
        self.assertEqual(system1, SYSTEM)
        self.assertEqual(system2, SYSTEM)
        self.assertEqual(evidence1, evidence2)
        self.assertEqual(task1, SCORE_TASK)
        # The confirmation pass is a second opinion: it does not see the first answers.
        self.assertEqual(task2, REVIEW_TASK)
        self.assertNotIn("ADDRESSEES", task2)
        self.assertNotIn("business_anchors", evidence1)
        first_user = client.chat.call_args_list[0].args[0][1]["content"]
        second_user = client.chat.call_args_list[1].args[0][1]["content"]
        shared = len(first_user) - len(SCORE_TASK)
        self.assertEqual(first_user[:shared], second_user[:shared])
        options = client.chat.call_args.kwargs
        self.assertEqual((options["thinking"], options["sampling"], options["max_tokens"]),
                         (False, {"temperature": 0}, 1024))

    def test_thinking_uses_the_client_defaults_and_more_output_room(self):
        answer = chat_result(reply("DIRECT"))
        answer.reasoning = "The actor is the operator."
        client = client_with(answer)
        _, audit = screen(client, SYSTEM, PROFILE, pack_for(self.PROVISION), thinking=True)
        options = client.chat.call_args.kwargs
        self.assertTrue(options["thinking"])
        self.assertNotIn("sampling", options)
        self.assertEqual(options["max_tokens"], 4096)
        self.assertEqual(audit["parts"][0]["thinking"], "The actor is the operator.")
        self.assertEqual(client.chat.call_args.args[0][0]["content"], SYSTEM)

    def test_correction_is_appended_after_the_task(self):
        client = client_with(reply("IGNORE", target_passage="P9"), reply("DIRECT"))
        decision, audit = screen(client, SYSTEM, PROFILE, pack_for(self.PROVISION))
        self.assertEqual(decision["level"], "DIRECT")
        first, second = (call.args[0] for call in client.chat.call_args_list)
        self.assertEqual(second[0]["content"], SYSTEM)
        self.assertTrue(second[1]["content"].startswith(first[1]["content"]))
        self.assertIn("CORRECTION", second[1]["content"])
        self.assertIn("target_passage", audit["parts"][0]["rejected_reply"]["error"])

    def test_two_invalid_replies_fail_only_this_target(self):
        client = client_with(reply(target_passage="P9"), reply(target_passage="P9"))
        with self.assertRaises(TargetScreeningError) as caught:
            screen(client, SYSTEM, PROFILE, pack_for(self.PROVISION))
        self.assertEqual(len(caught.exception.attempts), 2)

    def test_context_is_limited_and_omissions_are_named(self):
        long_reference = {"locator": "Article 9", "text": "Reference sentence. " * 2000,
                          "cited": [{"locator": "Article 9(2)", "text": "The cited paragraph."}]}
        references = [long_reference] + [{"locator": f"Article {n}", "text": "Other text. " * 120}
                                         for n in range(20, 40)]
        pack = pack_for(self.PROVISION, direct_references=references,
                        unavailable_references=[{"instrument": "31971L0320", "locator": "Annex I",
                                                 "reason": "External excerpt was not supplied."}])
        sent, _, trimmed = request_pack(pack, self.PROVISION, act_context={"level": "DIRECT", "reason": "r",
                                                                           "anchors": ["site_safety"]},
                                        warnings=["Its end is missing."])
        self.assertEqual(sent["references"][0], {"locator": "Article 9", "excerpt": True,
                                                 "text": "Article 9(2): The cited paragraph."})
        self.assertLess(size(sent["references"]), 5600)
        self.assertTrue(sent["context_not_included"])
        self.assertEqual(sent["missing_references"][0]["instrument"], "31971L0320")
        self.assertEqual(sent["act"]["document_screening"]["anchors"], ["site_safety"])
        self.assertEqual(sent["source_notes"], ["Its end is missing."])
        self.assertGreater(trimmed["context_left_out"], 0)
        self.assertLess(size(sent), 12000)

    def test_long_target_is_classified_in_parts_and_takes_the_highest_level(self):
        text = " ".join(f"{n}. The operator shall check item {n} every year." for n in range(1, 900))
        pack = pack_for({"locator": "Article 5", "text": text})
        parts = len(split_target(pack["provision"], TARGET_CHARS))
        self.assertGreater(parts, 2)
        client = client_with(reply("IGNORE"), reply("INDIRECT"), *[reply("IGNORE")] * (parts - 2))
        decision, audit = screen(client, SYSTEM, PROFILE, pack)
        self.assertEqual(client.chat.call_count, parts)
        self.assertEqual(decision["level"], "INDIRECT")
        self.assertTrue(decision["reason"].startswith(f"Classified in {parts} parts; relevant: part 2."))
        self.assertEqual([item["decision"]["level"] for item in audit["parts"]][:3], ["IGNORE", "INDIRECT", "IGNORE"])
        _, second, _ = sent_parts(client.chat.call_args_list[1])
        self.assertEqual(second["target"]["part"], f"2 of {parts}")
        self.assertEqual(second["position"][0]["relation"], "opening words of the whole target")
        self.assertTrue(second["position"][0]["text"].startswith("1. The operator shall check item 1"))
        sent_words = " ".join(passage for call in client.chat.call_args_list
                              for passage in number_passages(sent_parts(call)[1]["target"])[1].values())
        self.assertEqual(len(audit["requests"]), parts)
        self.assertIn("item 899", sent_words)

    def test_context_limit_splits_the_part_again(self):
        text = " ".join(f"{n}. The operator shall check item {n}." for n in range(1, 260))
        limit = ChatResult(ok=False, error_kind="context_limit", error="maximum context length")
        client = client_with(limit, reply("IGNORE"), reply("DIRECT"))
        decision, audit = screen(client, SYSTEM, PROFILE, pack_for({"locator": "Article 6", "text": text}))
        self.assertEqual(decision["level"], "DIRECT")
        self.assertEqual([item["part"] for item in audit["parts"]], ["1.1", "1.2"])
        tiny = client_with(limit)
        with self.assertRaises(ContextLimitError):
            screen(tiny, SYSTEM, PROFILE, pack_for({"locator": "Article 7", "text": "Short text."}))

    def test_endpoint_failure_stops_the_run(self):
        client = client_with(ChatResult(ok=False, error_kind="timeout", error="Timed out"))
        with self.assertRaises(ModelError) as caught:
            screen(client, SYSTEM, PROFILE, pack_for(self.PROVISION))
        self.assertNotIsInstance(caught.exception, TargetScreeningError)


class PackTests(unittest.TestCase):
    def setUp(self):
        self.enriched = enriched_act()
        self.evidence = runner.UniqueTargetEvidence(self.enriched, {})

    def test_annex_gets_the_provision_that_refers_to_it_and_skips_page_tables(self):
        pack = build_pack(self.evidence, self.evidence.units["annex"])
        self.assertEqual(pack["page_tables_read_as_text"], ["Annex I, Stored annex text [section 1], unnumbered table 1"])
        self.assertNotIn("Operating log", json.dumps(pack["provision"]))
        self.assertIn("Keep the operating log", json.dumps(pack["provision"]))
        governing = [item for item in pack["essential_context"]
                     if item.get("relation") == "provision that refers to the target"]
        self.assertEqual(governing[0]["locator"], "Article 1")
        self.assertIn("shall retain the records described in Annex I", governing[0]["text"])
        self.assertNotIn("business_anchors", pack)

    def test_table_context_is_only_the_text_before_the_table(self):
        section = self.enriched["sections"]["annex:database:1"]
        section["blocks"].insert(0, {"text": "Quarries and gravel pits. " * 400, "source": {}, "footnote_ids": []})
        evidence = runner.UniqueTargetEvidence(self.enriched, {})
        pack = build_pack(evidence, evidence.units["annex:pdf-table:1"])
        lead = pack["essential_context"][0]
        self.assertEqual(lead["relation"], "text just before the target table")
        self.assertLess(len(lead["text"]), 1300)
        self.assertTrue(lead["text"].endswith("Keep the operating log in the records for five years."))

    def test_article_keeps_its_reference_to_the_annex(self):
        pack = build_pack(self.evidence, self.evidence.units["article"])
        self.assertIn("Keep the operating log", json.dumps(pack["direct_references"]))


class RunnerTests(unittest.TestCase):
    EXTRA = (
        element("a2", "ARTICLE", "Article 2", 3, "Annex I, Section 3.1.", "2"),
        element("a3", "ARTICLE", "Article 3", 4, "This Directive is addressed to the Member States.", "3"),
        element("a4", "ARTICLE", "Article 4", 5, "and the operator shall inform the authority of any change.", "4"),
    )

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "enriched").mkdir()
        (self.root / "enriched" / f"{CELEX}_enriched.json").write_text(
            json.dumps(enriched_act(self.EXTRA)), encoding="utf-8")
        (self.root / "manifest.json").write_text(json.dumps({CELEX: VERSION}), encoding="utf-8")
        export = self.root / "document_run" / "relevance_export"
        export.mkdir(parents=True)
        (export / "direct.json").write_text(json.dumps({CELEX: {
            "level": "DIRECT", "reason": "Records duty for plant operators.", "area": ["industrial_emissions"],
        }}), encoding="utf-8")
        (export / "indirect.json").write_text("{}", encoding="utf-8")

    def args(self, **changes):
        values = dict(mode="pilot", celex=None, target=None, targets_file=None, prepare_only=False, thinking="off",
                      confirm_thinking="off",
                      enriched_dir=self.root / "enriched", manifest=self.root / "manifest.json",
                      anchors=ROOT / "config/business_anchors_en.yaml", document_run=self.root / "document_run",
                      supplements=None, env_file=self.root / ".env", output_dir=self.root / "out")
        return SimpleNamespace(**{**values, **changes})

    def fake_client(self):
        client = Mock(model="qwen3-30b-a3b")
        client.discover.return_value = {"ok": True}
        client.clean = lambda text: text

        def chat(messages, **options):
            _, evidence, task = sent_parts(SimpleNamespace(args=[messages]))
            text = json.dumps(evidence["target"])
            if "addressed to the Member States" in text:
                return chat_result(reply("IGNORE"), prompt_tokens=12000,
                                   prompt_tokens_details={"cached_tokens": 11000})
            return chat_result(reply("DIRECT", topic="industrial_emissions"), prompt_tokens=12000)

        client.chat.side_effect = chat
        return client

    def run_with(self, client, **changes):
        with patch.object(runner.QwenClient, "from_env", return_value=client):
            failed = runner.run(self.args(**changes))
        out = changes.get("output_dir", self.root / "out")
        summary = json.loads((out / "audit/run_summary.json").read_text(encoding="utf-8"))
        return failed, summary, out

    def test_run_skips_broken_input_reviews_ignores_and_reports(self):
        client = self.fake_client()
        failed, summary, out = self.run_with(client)
        self.assertFalse(failed)
        self.assertEqual(summary["targets_planned"], 6)
        self.assertEqual(summary["not_assessable"], {"REFERENCE_ONLY": 1, "PDF_PAGE_TABLE": 1})
        self.assertEqual(summary["counts"], {"DIRECT": 3, "IGNORE": 1})
        self.assertEqual(summary["ignore_reasons"], {"FORMAL": 1})
        self.assertEqual(summary["decision_basis"], {"ROLE": 3, "FORMAL": 1})
        self.assertEqual(summary["company_roles"], {"plant_operator": 3})
        self.assertEqual(summary["topics"], {"industrial_emissions": 3, NO_MATCH: 1})
        self.assertEqual(summary["formal_functions"], {"ADDRESSEES": 1})
        self.assertEqual(summary["actor_kinds"], {"BUSINESS": 3, STATE: 1})
        self.assertEqual(summary["targets_kept"], 3)
        self.assertEqual(summary["ignore_reviews"], {"confirmed": 1, "changed": 0, "pending": 0})
        self.assertEqual(summary["input_warnings"], {"STARTS_MID_SENTENCE": 1})
        self.assertEqual(summary["targets_with_incomplete_text"][0]["target"], "Article 4")
        self.assertEqual(summary["requests"]["count"], 5)
        self.assertEqual(summary["requests"]["cached_tokens"], 22000)
        self.assertEqual(summary["acts_all_ignore"], [])
        self.assertEqual({row["target"]: row["code"] for row in summary["not_assessable_targets"]},
                         {"Article 2": "REFERENCE_ONLY",
                          "Annex I, Stored annex text [section 1], unnumbered table 1": "PDF_PAGE_TABLE"})

        results = json.loads((out / "relevance_export/results.json").read_text(encoding="utf-8"))
        self.assertEqual(set(results[CELEX]), {"Article 1", "Article 3", "Article 4", "Annex I"})
        self.assertEqual(results[CELEX]["Article 3"], {"level": "IGNORE", "reason": "Fixture reason.", "area": []})
        self.assertEqual(results[CELEX]["Article 1"]["area"], ["industrial_emissions"])

        systems = {call.args[0][0]["content"] for call in client.chat.call_args_list}
        config = json.loads((out / "audit/run_config.json").read_text(encoding="utf-8"))
        self.assertEqual(systems, {config["system_prompt"]})
        sent = [sent_parts(call) for call in client.chat.call_args_list]
        self.assertNotIn("Section 3.1.", json.dumps([evidence["target"] for _, evidence, _ in sent]))
        self.assertTrue(all(evidence["act"]["document_screening"]["anchors"] == ["industrial_emissions"]
                            for _, evidence, _ in sent))
        warned = [evidence for _, evidence, _ in sent if evidence["target"]["locator"] == "Article 4"]
        self.assertIn("starts in the middle of a sentence", warned[0]["source_notes"][0])

        calls = client.chat.call_count
        failed, again, _ = self.run_with(client)
        self.assertEqual((failed, client.chat.call_count), (False, calls))
        self.assertEqual(again["counts"], summary["counts"])

    def test_act_with_only_ignored_provisions_is_listed_for_document_review(self):
        client = self.fake_client()
        client.chat.side_effect = lambda messages, **options: chat_result(
            reply("IGNORE", formal_function=NONE, topic="statistics", company_role="large_undertaking"))
        _, summary, _ = self.run_with(client)
        self.assertEqual(summary["acts_all_ignore"], [CELEX])
        self.assertEqual(summary["negative_anchors"], {"statistics": 4})
        self.assertEqual(summary["ignore_reasons"], {"OUTSIDE_BUSINESS": 4})
        self.assertEqual(summary["targets_kept"], 0)

    def test_changed_review_exports_the_confirmed_level(self):
        client = self.fake_client()
        answers = iter([reply("IGNORE", formal_function=NONE, topic=ANCHOR), reply("INDIRECT")])
        client.chat.side_effect = lambda messages, **options: chat_result(next(answers))
        _, summary, out = self.run_with(client, celex=[CELEX], target=["Article 3"])
        self.assertEqual(summary["ignore_reviews"]["changed"], 1)
        self.assertEqual(summary["counts"], {"INDIRECT": 1})
        self.assertEqual(summary["indirect_paths"], {"suppliers_prices": 1})

    def test_targets_file_limits_the_run(self):
        chosen = self.root / "targets.json"
        chosen.write_text(json.dumps({CELEX: ["Article 3", "Article 2"]}), encoding="utf-8")
        client = self.fake_client()
        _, summary, _ = self.run_with(client, targets_file=chosen)
        self.assertEqual((summary["targets_planned"], summary["targets_exported"],
                          summary["targets_not_assessable"]), (2, 1, 1))
        self.assertEqual(summary["targets_not_found"], [])
        chosen.write_text(json.dumps({CELEX: ["Article 99", "Article 3"]}), encoding="utf-8")
        _, summary, _ = self.run_with(client, targets_file=chosen, output_dir=self.root / "other")
        self.assertEqual(summary["targets_not_found"], [{"celex": CELEX, "target": "Article 99"}])
        self.assertEqual(summary["targets_planned"], 1)
        chosen.write_text(json.dumps({CELEX: ["Article 99"]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "No target was selected"):
            self.run_with(client, targets_file=chosen, output_dir=self.root / "third")
        with self.assertRaisesRegex(ValueError, "target locator not found"):
            self.run_with(client, celex=[CELEX], target=["Article 99"], output_dir=self.root / "fourth")

    def test_failed_target_is_reported_and_retried(self):
        client = self.fake_client()
        client.chat.side_effect = lambda messages, **options: chat_result(reply(target_passage="P99"))
        failed, summary, _ = self.run_with(client, celex=[CELEX], target=["Article 1"])
        self.assertTrue(failed)
        self.assertEqual((summary["targets_failed"], summary["targets_exported"]), (1, 0))
        client.chat.side_effect = lambda messages, **options: chat_result(reply("DIRECT"))
        failed, summary, _ = self.run_with(client, celex=[CELEX], target=["Article 1"])
        self.assertEqual((failed, summary["targets_failed"], summary["targets_exported"]), (False, 0, 1))

    def test_corpus_export_adds_up_batches(self):
        output = self.root / "corpus"
        batch = output / "batches" / "0001"
        self.run_with(self.fake_client(), output_dir=batch)
        plan = {"batches": [{"manifest": {CELEX: VERSION}, "targets": 6}], "targets": 6,
                "acts_with_provisions": 1, "document_text_only": [], "without_targets": []}
        finished = corpus.export_results(SimpleNamespace(output_dir=output), plan, None)
        self.assertTrue(finished)
        summary = json.loads((output / "run_summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["targets_exported"], summary["targets_not_assessable"],
                          summary["targets_pending"], summary["targets_failed"]), (4, 2, 0, 0))
        self.assertEqual(summary["counts"], {"DIRECT": 3, "IGNORE": 1, "INDIRECT": 0, "UNSURE": 0})
        self.assertEqual(summary["not_assessable"], {"REFERENCE_ONLY": 1, "PDF_PAGE_TABLE": 1})
        self.assertEqual(summary["ignore_reasons"], {"FORMAL": 1})
        self.assertEqual(summary["requests"]["count"], 5)
        listed = json.loads((output / "not_assessable.json").read_text(encoding="utf-8"))
        self.assertEqual(len(listed), 2)
        results = json.loads((output / "relevance_export/results.json").read_text(encoding="utf-8"))
        self.assertEqual(len(results[CELEX]), 4)

    def test_unsure_is_kept_with_its_question_and_thinking_is_passed_on(self):
        client = self.fake_client()
        client.chat.side_effect = lambda messages, **options: chat_result(reply("UNSURE"))
        _, summary, out = self.run_with(client, celex=[CELEX], target=["Article 1"], thinking="on")
        self.assertEqual((summary["counts"], summary["targets_kept"]), ({"UNSURE": 1}, 1))
        self.assertEqual(summary["decision_basis"], {"OPEN_QUESTION": 1})
        results = json.loads((out / "relevance_export/results.json").read_text(encoding="utf-8"))
        self.assertTrue(results[CELEX]["Article 1"]["reason"].endswith("To check: Is the plant above 50 MW?"))
        self.assertTrue(client.chat.call_args.kwargs["thinking"])
        config = json.loads((out / "audit/run_config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["thinking"], config["document_hint"]), (True, True))
        with self.assertRaisesRegex(ValueError, "Run inputs changed"):
            self.run_with(client, celex=[CELEX], target=["Article 1"], thinking="off")

    def test_confirm_thinking_applies_to_the_confirmation_pass_only(self):
        client = self.fake_client()
        _, summary, out = self.run_with(client, celex=[CELEX], target=["Article 3"], confirm_thinking="on")
        first, second = client.chat.call_args_list
        self.assertEqual((first.kwargs["thinking"], second.kwargs["thinking"]), (False, True))
        self.assertEqual(summary["ignore_reviews"]["confirmed"], 1)
        config = json.loads((out / "audit/run_config.json").read_text(encoding="utf-8"))
        self.assertEqual((config["thinking"], config["confirm_thinking"]), (False, True))

    def test_run_without_document_hint(self):
        client = self.fake_client()
        _, _, out = self.run_with(client, celex=[CELEX], target=["Article 1"], document_run=None)
        _, evidence, _ = sent_parts(client.chat.call_args)
        self.assertNotIn("document_screening", evidence["act"])
        config = json.loads((out / "audit/run_config.json").read_text(encoding="utf-8"))
        self.assertFalse(config["document_hint"])

    def test_prepare_only_saves_packs_without_a_model(self):
        with patch.object(runner.QwenClient, "from_env", side_effect=AssertionError("no model call expected")):
            runner.run(self.args(prepare_only=True))
        summary = json.loads((self.root / "out/audit/run_summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["counts"], summary["targets_not_assessable"]), ({"PREPARED": 6}, 2))
        self.assertFalse((self.root / "out/relevance_export").exists())


class LabelComparisonTests(unittest.TestCase):
    def test_unsure_counts_as_kept_and_removed_relevant_rows_are_counted(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import compare_provision_labels as tool

        labels = [{"id": str(n), "label": label, "celex": "A", "target": f"Article {n}"}
                  for n, label in enumerate(("DIRECT", "IGNORE", "INDIRECT", "IGNORE", "UNSURE", "INDIRECT",
                                             "INDIRECT"), 1)]
        decisions = {("A", f"Article {n}"): {"level": level, "reason": "r", "answers": {"actor_kind": "BUSINESS"}}
                     for n, level in enumerate(("UNSURE", "UNSURE", "IGNORE", "IGNORE", "DIRECT", None, "IGNORE"), 1)
                     if level}
        table, summary = tool.compare(labels, decisions, {("A", "Article 6"): "REFERENCE_ONLY"},
                                      excluded={("A", "Article 7"): "wrong stored text"})
        self.assertEqual([row["Outcome"] for row in table], [
            "kept, to check", "kept but labelled IGNORE", "REMOVED but labelled relevant", "same",
            "no usable label", "not assessable (REFERENCE_ONLY)", "left out of the scores (wrong stored text)"])
        self.assertEqual((summary["removed"], summary["removed_but_labelled_relevant"],
                          summary["kept"], summary["kept_but_labelled_ignore"], summary["left_out"]), (2, 1, 2, 1, 1))
        self.assertEqual(table[0]["actor_kind"], "BUSINESS")
        self.assertIn("UNSURE", tool.report(summary))

        checks = tool.read_json(ROOT / "config/eurlex_provision_checked_cases.json")
        run = {("32006L0112", "Article 63"): {"level": "IGNORE"}, ("32011R0284", "Article 2"): {"level": "IGNORE"}}
        lines = tool.checked_cases(checks, run)
        self.assertEqual(len(lines), len(checks["cases"]))
        self.assertTrue(any(line.startswith("WRONG 32006L0112 Article 63: IGNORE") for line in lines))
        self.assertTrue(any(line.startswith("ok    32011R0284 Article 2: IGNORE") for line in lines))


class CorpusRunnerTests(unittest.TestCase):
    def test_batch_is_complete_when_every_target_is_classified_or_reported(self):
        done = {"targets_exported": 8, "targets_not_assessable": 2, "targets_failed": 0, "error": None}
        self.assertTrue(corpus.complete(done, 10))
        self.assertFalse(corpus.complete({**done, "targets_failed": 1}, 10))
        self.assertFalse(corpus.complete({**done, "targets_exported": 7}, 10))
        self.assertFalse(corpus.complete({}, 0))


if __name__ == "__main__":
    unittest.main()
