from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import storage.database as database
from ai_scoring import knowledge
from ai_scoring.service import (
    _compact_dimension_review,
    _clean_model_rationale,
    _normalize_candidate_scores,
    _normalize_sector_scores,
    _valuation_position_score,
)


class AIKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_path = database.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.temp_dir.name) / "test.db"

    def tearDown(self) -> None:
        database.DB_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_static_evidence_is_versioned_and_keeps_evidence_levels(self) -> None:
        count = knowledge.sync_static_knowledge()
        documents = knowledge.knowledge_documents()

        self.assertGreaterEqual(count, 7)
        self.assertGreaterEqual(len(documents), 7)
        self.assertIn("direct", {item["evidence_level"] for item in documents})
        self.assertIn("user_primary", {item["evidence_level"] for item in documents})
        self.assertIn("secondary", {item["evidence_level"] for item in documents})

    def test_catalog_title_is_explicitly_weak_evidence(self) -> None:
        original_fetch = knowledge._fetch_json
        knowledge._fetch_json = lambda _: {
            "code": 0,
            "data": {
                "archives": [
                    {
                        "aid": 1,
                        "bvid": "BV1test",
                        "title": "测试赛道标题",
                        "pubdate": 1_700_000_000,
                        "stat": {"view": 10},
                    }
                ],
                "page": {"total": 1},
            },
        }
        try:
            rows = knowledge._collection_documents({"season_id": 1, "name": "测试合集"})
        finally:
            knowledge._fetch_json = original_fetch

        self.assertEqual(rows[0]["evidence_level"], "catalog")
        self.assertIn("不能单独证明", rows[0]["content"])


class DeterministicScoringTests(unittest.TestCase):
    def test_valuation_position_score_rewards_nearness_to_three_year_low(self) -> None:
        self.assertEqual(_valuation_position_score(20), 100)
        self.assertEqual(_valuation_position_score(70), 50)
        self.assertEqual(_valuation_position_score(187), 0)

    def test_model_rationale_drops_conflicting_final_score_claim(self) -> None:
        result = _clean_model_rationale("行业订单回升。综合评分86.4分，建议买入。仍需关注毛利率。")

        self.assertEqual(result, "行业订单回升。仍需关注毛利率。")

    def test_long_dimension_review_is_compacted_to_a_complete_sentence(self) -> None:
        text = "甲" * 79 + "。" + "乙" * 40

        result = _compact_dimension_review(text)

        self.assertEqual(len(result), 80)
        self.assertTrue(result.endswith("。"))

    def test_candidate_final_score_is_recomputed(self) -> None:
        payload = {
            "scores": [
                {
                    "code": "1",
                    "final_score": 99,
                    "dimension_scores": {
                        "行业景气度": 100,
                        "业务纯度": 80,
                        "估值水位": 100,
                        "细分行业龙头": 100,
                        "市场辨识度": 100,
                    },
                    "risk_deduction": 40,
                    "liquidity_coefficient": 0.8,
                }
            ]
        }

        _normalize_candidate_scores(payload, [{"code": "000001", "name": "测试股份"}])
        score = payload["scores"][0]

        self.assertEqual(score["name"], "测试股份")
        self.assertEqual(score["model_final_score"], 99)
        self.assertAlmostEqual(score["final_score"], 75.52)
        self.assertEqual(score["decision"], "watch")

    def test_zero_prosperity_forces_avoid(self) -> None:
        payload = {
            "scores": [
                {
                    "code": "000001",
                    "dimension_scores": {
                        "行业景气度": 0,
                        "业务纯度": 100,
                        "估值水位": 100,
                        "细分行业龙头": 100,
                        "市场辨识度": 100,
                    },
                    "risk_deduction": 0,
                    "liquidity_coefficient": 1.2,
                }
            ]
        }

        _normalize_candidate_scores(payload, [])

        self.assertEqual(payload["scores"][0]["decision"], "avoid")

    def test_sector_without_sources_cannot_score_above_49(self) -> None:
        payload = {"sectors": [{"sector": "测试", "score": 95, "confidence": 0.9, "source_refs": []}]}

        _normalize_sector_scores(payload)

        self.assertEqual(payload["sectors"][0]["score"], 49.0)
        self.assertIn("缺少可追溯 source_refs", payload["sectors"][0]["evidence_gaps"])


if __name__ == "__main__":
    unittest.main()
