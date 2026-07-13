from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_scoring.service import (
    latest_candidate_ai_scores,
    latest_sector_scores,
    refresh_sector_scores,
    score_latest_candidates,
)
from ai_scoring.knowledge import (
    ensure_knowledge_fresh,
    knowledge_documents,
    knowledge_status,
    refresh_public_knowledge,
)
from pipeline.cancellation import RunCancelledError
from pipeline.runtime import DATA_MODES, run_pipeline
from pipeline.select_stock import normalize_strategy_config
from strategies.registry import list_strategies
from storage.database import (
    get_current_pipeline_run as db_current_run,
    get_pipeline_run as db_get_run,
    init_db,
    latest_candidate_run as db_latest_candidates,
    load_daily_prices,
    load_stocks,
    mark_interrupted_runs,
    save_research_document,
    delete_research_document,
    upsert_pipeline_run,
)

ROOT = Path(__file__).resolve().parent.parent
FETCH_CONFIG = ROOT / "config" / "fetch_data.yaml"
RULES_CONFIG = ROOT / "config" / "rules_preselect.yaml"
AI_CONFIG = ROOT / "config" / "ai_scoring.yaml"
LATEST_CANDIDATES = ROOT / "data" / "candidates" / "candidates_latest.json"
LATEST_FAILURES = ROOT / "data" / "failures" / "failed_symbols_latest.json"
WEB_DIST = ROOT / "web" / "dist"
API_VERSION = "0.2.0"

logger = logging.getLogger(__name__)

app = FastAPI(title="oversell console API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DataMode = Literal["existing", "incremental", "refresh", "cache-only"]


class ConfigPayload(BaseModel):
    data_mode: DataMode = "incremental"
    fetch: dict[str, Any] = Field(default_factory=dict)
    active_strategy: str = "b1"
    global_: dict[str, Any] = Field(default_factory=dict, alias="global")
    strategies: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RunRequest(BaseModel):
    data_mode: DataMode = "incremental"
    pick_date: str | None = None
    strategy_id: str | None = None
    config: ConfigPayload | None = None


class RunStatus(BaseModel):
    run_id: str
    status: Literal["queued", "running", "cancelling", "success", "failed", "cancelled"]
    stage: str = "等待开始"
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    result: dict[str, Any] | None = None


class CurrentRunResponse(BaseModel):
    run: RunStatus | None = None


class SectorScoreRequest(BaseModel):
    extra_context: str | None = None


class CandidateAIScoreRequest(BaseModel):
    strategy_id: str | None = None
    max_candidates: int | None = None
    web_research: bool = True


class AIScoreJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "failed"]
    stage: str = "等待开始"
    model: str = "deepseek-v4-flash"
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    reasoning: str = ""
    content_preview: str = ""
    logs: list[str] = Field(default_factory=list)
    error: str | None = None
    result: dict[str, Any] | None = None


class ResearchDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    source_url: str | None = Field(default=None, max_length=2_000)
    source_type: str = Field(default="manual", max_length=40)
    captured_at: str | None = None


_runs: dict[str, RunStatus] = {}
_run_cancel_events: dict[str, threading.Event] = {}
_runs_lock = threading.Lock()
_ai_score_jobs: dict[str, AIScoreJobStatus] = {}
_ai_jobs_lock = threading.Lock()


@app.on_event("startup")
def initialize_local_store() -> None:
    init_db()
    mark_interrupted_runs()
    threading.Thread(target=_knowledge_refresh_loop, daemon=True, name="knowledge-refresh").start()


def _knowledge_refresh_loop() -> None:
    while True:
        try:
            ensure_knowledge_fresh()
        except Exception:  # noqa: BLE001
            logger.exception("后台知识库更新失败，继续使用本地资料")
        time.sleep(3600)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


def _ai_model_config() -> dict[str, Any]:
    deepseek = _read_yaml(AI_CONFIG).get("deepseek", {})
    scoring = _read_yaml(AI_CONFIG).get("scoring", {})
    return {
        "model": str(deepseek.get("model", "deepseek-v4-flash")),
        "thinking_mode": True,
        "reasoning_effort": str(deepseek.get("reasoning_effort", "high")),
        "web_search_default": bool(scoring.get("candidate_web_search_enabled", True)),
        "max_search_candidates": int(scoring.get("max_search_candidates", 12)),
    }


