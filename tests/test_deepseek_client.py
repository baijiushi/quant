from __future__ import annotations

import json
import unittest

from ai_scoring import client as client_module
from ai_scoring.client import DeepSeekClient


class _FakeResponse:
    def __init__(self, payload: bytes | None = None, lines: list[bytes] | None = None) -> None:
        self.payload = payload or b""
        self.lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)

    def read(self) -> bytes:
        return self.payload


class DeepSeekClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_urlopen = client_module.request.urlopen

    def tearDown(self) -> None:
        client_module.request.urlopen = self.original_urlopen

    def test_stream_collects_reasoning_and_json_content(self) -> None:
        events = [
            {"choices": [{"delta": {"reasoning_content": "先核对证据"}}]},
            {"choices": [{"delta": {"content": '{"scores":'}}]},
            {"choices": [{"delta": {"content": "[]}"}}], "usage": {"total_tokens": 12}},
        ]
        lines = [f"data: {json.dumps(item, ensure_ascii=False)}\n".encode("utf-8") for item in events]
        lines.append(b"data: [DONE]\n")
        client_module.request.urlopen = lambda *_args, **_kwargs: _FakeResponse(lines=lines)
        chunks: list[tuple[str, str]] = []

        payload, metadata = DeepSeekClient(api_key="test").chat_json_stream(
            [{"role": "user", "content": "output json"}],
            on_chunk=lambda kind, text: chunks.append((kind, text)),
        )

        self.assertEqual(payload, {"scores": []})
        self.assertEqual(metadata["reasoning_content"], "先核对证据")
        self.assertEqual(metadata["usage"]["total_tokens"], 12)
        self.assertIn(("reasoning", "先核对证据"), chunks)

    def test_anthropic_web_search_extracts_sources(self) -> None:
        response = {
            "model": "deepseek-v4-flash",
            "content": [
                {"type": "thinking", "thinking": "检索公告"},
                {"type": "server_tool_use", "input": {"query": "测试股份 年报"}},
                {
                    "type": "web_search_tool_result",
                    "content": [
                        {"type": "web_search_result", "title": "测试公告", "url": "https://example.com/a"}
                    ],
                },
                {"type": "text", "text": "已核验公开资料。"},
            ],
            "usage": {"input_tokens": 10},
        }
        client_module.request.urlopen = lambda *_args, **_kwargs: _FakeResponse(
            payload=json.dumps(response, ensure_ascii=False).encode("utf-8")
        )

        result = DeepSeekClient(api_key="test").web_search("查询测试股份")

        self.assertEqual(result["summary"], "已核验公开资料。")
        self.assertEqual(result["queries"], ["测试股份 年报"])
        self.assertEqual(result["sources"][0]["url"], "https://example.com/a")


if __name__ == "__main__":
    unittest.main()
