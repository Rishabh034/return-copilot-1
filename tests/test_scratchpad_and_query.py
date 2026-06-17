import json
import unittest

from langchain_core.messages import ToolMessage

from app.rr_log_analyst.agent.context import compact_tool_messages_for_llm
from app.rr_log_analyst.agent.result_query import (
    extract_query_keywords,
    match_json_keywords,
)
from app.rr_log_analyst.agent.scratchpad import (
    assemble_scratchpad,
    build_compact_envelope,
    chunk_json_result,
    fallback_scratchpad,
)
from app.rr_log_analyst.memory.tool_results import StoredToolResult, tool_result_store
from app.rr_log_analyst.tools.audit_logs import _shape_audit_logs, _updated_at_bounds
from app.config import settings


class ChunkJsonResultTests(unittest.TestCase):
    def test_small_dict_single_chunk(self):
        result = {"reference_number": "ABC123", "total_ranked": 3}
        chunks = chunk_json_result(result, max_chars=10_000)
        self.assertEqual(len(chunks), 1)
        self.assertIn("ABC123", chunks[0])

    def test_splits_by_top_level_keys(self):
        result = {
            "meta": {"reference_number": "ABC123"},
            "filters_ran": {"3_clickpost_filters_rules_list": ["rule-a", "rule-b"]},
        }
        chunks = chunk_json_result(result, max_chars=40)
        self.assertGreater(len(chunks), 1)
        joined = "".join(chunks)
        self.assertIn("ABC123", joined)
        self.assertIn("rule-a", joined)

    def test_splits_large_array(self):
        result = {"preference_array": [{"cp_name": f"carrier-{index}"} for index in range(50)]}
        chunks = chunk_json_result(result, max_chars=120)
        self.assertGreater(len(chunks), 1)
        joined = "".join(chunks)
        self.assertIn("carrier-0", joined)
        self.assertIn("carrier-49", joined)

    def test_audit_logs_one_entry_per_chunk_when_large(self):
        logs = [
            {"updated_at": "2026-01-01", "username": f"user-{i}", "payload": {"rule": "x" * 1000}}
            for i in range(3)
        ]
        chunks = chunk_json_result({"logs": logs}, max_chars=200)
        self.assertGreaterEqual(len(chunks), 3)


class FallbackScratchpadTests(unittest.TestCase):
    def test_fallback_is_compact(self):
        result = {"items": ["x" * 5000], "count": 1}
        built = fallback_scratchpad("fetch_rr_log", result)
        self.assertEqual(len(built.batches), 1)
        self.assertNotIn("x" * 100, built.scratchpad)
        self.assertIn("count=1", built.scratchpad)


class AssembleScratchpadTests(unittest.TestCase):
    def test_single_batch_unchanged(self):
        self.assertEqual(assemble_scratchpad(["line one"]), "line one")

    def test_multiple_batches_combined_without_truncation(self):
        combined = assemble_scratchpad(["summary A", "summary B", "summary C"])
        self.assertIn("--- batch 1/3 ---", combined)
        self.assertIn("summary A", combined)
        self.assertIn("--- batch 3/3 ---", combined)
        self.assertIn("summary C", combined)

    def test_compact_envelope_includes_full_scratchpad_when_small(self):
        scratchpad = "batch one text\n" + ("detail " * 100)
        envelope = build_compact_envelope(
            result_id="abc",
            tool_name="fetch_audit_logs",
            scratchpad=scratchpad,
            batches=["batch one text", "batch two text"],
            raw_data_parts=["raw1", "raw2"],
        )
        self.assertEqual(envelope["scratchpad_batches"], 2)
        self.assertEqual(envelope["scratchpad"], scratchpad)
        self.assertNotIn("scratchpad_stored", envelope)

    def test_compact_envelope_defers_huge_scratchpad_to_store(self):
        scratchpad = "x" * 100_000
        envelope = build_compact_envelope(
            result_id="abc",
            tool_name="fetch_audit_logs",
            scratchpad=scratchpad,
            batches=["x" * 50_000, "x" * 50_000],
            raw_data_parts=["raw"] * 2,
        )
        self.assertTrue(envelope["scratchpad_stored"])
        self.assertNotIn("scratchpad", envelope)
        self.assertEqual(envelope["scratchpad_total_chars"], 100_000)