def _load_config() -> ConfigPayload:
    fetch = _read_yaml(FETCH_CONFIG).get("data", {})
    rules = normalize_strategy_config(_read_yaml(RULES_CONFIG))
    return ConfigPayload(
        data_mode="incremental",
        fetch=fetch,
        active_strategy=rules.get("active_strategy", "b1"),
        **{
            "global": rules.get("global", {}),
            "strategies": rules.get("strategies", {}),
        },
    )


def _save_config(config: ConfigPayload) -> None:
    fetch_yaml = _read_yaml(FETCH_CONFIG)
    fetch_yaml["data"] = config.fetch
    _write_yaml(FETCH_CONFIG, fetch_yaml)

    rules_yaml = _read_yaml(RULES_CONFIG)
    rules_yaml.pop("b1", None)
    rules_yaml["active_strategy"] = config.active_strategy
    rules_yaml["global"] = config.global_
    rules_yaml["strategies"] = config.strategies
    _write_yaml(RULES_CONFIG, rules_yaml)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _append_run_log(run_id: str, message: str) -> None:
    with _runs_lock:
        status = _runs.get(run_id)
        if status is not None:
            status.logs.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
            if len(status.logs) > 400:
                status.logs = status.logs[-400:]
            upsert_pipeline_run(status.model_dump() if hasattr(status, "model_dump") else status.dict())


def _set_run_status(run_id: str, **updates: Any) -> None:
    with _runs_lock:
        old = _runs[run_id]
        current = old.model_copy(update=updates) if hasattr(old, "model_copy") else old.copy(update=updates)
        _runs[run_id] = current
        upsert_pipeline_run(current.model_dump() if hasattr(current, "model_dump") else current.dict())


def _get_current_run_locked() -> RunStatus | None:
    active = next((run for run in reversed(_runs.values()) if run.status in {"queued", "running", "cancelling"}), None)
    if active is not None:
        return active
    return next(reversed(_runs.values()), None) if _runs else None


class _RunLogHandler(logging.Handler):
    def __init__(self, run_id: str) -> None:
        super().__init__(level=logging.INFO)
        self.run_id = run_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            _append_run_log(self.run_id, message)
        except Exception:
            pass


def _stage_from_log(message: str) -> str | None:
    if "SQLite" in message and ("同步" in message or "读取" in message or "整理" in message):
        return "同步/加载数据库"
    if "拉取" in message or "TUShare" in message or "缓存" in message and "加载" not in message:
        return "数据更新"
    if "加载本地日线数据" in message or "加载缓存数据" in message:
        return "加载本地数据"
    if "流动性过滤" in message:
        return "流动性过滤"
    if "指标预计算" in message:
        return "指标计算"
    if "B1选股进度" in message or "缩量新高进度" in message or "运行策略" in message:
        return "策略筛选"
    if "候选结果已保存" in message or "选股完成" in message:
        return "保存结果"
    return None


def _install_run_log_handler(run_id: str) -> _RunLogHandler:
    handler = _RunLogHandler(run_id)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    class StageFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            stage = _stage_from_log(message)
            if stage:
                _set_run_status(run_id, stage=stage)
            return True

    handler.addFilter(StageFilter())
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(min(root_logger.level or logging.INFO, logging.INFO))
    return handler


def _run_background(run_id: str, request: RunRequest) -> None:
    stop_event = _run_cancel_events.get(run_id)
    _set_run_status(
        run_id,
        status="running",
        stage="准备运行",
        started_at=datetime.now().isoformat(timespec="seconds"),
    )
    handler = _install_run_log_handler(run_id)
    try:
        if request.config is not None:
            _set_run_status(run_id, stage="保存配置")
            _append_run_log(run_id, "保存运行配置")
            _save_config(request.config)

        _set_run_status(run_id, stage="启动任务")
        _append_run_log(run_id, f"启动 pipeline，data_mode={request.data_mode}")
        strategy_id = request.strategy_id or (request.config.active_strategy if request.config else None)
        result = run_pipeline(
            data_mode=request.data_mode,
            pick_date=request.pick_date,
            strategy_id=strategy_id,
            start_from=1,
            no_dashboard=True,
            stop_event=stop_event,
        )
        payload = result.to_dict() if result else None
        _append_run_log(run_id, "运行完成")
        _set_run_status(
            run_id,
            status="success",
            stage="运行完成",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            result=payload,
        )
    except RunCancelledError as exc:
        _append_run_log(run_id, f"任务已终止：{exc}")
        _set_run_status(
            run_id,
            status="cancelled",
            stage="已终止",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("run %s failed", run_id)
        _append_run_log(run_id, f"运行失败：{exc}")
        _set_run_status(
            run_id,
            status="failed",
            stage="运行失败",
            finished_at=datetime.now().isoformat(timespec="seconds"),
            error=str(exc),
        )
    finally:
        logging.getLogger().removeHandler(handler)
        _run_cancel_events.pop(run_id, None)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "oversell", "version": API_VERSION}


