from __future__ import annotations

import time
import unittest

from fastapi.testclient import TestClient

import backend.app as backend_app


class BackendAIJobTests(unittest.TestCase):
    def test_ai_job_streams_reasoning_and_result(self) -> None:
        original = backend_app.score_latest_candidates

        def fake_score(**kwargs):
            kwargs["progress_callback"]("联网检索测试资料")
            kwargs["stream_callback"]("reasoning", "正在核对主营与风险")
            kwargs["stream_callback"]("content", '{"scores":[]}')
            return {
                "generated_at": "2026-07-13T00:00:00",
                "model": "deepseek-v4-flash",
                "reasoning_content": "正在核对主营与风险",
                "scores": [],
            }

        backend_app.score_latest_candidates = fake_score
        backend_app._ai_score_jobs.clear()
        client = TestClient(backend_app.app)
        try:
            created = client.post(
                "/api/ai/candidate-scores/jobs",
                json={"strategy_id": "b1", "max_candidates": 1, "web_research": True},
            )
            self.assertEqual(created.status_code, 200)
            job_id = created.json()["job_id"]
            for _ in range(50):
                status = client.get(f"/api/ai/candidate-scores/jobs/{job_id}").json()
                if status["status"] == "success":
                    break
                time.sleep(0.01)

            self.assertEqual(status["status"], "success")
            self.assertIn("核对主营", status["reasoning"])
            self.assertEqual(status["result"]["model"], "deepseek-v4-flash")
            event_response = client.get(f"/api/ai/candidate-scores/jobs/{job_id}/events")
            self.assertIn('"status": "success"', event_response.text)
        finally:
            backend_app.score_latest_candidates = original
            backend_app._ai_score_jobs.clear()


if __name__ == "__main__":
    unittest.main()