class CompactToolMessageTests(unittest.TestCase):
    def test_compacts_legacy_full_scratchpad_message(self):
        conversation_id = "conv-test"
        tool_result_store.put(
            conversation_id,
            StoredToolResult(
                tool_call_id="call-1",
                tool_name="fetch_audit_logs",
                args={},
                result={"count": 1},
                scratchpad="full scratchpad",
                scratchpad_batches=["batch one", "batch two"],
                raw_data_parts=["raw one", "raw two"],
            ),
        )
        huge = json.dumps(
            {
                "result_id": "call-1",
                "tool": "fetch_audit_logs",
                "scratchpad": "x" * 10_000,
            }
        )
        messages = [
            ToolMessage(content=huge, tool_call_id="call-1", name="fetch_audit_logs"),
        ]
        compacted = compact_tool_messages_for_llm(messages, conversation_id)
        parsed = json.loads(compacted[0].content)
        self.assertEqual(parsed["scratchpad_batches"], 2)
        self.assertIn("scratchpad", parsed)
        self.assertNotIn("scratchpad_preview", parsed)
        tool_result_store.purge_conversation(conversation_id)


class KeywordMatcherTests(unittest.TestCase):
    def test_extract_query_keywords(self):
        keywords = extract_query_keywords("Why was Delhivery selected at priority 12?")
        self.assertIn("delhivery", keywords)
        self.assertIn("priority", keywords)
        self.assertIn("12", keywords)
        self.assertNotIn("why", keywords)

    def test_match_json_keywords_on_nested_rr_shape(self):
        data = {
            "reference_number": "REF-1",
            "filters_ran": {
                "clickpost_defined_and_custom_rule_matched": "Priority 12 COD rule",
                "4_ordered_list_rules_list": ["OL-A", "OL-B"],
            },
            "preference_array": [
                {"cp_name": "Delhivery", "priority": 1},
                {"cp_name": "Bluedart", "priority": 2},
            ],
        }
        matched = match_json_keywords(
            data,
            ["priority", "delhivery"],
            max_matches=20,
            max_chars=settings.tool_result_query_max_chars,
        )
        self.assertGreater(matched["match_count"], 0)


class AuditLogShapeTests(unittest.TestCase):
    def test_shape_audit_logs_includes_updated_at_bounds(self):
        shaped = _shape_audit_logs(
            {
                "totalCount": 2,
                "data": [
                    {"updated_at": "2026-06-02T10:00:00", "username": "a"},
                    {"updated_at": "2026-06-01T09:00:00", "username": "b"},
                ],
            },
            "2026-06-01",
            "2026-06-02",
            "Allocation",
        )
        self.assertEqual(shaped["latest_updated_at"], "2026-06-02T10:00:00+00:00")
        self.assertEqual(shaped["earliest_updated_at"], "2026-06-01T09:00:00+00:00")
        self.assertEqual(shaped["logs"][0]["updated_at"], "2026-06-02T10:00:00")

    def test_updated_at_bounds_mixed_naive_and_aware(self):
        bounds = _updated_at_bounds(
            [
                {"updated_at": "2026-06-02T10:00:00"},
                {"payload_updated_at": "2026-06-01T09:00:00.000000Z"},
            ]
        )
        self.assertIsNotNone(bounds["earliest_updated_at"])
        self.assertIsNotNone(bounds["latest_updated_at"])

    def test_updated_at_bounds_from_shaped_logs(self):
        bounds = _updated_at_bounds(
            [
                {"updated_at": "2026-06-03T12:00:00"},
                {"updated_at": "2026-06-01T08:00:00"},
            ]
        )
        self.assertEqual(bounds["earliest_updated_at"], "2026-06-01T08:00:00+00:00")
        self.assertEqual(bounds["latest_updated_at"], "2026-06-03T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