@app.get("/api/config", response_model=ConfigPayload)
def get_config() -> ConfigPayload:
    return _load_config()


@app.put("/api/config", response_model=ConfigPayload)
def put_config(config: ConfigPayload) -> ConfigPayload:
    _save_config(config)
    return config


@app.get("/api/strategies")
def get_strategies() -> dict[str, Any]:
    return {
        "strategies": [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "default_config": item.default_config,
            }
            for item in list_strategies()
        ]
    }


@app.post("/api/runs", response_model=RunStatus)
def create_run(request: RunRequest) -> RunStatus:
    if request.data_mode not in DATA_MODES:
        raise HTTPException(status_code=400, detail="invalid data_mode")

    with _runs_lock:
        active = next((run for run in _runs.values() if run.status in {"queued", "running", "cancelling"}), None)
    if active is not None:
        raise HTTPException(status_code=409, detail=f"已有任务正在运行: {active.run_id}")

    run_id = uuid.uuid4().hex[:12]
    status = RunStatus(run_id=run_id, status="queued")
    with _runs_lock:
        _runs[run_id] = status
        _run_cancel_events[run_id] = threading.Event()
        upsert_pipeline_run(
            status.model_dump() if hasattr(status, "model_dump") else status.dict(),
            request.model_dump(by_alias=True) if hasattr(request, "model_dump") else request.dict(by_alias=True),
        )

    thread = threading.Thread(target=_run_background, args=(run_id, request), daemon=True)
    thread.start()
    return status


@app.post("/api/runs/{run_id}/cancel", response_model=RunStatus)
def cancel_run(run_id: str) -> RunStatus:
    with _runs_lock:
        status = _runs.get(run_id)
        stop_event = _run_cancel_events.get(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="run not found")
    if status.status in {"success", "failed", "cancelled"}:
        return status
    if stop_event is None:
        raise HTTPException(status_code=409, detail="run cannot be cancelled")

    stop_event.set()
    _append_run_log(run_id, "收到终止请求，等待当前步骤安全退出")
    _set_run_status(run_id, status="cancelling", stage="正在终止")
    with _runs_lock:
        return _runs[run_id]


@app.get("/api/runs/current", response_model=CurrentRunResponse)
def get_current_run() -> CurrentRunResponse:
    with _runs_lock:
        status = _get_current_run_locked()
    if status is None:
        stored = db_current_run()
        status = RunStatus(**stored) if stored else None
    return CurrentRunResponse(run=status)


@app.get("/api/runs/{run_id}", response_model=RunStatus)
def get_run(run_id: str) -> RunStatus:
    with _runs_lock:
        status = _runs.get(run_id)
    if status is None:
        stored = db_get_run(run_id)
        status = RunStatus(**stored) if stored else None
    if status is None:
        raise HTTPException(status_code=404, detail="run not found")
    return status


@app.get("/api/candidates/latest")
def get_latest_candidates(strategy_id: str | None = None) -> dict[str, Any]:
    stored = db_latest_candidates(strategy_id)
    if stored:
        return stored
    if strategy_id:
        path = ROOT / "data" / "candidates" / f"candidates_latest_{strategy_id}.json"
        if path.exists():
            return _read_json(path)
    return _read_json(LATEST_CANDIDATES)


@app.post("/api/backtests", status_code=501)
def create_backtest() -> dict[str, Any]:
    raise HTTPException(status_code=501, detail="回测接口已预留，交易回测逻辑尚未实现")


@app.get("/api/backtests/{backtest_id}", status_code=501)
def get_backtest(backtest_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail=f"回测接口已预留，尚未实现: {backtest_id}")


@app.get("/api/failures/latest")
def get_latest_failures() -> dict[str, Any]:
    if not LATEST_FAILURES.exists():
        return {
            "generated_at": None,
            "total_symbols": 0,
            "failed_count": 0,
            "empty_count": 0,
            "failed_symbols": [],
            "empty_symbols": [],
        }
    return _read_json(LATEST_FAILURES)


