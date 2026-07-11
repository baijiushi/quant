from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request

import yaml

from ai_scoring.client import DeepSeekClient
from storage.database import (
    latest_candidate_ai_scores as db_latest_candidate_scores,
    latest_candidate_run,
    latest_sector_scores as db_latest_sector_scores,
    list_research_documents,
    save_candidate_ai_scores,
    save_sector_scores,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "ai_scoring.yaml"
LATEST_CANDIDATES = ROOT / "data" / "candidates" / "candidates_latest.json"


def _read_yaml(path: Path = CONFIG_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve(path_like: str | Path) -> Path:
    path = Path(path_like)
    return path if path.is_absolute() else ROOT / path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_url_text(url: str) -> str:
    req = request.Request(url, headers={"User-Agent": "oversell-ai-scoring/0.1"})
    with request.urlopen(req, timeout=20) as response:
        raw = response.read(2_000_000)
    return raw.decode("utf-8", errors="ignore")


def _load_news_inputs(input_dir: Path, max_chars: int) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    if not input_dir.exists():
        return documents
    for path in sorted(input_dir.glob("*")):
        if path.suffix.lower() not in {".txt", ".md", ".json", ".csv"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        documents.append({"source": str(path.relative_to(ROOT)), "content": text[:max_chars]})
    return documents


def collect_sector_sources() -> list[dict[str, str]]:
    cfg = _read_yaml()
    scoring_cfg = cfg.get("scoring", {})
    max_chars = int(scoring_cfg.get("max_source_chars", 24000))
    input_dir = _resolve(scoring_cfg.get("news_input_dir", "data/news_inputs"))
    documents = _load_news_inputs(input_dir, max_chars)
    # Research notes are intentionally explicit inputs: they can contain a video
    # summary or a licensed report excerpt without pretending that it was scraped.
    for item in list_research_documents(limit=50):
        documents.append(
            {
                "source": item.get("source_url") or f"research:{item.get('title', 'untitled')}",
                "content": str(item.get("content", ""))[:max_chars],
            }
        )

    for url in scoring_cfg.get("source_urls", []) or []:
        try:
            documents.append({"source": str(url), "content": _fetch_url_text(str(url))[:max_chars]})
        except Exception as exc:  # noqa: BLE001
            documents.append({"source": str(url), "content": f"FETCH_FAILED: {exc}"})
    return documents


def _client_from_config() -> DeepSeekClient:
    cfg = _read_yaml().get("deepseek", {})
    return DeepSeekClient(
        base_url=str(cfg.get("base_url", "https://api.deepseek.com")),
        model=str(cfg.get("model", "deepseek-chat")),
        temperature=float(cfg.get("temperature", 0.2)),
        max_tokens=int(cfg.get("max_tokens", 4096)),
    )


def latest_sector_scores() -> dict[str, Any]:
    stored = db_latest_sector_scores()
    if stored:
        return stored
    out_dir = _resolve(_read_yaml().get("scoring", {}).get("sector_output_dir", "data/ai_scoring"))
    return _read_json(out_dir / "sector_scores_latest.json")


def refresh_sector_scores(extra_context: str | None = None) -> dict[str, Any]:
    cfg = _read_yaml()
    out_dir = _resolve(cfg.get("scoring", {}).get("sector_output_dir", "data/ai_scoring"))
    documents = collect_sector_sources()
    source_blob = json.dumps(documents, ensure_ascii=False)[: int(cfg.get("scoring", {}).get("max_source_chars", 24000))]

    messages = [
        {
            "role": "system",
            "content": (
                "你是A股超景气赛道研究员。方法论参考超景气价值投机框架："
                "只在政策拐点、技术突破、订单爆发、供需反转或现象级事件有可核验材料时给高分。"
                "只根据用户提供的资料评分，不要编造未提供的新闻、机构观点或公司关系。"
                "证据不足时 score 必须低于 50，并明确写 evidence_gaps 与 source_refs。输出严格JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                "请按超景气价值投机模型，识别当前或即将超景气的A股赛道。"
                "重点关注政策拐点、技术重大突破、突发订单、行业反转、现象级事件。"
                "输出JSON字段：generated_at, sectors。sectors数组每项包含：sector, score(0-100), "
                "opportunity_type, catalysts, evidence, source_refs, risk_notes, evidence_gaps, confidence(0-1)。\n"
                f"额外背景：{extra_context or ''}\n"
                f"资料：{source_blob}"
            ),
        },
    ]
    payload = _client_from_config().chat_json(messages)
    payload.setdefault("generated_at", datetime.now().isoformat(timespec="seconds"))
    payload.setdefault("source_count", len(documents))
    payload.setdefault("methodology", "super-boom-v2")
    save_sector_scores(payload, documents, model=_read_yaml().get("deepseek", {}).get("model"))
    _write_json(out_dir / "sector_scores_latest.json", payload)
    dated = out_dir / f"sector_scores_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    _write_json(dated, payload)
    return payload


def _load_candidate_run(strategy_id: str | None = None) -> dict[str, Any]:
    stored = latest_candidate_run(strategy_id)
    if stored:
        return stored
    if strategy_id:
        path = ROOT / "data" / "candidates" / f"candidates_latest_{strategy_id}.json"
        if path.exists():
            return _read_json(path)
    return _read_json(LATEST_CANDIDATES)


def _load_stocklist_rows(limit_codes: set[str]) -> list[dict[str, str]]:
    path = ROOT / "data" / "stocklist.csv"
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = str(row.get("代码", "")).zfill(6)
            if code in limit_codes:
                rows.append({k: str(v) for k, v in row.items()})
    return rows


def latest_candidate_ai_scores(strategy_id: str | None = None) -> dict[str, Any]:
    stored = db_latest_candidate_scores(strategy_id)
    if stored:
        return stored
    out_dir = _resolve(_read_yaml().get("scoring", {}).get("candidate_output_dir", "data/ai_scoring"))
    if strategy_id:
        path = out_dir / f"candidate_ai_scores_latest_{strategy_id}.json"
        if path.exists():
            return _read_json(path)
    return _read_json(out_dir / "candidate_ai_scores_latest.json")


def score_latest_candidates(strategy_id: str | None = None, max_candidates: int | None = None) -> dict[str, Any]:
    cfg = _read_yaml()
    scoring_cfg = cfg.get("scoring", {})
    out_dir = _resolve(scoring_cfg.get("candidate_output_dir", "data/ai_scoring"))
    max_items = int(max_candidates or scoring_cfg.get("max_candidates_per_run", 20))
    candidate_run = _load_candidate_run(strategy_id)
    candidates = list(candidate_run.get("candidates", []))[:max_items]
    sector_scores = latest_sector_scores()
    stock_rows = _load_stocklist_rows({str(item.get("code", "")).zfill(6) for item in candidates})

    messages = [
        {
            "role": "system",
            "content": (
                "你是A股超景气价值投机评分员。必须严格按给定公式评分。不要给投资承诺。"
                "行业景气度为0时，decision必须为avoid。没有证据时不得以常识补全，资料不足时降低分数并列出data_needed。"
                "每个非零维度必须给 source_refs，输出严格JSON。"
            ),
        },
        {
            "role": "user",
            "content": (
                "评分表：正向维度为行业景气度、业务纯度、估值水位、细分行业龙头、市场辨识度，均为0-100。"
                "风险为扣分项，最终分数 = ((五项正向分数求和) - 风险扣分 * 0.2) * 流动性系数 / 5。"
                "流动性系数：全市场成交额<0.8万亿用0.8，>1.5万亿用1.2，否则1.0；无法判断默认1.0。"
                "输出JSON字段：generated_at, pick_date, strategy_id, scores。scores每项包含：code, name, industry, sector_match, "
                "final_score, decision(buy/watch/avoid), dimension_scores(行业景气度/业务纯度/估值水位/细分行业龙头/市场辨识度), "
                "risk_deduction, liquidity_coefficient, risk_events, rationale, source_refs, evidence_gaps, data_needed。\n"
                f"候选股：{json.dumps(candidates, ensure_ascii=False)}\n"
                f"股票列表补充：{json.dumps(stock_rows, ensure_ascii=False)}\n"
                f"赛道评分：{json.dumps(sector_scores, ensure_ascii=False)}"
            ),
        },
    ]
    payload = _client_from_config().chat_json(messages)
    payload.setdefault("generated_at", datetime.now().isoformat(timespec="seconds"))
    payload.setdefault("pick_date", candidate_run.get("pick_date"))
    payload.setdefault("strategy_id", strategy_id or candidate_run.get("meta", {}).get("strategy"))
    payload.setdefault("methodology", "super-boom-v2")
    save_candidate_ai_scores(payload, model=cfg.get("deepseek", {}).get("model"))
    _write_json(out_dir / "candidate_ai_scores_latest.json", payload)
    resolved_strategy = str(payload.get("strategy_id") or strategy_id or "unknown")
    _write_json(out_dir / f"candidate_ai_scores_latest_{resolved_strategy}.json", payload)
    dated = out_dir / f"candidate_ai_scores_{resolved_strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    _write_json(dated, payload)
    return payload
