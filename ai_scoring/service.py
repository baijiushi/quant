from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib import request

import yaml

from ai_scoring.client import DeepSeekClient
from ai_scoring.knowledge import (
    build_methodology_context,
    ensure_knowledge_fresh,
    knowledge_documents,
)
from storage.database import (
    latest_candidate_ai_scores as db_latest_candidate_scores,
    latest_candidate_run,
    latest_sector_scores as db_latest_sector_scores,
    list_research_documents,
    save_candidate_ai_scores,
    save_sector_scores,
    load_daily_prices,
    market_turnover_snapshot,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config" / "ai_scoring.yaml"
LATEST_CANDIDATES = ROOT / "data" / "candidates" / "candidates_latest.json"
METHODOLOGY_VERSION = "benben-super-boom-v1"
POSITIVE_DIMENSIONS = ("行业景气度", "业务纯度", "估值水位", "细分行业龙头", "市场辨识度")


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

    for item in knowledge_documents(limit=100):
        documents.append(
            {
                "source": item.get("source_url") or f"knowledge:{item.get('source_key')}",
                "source_ref": str(item.get("source_key")),
                "evidence_level": str(item.get("evidence_level")),
                "published_at": str(item.get("published_at") or ""),
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
        reasoning_effort=str(cfg.get("reasoning_effort", "high")),
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
    knowledge_refresh = ensure_knowledge_fresh()
    documents = collect_sector_sources()
    source_blob = json.dumps(documents, ensure_ascii=False)[: int(cfg.get("scoring", {}).get("max_source_chars", 24000))]
    methodology = build_methodology_context()

    messages = [
        {
            "role": "system",
            "content": (
                "你是A股超景气赛道研究员。严格执行下方方法论与证据等级。"
                "只在政策拐点、技术突破、订单爆发、供需反转或现象级事件有可核验材料时给高分。"
                "不得把视频标题、二手笔记或持仓推断写成创作者原话，不得编造新闻、机构观点或公司关系。"
                "目录标题只能发现线索，不能单独支撑50分及以上结论。证据不足时 score 必须低于50。"
                "所有判断写 source_refs、evidence_gaps 和可证伪条件，输出严格JSON。\n\n"
                f"{methodology}"
            ),
        },
        {
            "role": "user",
            "content": (
                "请按超景气价值投机模型，识别当前或即将超景气的A股赛道。"
                "重点关注政策拐点、技术重大突破、突发订单、行业反转、现象级事件。"
                "输出JSON字段：generated_at, sectors。sectors数组每项包含：sector, score(0-100), "
                "opportunity_type, event_strength, commercialization_stage, supply_demand_impact, "
                "time_horizon, catalysts, evidence, source_refs, risk_notes, evidence_gaps, "
                "invalidation_triggers, evidence_grade(direct/user_primary/secondary/catalog/mixed), confidence(0-1)。\n"
                f"额外背景：{extra_context or ''}\n"
                f"资料：{source_blob}"
            ),
        },
    ]
    payload = _client_from_config().chat_json(messages)
    payload.setdefault("generated_at", datetime.now().isoformat(timespec="seconds"))
    payload.setdefault("source_count", len(documents))
    payload["methodology"] = METHODOLOGY_VERSION
    payload["knowledge_status"] = knowledge_refresh
    _normalize_sector_scores(payload)
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


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bounded(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, _number(value)))


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if value is not None and str(value).strip() else []


def _validated_source_refs(value: Any, allowed_urls: set[str] | None = None) -> list[str]:
    refs = _string_list(value)
    if allowed_urls is None:
        return refs
    return [ref for ref in refs if not ref.lower().startswith(("http://", "https://")) or ref in allowed_urls]


def _compact_dimension_review(text: str, max_chars: int = 100) -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    sentence_ends = [index + 1 for index, char in enumerate(cleaned[:max_chars]) if char in "。；！？"]
    valid_ends = [index for index in sentence_ends if index >= 75]
    if valid_ends:
        return cleaned[: valid_ends[-1]]
    return cleaned[: max_chars - 1].rstrip("，、；：") + "…"


def _valuation_position_score(gain_from_low_pct: Any) -> float | None:
    if gain_from_low_pct is None:
        return None
    gain = _number(gain_from_low_pct)
    return round(100.0 if gain <= 20 else max(0.0, 100.0 - (gain - 20.0)), 2)


def _clean_model_rationale(text: Any) -> str:
    original = str(text or "").strip()
    sentences = re.split(r"(?<=[。！？])", original)
    kept = [sentence for sentence in sentences if not any(key in sentence for key in ("综合评分", "最终分"))]
    cleaned = "".join(kept).strip()
    return cleaned or original


def _normalize_sector_scores(payload: dict[str, Any]) -> None:
    for item in payload.get("sectors", []) or []:
        refs = _string_list(item.get("source_refs"))
        score = _bounded(item.get("score"))
        if not refs:
            score = min(score, 49.0)
            gaps = list(item.get("evidence_gaps", []) or [])
            gaps.append("缺少可追溯 source_refs")
            item["evidence_gaps"] = list(dict.fromkeys(str(gap) for gap in gaps))
        item["score"] = round(score, 2)
        item["confidence"] = round(_bounded(item.get("confidence"), 0.0, 1.0), 3)


def _normalize_candidate_scores(
    payload: dict[str, Any],
    candidates: list[dict[str, Any]],
    liquidity_coefficient: float | None = None,
    allowed_source_urls: set[str] | None = None,
    market_facts_by_code: dict[str, dict[str, Any]] | None = None,
) -> None:
    candidates_by_code = {str(item.get("code", "")).zfill(6): item for item in candidates}
    for item in payload.get("scores", []) or []:
        code = str(item.get("code", "")).zfill(6)
        item["code"] = code
        source_candidate = candidates_by_code.get(code, {})
        item.setdefault("name", source_candidate.get("name", ""))
        dimensions = item.get("dimension_scores") if isinstance(item.get("dimension_scores"), dict) else {}
        normalized_dimensions = {key: round(_bounded(dimensions.get(key)), 2) for key in POSITIVE_DIMENSIONS}
        market_fact = (market_facts_by_code or {}).get(code, {})
        valuation_score = _valuation_position_score(market_fact.get("gain_from_three_year_low_pct"))
        if valuation_score is not None:
            normalized_dimensions["估值水位"] = valuation_score
        item["dimension_scores"] = normalized_dimensions
        raw_reviews = item.get("dimension_reviews") if isinstance(item.get("dimension_reviews"), dict) else {}
        normalized_reviews: dict[str, dict[str, Any]] = {}
        for key in POSITIVE_DIMENSIONS:
            raw_review = raw_reviews.get(key, {})
            if isinstance(raw_review, dict):
                comment = _compact_dimension_review(str(raw_review.get("comment") or ""))
                refs = _validated_source_refs(raw_review.get("source_refs"), allowed_source_urls)
            else:
                comment = _compact_dimension_review(str(raw_review or ""))
                refs = []
            normalized_reviews[key] = {
                "comment": comment,
                "source_refs": refs,
                "length": len(comment),
            }
        item["dimension_reviews"] = normalized_reviews
        risk = round(_bounded(item.get("risk_deduction")), 2)
        liquidity = _number(liquidity_coefficient, 1.0) if liquidity_coefficient is not None else _number(
            item.get("liquidity_coefficient"), 1.0
        )
        if liquidity not in {0.8, 1.0, 1.2}:
            liquidity = 1.0
        raw_final = item.get("final_score")
        calculated = ((sum(normalized_dimensions.values()) - risk * 0.2) * liquidity) / 5
        calculated = round(max(0.0, min(120.0, calculated)), 2)
        veto = bool(item.get("veto")) or normalized_dimensions["行业景气度"] <= 0
        decision = "avoid" if veto or calculated < 60 else "watch" if calculated < 80 else "buy"
        item["model_final_score"] = raw_final
        item["final_score"] = calculated
        item["risk_deduction"] = risk
        item["liquidity_coefficient"] = liquidity
        item["decision"] = decision
        original_rationale = str(item.get("rationale") or "").strip()
        cleaned_rationale = _clean_model_rationale(original_rationale)
        if cleaned_rationale != original_rationale:
            item["model_rationale"] = original_rationale
            item["rationale"] = cleaned_rationale
        item["calculation"] = {
            "positive_sum": round(sum(normalized_dimensions.values()), 2),
            "risk_weight": 0.2,
            "formula": "((positive_sum - risk_deduction * 0.2) * liquidity_coefficient) / 5",
            "calculated_by": "oversell-python",
            "valuation_rule": "三年低点涨幅<=20%得100分；超过20%后每增加1个百分点扣1分，最低0分",
        }
        item["decision_note"] = (
            "行业景气度为0或存在一票否决项" if veto else "80分以上重点研究，60-79.99观察，低于60回避；非自动交易指令"
        )
        refs = _validated_source_refs(item.get("source_refs"), allowed_source_urls)
        item["source_refs"] = refs
        confidence = _bounded(item.get("confidence"), 0.0, 1.0)
        item["confidence"] = round(min(confidence, 0.45) if not refs else confidence, 3)


def _candidate_market_facts(
    candidates: list[dict[str, Any]],
    adjust: str,
) -> list[dict[str, Any]]:
    codes = [str(item.get("code", "")).zfill(6) for item in candidates]
    frames = load_daily_prices(adjust, 1, codes) if codes else {}
    facts: list[dict[str, Any]] = []
    for candidate in candidates:
        code = str(candidate.get("code", "")).zfill(6)
        frame = frames.get(code)
        fact: dict[str, Any] = {"code": code, "name": candidate.get("name", "")}
        if frame is not None and not frame.empty and "close" in frame.columns:
            valid = frame.dropna(subset=["close"]).sort_index()
            if not valid.empty:
                latest_date = valid.index.max()
                recent = valid.loc[valid.index >= latest_date - timedelta(days=1095)]
                current_close = float(valid.iloc[-1]["close"])
                low_3y = float(recent["close"].min()) if not recent.empty else current_close
                high_3y = float(recent["close"].max()) if not recent.empty else current_close
                fact.update(
                    {
                        "latest_date": latest_date.strftime("%Y-%m-%d"),
                        "current_close": round(current_close, 4),
                        "three_year_low_close": round(low_3y, 4),
                        "three_year_high_close": round(high_3y, 4),
                        "gain_from_three_year_low_pct": round((current_close / low_3y - 1) * 100, 2) if low_3y else None,
                    }
                )
                fact["valuation_position_score"] = _valuation_position_score(fact.get("gain_from_three_year_low_pct"))
        facts.append(fact)
    return facts


def _collect_candidate_web_research(
    client: DeepSeekClient,
    candidates: list[dict[str, Any]],
    market_facts: list[dict[str, Any]],
    pick_date: str | None,
    scoring_cfg: dict[str, Any],
    progress_callback: Callable[[str], None] | None,
) -> dict[str, Any]:
    max_candidates = max(1, int(scoring_cfg.get("max_search_candidates", 12)))
    selected = candidates[:max_candidates]
    if not selected:
        return {"enabled": True, "summary": "", "sources": [], "searched_codes": []}
    if progress_callback:
        progress_callback(f"联网检索 {len(selected)} 只候选的公司、行业与风险资料")
    selected_codes = {str(item.get("code", "")).zfill(6) for item in selected}
    selected_facts = [item for item in market_facts if item.get("code") in selected_codes]
    prompt = (
        f"截至 {datetime.now().strftime('%Y-%m-%d')}，请联网检索下列A股候选，选股日为 {pick_date or '未指定'}。\n"
        "每只股票重点核验：1. 最新主营收入和利润构成、业务纯度；2. 所属行业近期供需、政策、技术、订单催化；"
        "3. 国内或全球细分市场地位和主要竞争对手；4. 市场辨识度与稀缺性；5. 定增、解禁、减持、诉讼、"
        "制裁、审计、事故、监管等风险。优先公司公告、交易所、政府、年报和权威财经来源。"
        "不要仅凭搜索摘要下结论；不确定就明确缺口。按股票代码分节，给出事实日期和URL。\n"
        f"候选：{json.dumps(selected, ensure_ascii=False)}\n"
        f"本地价格事实：{json.dumps(selected_facts, ensure_ascii=False)}"
    )
    try:
        result = client.web_search(
            prompt,
            max_uses=int(scoring_cfg.get("web_search_max_uses", 18)),
            max_tokens=min(12000, max(3000, int(scoring_cfg.get("web_search_max_tokens", 8000)))),
        )
        result["enabled"] = True
        result["searched_codes"] = sorted(selected_codes)
        if progress_callback:
            progress_callback(f"联网资料完成，获得 {len(result.get('sources', []))} 个可追溯来源")
        return result
    except Exception as exc:  # noqa: BLE001
        if progress_callback:
            progress_callback(f"联网检索失败，改用本地资料继续评分：{exc}")
        return {
            "enabled": True,
            "searched_codes": sorted(selected_codes),
            "summary": "",
            "sources": [],
            "error": str(exc),
        }


def score_latest_candidates(
    strategy_id: str | None = None,
    max_candidates: int | None = None,
    web_research: bool | None = None,
    stream_callback: Callable[[str, str], None] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    cfg = _read_yaml()
    scoring_cfg = cfg.get("scoring", {})
    out_dir = _resolve(scoring_cfg.get("candidate_output_dir", "data/ai_scoring"))
    max_items = int(max_candidates or scoring_cfg.get("max_candidates_per_run", 20))
    candidate_run = _load_candidate_run(strategy_id)
    candidates = list(candidate_run.get("candidates", []))[:max_items]
    if not candidates:
        raise ValueError("当前策略没有可评分候选，请先完成选股任务。")
    sector_scores = latest_sector_scores()
    sector_context = dict(sector_scores)
    sector_context.pop("sources", None)
    stock_rows = _load_stocklist_rows({str(item.get("code", "")).zfill(6) for item in candidates})
    methodology = build_methodology_context()
    adjust = str(candidate_run.get("meta", {}).get("adjust") or "qfq")
    if progress_callback:
        progress_callback("读取候选股三年价格位置和全市场流动性")
    market_facts = _candidate_market_facts(candidates, adjust)
    liquidity = market_turnover_snapshot(adjust, candidate_run.get("pick_date"))
    client = _client_from_config()
    should_research = scoring_cfg.get("candidate_web_search_enabled", True) if web_research is None else web_research
    research = (
        _collect_candidate_web_research(client, candidates, market_facts, candidate_run.get("pick_date"), scoring_cfg, progress_callback)
        if should_research
        else {"enabled": False, "summary": "", "sources": [], "searched_codes": []}
    )
    research_for_prompt = {
        **research,
        "summary": str(research.get("summary") or "")[:40_000],
        "reasoning_content": "",
    }

    messages = [
        {
            "role": "system",
            "content": (
                "你是A股超景气价值投机评分员。你负责维度判断和证据说明，最终算术由程序复算。不要给投资承诺。"
                "行业景气度为0时，decision必须为avoid。没有证据时不得以常识补全，资料不足时降低分数并列出data_needed。"
                "公开公司公告、交易所、政府、产业数据和权威财经报道都可作为主要证据，不要求结论来自特定作者或用户。"
                "估值水位是正向的价格安全边际分，越接近三年低点分数越高；程序会覆盖模型分值，不得把高价解释为高分。"
                "每个非零维度必须给 source_refs；标题目录不能单独支撑高分；推断必须标注。"
                "联网资料有多条独立可靠来源且相互印证时，confidence可以达到0.8-0.95；单一或间接来源要降低。输出严格JSON。\n\n"
                f"{methodology}"
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
                "dimension_reviews（五个同名维度，每项包含comment和source_refs；comment必须分别写75-100个汉字，说明事实、判断和不足）, "
                "risk_deduction, liquidity_coefficient, veto, risk_events, rationale, source_refs, evidence_gaps, data_needed, "
                "thesis_transmission, invalidation_triggers, confidence(0-1), evidence_grade。rationale写120-180字综合结论。\n"
                "rationale只讨论事实、逻辑和风险，不重复最终分、不写买入卖出建议，程序会另行计算分数与研究标签。\n"
                f"候选股：{json.dumps(candidates, ensure_ascii=False)}\n"
                f"股票列表补充：{json.dumps(stock_rows, ensure_ascii=False)}\n"
                f"本地三年价格事实：{json.dumps(market_facts, ensure_ascii=False)}\n"
                f"全市场流动性（程序将强制采用此系数）：{json.dumps(liquidity, ensure_ascii=False)}\n"
                f"联网检索资料：{json.dumps(research_for_prompt, ensure_ascii=False)}\n"
                f"赛道评分：{json.dumps(sector_context, ensure_ascii=False)}"
            ),
        },
    ]
    if progress_callback:
        progress_callback(f"调用 {client.model} 思考模式生成五维评分")
    payload, stream_meta = client.chat_json_stream(messages, on_chunk=stream_callback)
    payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["pick_date"] = candidate_run.get("pick_date")
    payload["strategy_id"] = strategy_id or candidate_run.get("meta", {}).get("strategy") or "unknown"
    payload["methodology"] = METHODOLOGY_VERSION
    payload["model"] = stream_meta.get("model") or client.model
    payload["thinking_mode"] = True
    payload["reasoning_content"] = stream_meta.get("reasoning_content", "")
    payload["usage"] = stream_meta.get("usage", {})
    payload["web_research"] = research
    payload["market_liquidity"] = liquidity
    allowed_source_urls = {
        str(item.get("url")) for item in research.get("sources", []) or [] if str(item.get("url") or "").strip()
    }
    _normalize_candidate_scores(
        payload,
        candidates,
        liquidity_coefficient=float(liquidity.get("coefficient", 1.0)),
        allowed_source_urls=allowed_source_urls,
        market_facts_by_code={str(item.get("code")): item for item in market_facts},
    )
    if progress_callback:
        progress_callback("保存结构化评分和证据明细")
    save_candidate_ai_scores(payload, model=cfg.get("deepseek", {}).get("model"))
    _write_json(out_dir / "candidate_ai_scores_latest.json", payload)
    resolved_strategy = str(payload.get("strategy_id") or strategy_id or "unknown")
    _write_json(out_dir / f"candidate_ai_scores_latest_{resolved_strategy}.json", payload)
    dated = out_dir / f"candidate_ai_scores_{resolved_strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    _write_json(dated, payload)
    return payload