@app.get("/api/stocks")
def get_stocks() -> dict[str, Any]:
    database_rows = load_stocks()
    if not database_rows.empty:
        return {"stocks": database_rows.fillna("").to_dict(orient="records")}
    path = ROOT / "data" / "stocklist.csv"
    if not path.exists():
        return {"stocks": []}
    df = pd.read_csv(path, dtype={"代码": str, "ts_code": str})
    records = df.fillna("").to_dict(orient="records")
    return {"stocks": records}


@app.get("/api/stocks/{code}/kline")
def get_stock_kline(code: str, adjust: str = "qfq", limit: int = 180) -> dict[str, Any]:
    safe_code = str(code).zfill(6)
    database_data = load_daily_prices(adjust, 1, [safe_code])
    if database_data.get(safe_code) is not None:
        df = database_data[safe_code].copy()
        df.index.name = "date"
        df = df.reset_index()
        keep = [c for c in ["date", "open", "close", "high", "low", "volume", "amount"] if c in df.columns]
        df = df[keep].tail(max(1, min(limit, 1000))).copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        return {"code": safe_code, "rows": df.fillna(0).to_dict(orient="records")}
    path = ROOT / "data" / "raw" / f"{safe_code}_{adjust}.csv"
    if not path.exists():
        raise HTTPException(status_code=404, detail="kline file not found")

    df = pd.read_csv(path)
    df.columns = [str(c).lower() for c in df.columns]
    rename = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    df = df.rename(columns=rename)
    if "date" not in df.columns:
        df = df.reset_index().rename(columns={"index": "date"})
    keep = [c for c in ["date", "open", "close", "high", "low", "volume", "amount"] if c in df.columns]
    df = df[keep].tail(max(1, min(limit, 1000))).copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.fillna(0)
    return {"code": safe_code, "rows": df.to_dict(orient="records")}


@app.get("/api/ai/sector-scores/latest")
def get_latest_sector_scores() -> dict[str, Any]:
    payload = latest_sector_scores()
    if not payload:
        return {"generated_at": None, "sectors": [], "source_count": 0}
    return payload


@app.post("/api/ai/sector-scores/refresh")
def post_refresh_sector_scores(request: SectorScoreRequest) -> dict[str, Any]:
    try:
        return refresh_sector_scores(extra_context=request.extra_context)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI sector scoring failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/ai/candidate-scores/latest")
def get_latest_candidate_scores(strategy_id: str | None = None) -> dict[str, Any]:
    payload = latest_candidate_ai_scores(strategy_id=strategy_id)
    if not payload:
        return {"generated_at": None, "scores": []}
    return payload


def _ai_job_dump(job: AIScoreJobStatus) -> dict[str, Any]:
    return job.model_dump() if hasattr(job, "model_dump") else job.dict()


def _get_ai_job(job_id: str) -> AIScoreJobStatus | None:
    with _ai_jobs_lock:
        job = _ai_score_jobs.get(job_id)
        return AIScoreJobStatus(**_ai_job_dump(job)) if job else None


def _append_ai_job_progress(job_id: str, message: str) -> None:
    with _ai_jobs_lock:
        job = _ai_score_jobs.get(job_id)
        if not job:
            return
        job.stage = message
        job.logs.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
        job.logs = job.logs[-200:]


def _append_ai_stream(job_id: str, kind: str, text: str) -> None:
    with _ai_jobs_lock:
        job = _ai_score_jobs.get(job_id)
        if not job:
            return
        if kind == "reasoning":
            job.stage = "DeepSeek 正在思考"
            job.reasoning = (job.reasoning + text)[-160_000:]
        else:
            job.stage = "正在生成结构化评分"
            job.content_preview = (job.content_preview + text)[-160_000:]


def _run_ai_score_job(job_id: str, score_request: CandidateAIScoreRequest) -> None:
    with _ai_jobs_lock:
        job = _ai_score_jobs[job_id]
        job.status = "running"
        job.stage = "准备候选资料"
        job.started_at = datetime.now().isoformat(timespec="seconds")
        job.logs.append(f"{datetime.now().strftime('%H:%M:%S')} 开始候选股 AI 评分")
    try:
        result = score_latest_candidates(
            strategy_id=score_request.strategy_id,
            max_candidates=score_request.max_candidates,
            web_research=score_request.web_research,
            stream_callback=lambda kind, text: _append_ai_stream(job_id, kind, text),
            progress_callback=lambda message: _append_ai_job_progress(job_id, message),
        )
        with _ai_jobs_lock:
            job = _ai_score_jobs[job_id]
            job.status = "success"
            job.stage = "评分完成"
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.result = result
            job.logs.append(f"{datetime.now().strftime('%H:%M:%S')} 评分完成")
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI score job %s failed", job_id)
        with _ai_jobs_lock:
            job = _ai_score_jobs[job_id]
            job.status = "failed"
            job.stage = "评分失败"
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.error = str(exc)
            job.logs.append(f"{datetime.now().strftime('%H:%M:%S')} 评分失败：{exc}")


@app.get("/api/ai/model")
def get_ai_model() -> dict[str, Any]:
    return _ai_model_config()


@app.post("/api/ai/candidate-scores/jobs", response_model=AIScoreJobStatus)
def create_candidate_score_job(request: CandidateAIScoreRequest) -> AIScoreJobStatus:
    with _ai_jobs_lock:
        active = next((item for item in _ai_score_jobs.values() if item.status in {"queued", "running"}), None)
        if active:
            raise HTTPException(status_code=409, detail=f"已有 AI 评分任务正在运行: {active.job_id}")
        job_id = uuid.uuid4().hex[:12]
        job = AIScoreJobStatus(
            job_id=job_id,
            status="queued",
            model=_ai_model_config()["model"],
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        _ai_score_jobs[job_id] = job
    threading.Thread(target=_run_ai_score_job, args=(job_id, request), daemon=True, name=f"ai-score-{job_id}").start()
    return job


@app.get("/api/ai/candidate-scores/jobs/current")
def get_current_candidate_score_job() -> dict[str, Any]:
    with _ai_jobs_lock:
        active = next(
            (item for item in reversed(_ai_score_jobs.values()) if item.status in {"queued", "running"}),
            None,
        )
        current = active or (next(reversed(_ai_score_jobs.values()), None) if _ai_score_jobs else None)
        return {"job": _ai_job_dump(current) if current else None}


@app.get("/api/ai/candidate-scores/jobs/{job_id}", response_model=AIScoreJobStatus)
def get_candidate_score_job(job_id: str) -> AIScoreJobStatus:
    job = _get_ai_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="AI score job not found")
    return job


@app.get("/api/ai/candidate-scores/jobs/{job_id}/events")
def stream_candidate_score_job(job_id: str) -> StreamingResponse:
    if not _get_ai_job(job_id):
        raise HTTPException(status_code=404, detail="AI score job not found")

    def events():
        last_payload = ""
        while True:
            job = _get_ai_job(job_id)
            if not job:
                break
            payload = json.dumps(_ai_job_dump(job), ensure_ascii=False, default=str)
            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            if job.status in {"success", "failed"}:
                break
            time.sleep(0.25)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ai/candidate-scores/score")
def post_score_candidates(request: CandidateAIScoreRequest) -> dict[str, Any]:
    try:
        return score_latest_candidates(
            strategy_id=request.strategy_id,
            max_candidates=request.max_candidates,
            web_research=request.web_research,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI candidate scoring failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/knowledge/benben/status")
def get_benben_knowledge_status() -> dict[str, Any]:
    return knowledge_status()


@app.get("/api/knowledge/benben/documents")
def get_benben_knowledge_documents(limit: int = 100) -> dict[str, Any]:
    return {"documents": knowledge_documents(limit=max(1, min(limit, 300)))}


@app.post("/api/knowledge/benben/refresh")
def post_refresh_benben_knowledge() -> dict[str, Any]:
    try:
        return refresh_public_knowledge(force=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("知识库更新失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/research/documents")
def get_research_documents(limit: int = 30) -> dict[str, Any]:
    from storage.database import list_research_documents

    return {"documents": list_research_documents(limit)}


@app.post("/api/research/documents")
def post_research_document(request: ResearchDocumentRequest) -> dict[str, Any]:
    """Store a licensed excerpt or the user's own summary as AI-score evidence."""
    return save_research_document(request.model_dump())


@app.delete("/api/research/documents/{document_id}")
def remove_research_document(document_id: int) -> dict[str, bool]:
    return {"deleted": delete_research_document(document_id)}


if WEB_DIST.exists():
    assets_dir = WEB_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        target = WEB_DIST / full_path
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
