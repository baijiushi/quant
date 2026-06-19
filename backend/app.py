from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai_scoring.service import (
    latest_candidate_ai_scores,
    latest_sector_scores,
    refresh_sector_scores,
    score_latest_candidates,
)
from pipeline.cancellation import RunCancelledError
from pipeline.runtime import DATA_MODES, run_pipeline
from pipeline.select_stock import normalize_strategy_config
from strategies.registry import list_strategies

ROOT = Path(__file__).resolve().parent.parent
FETCH_CONFIG = ROOT / "config" / "fetch_data.yaml"
RULES_CONFIG = ROOT / "config" / "rules_preselect.yaml"
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


_runs: dict[str, RunStatus] = {}
_run_cancel_events: dict[str, threading.Event] = {}
_runs_lock = threading.Lock()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)


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


def _set_run_status(run_id: str, **updates: Any) -> None:
    with _runs_lock:
        old = _runs[run_id]
        current = old.model_copy(update=updates) if hasattr(old, "model_copy") else old.copy(update=updates)
        _runs[run_id] = current


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
    if "拉取" in message or "TUShare" in message or "缓存" in message and "加载" not in message:
        return "数据更新"
    if "加载本地日线数据" in message or "加载缓存数据" in message:
        return "加载本地数据"
    if "流动性过滤" in message:
        return "流动性过滤"
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
    return CurrentRunResponse(run=status)


@app.get("/api/runs/{run_id}", response_model=RunStatus)
def get_run(run_id: str) -> RunStatus:
    with _runs_lock:
        status = _runs.get(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="run not found")
    return status


@app.get("/api/candidates/latest")
def get_latest_candidates(strategy_id: str | None = None) -> dict[str, Any]:
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
    path = ROOT / "data" / "stocklist.csv"
    if not path.exists():
        return {"stocks": []}
    df = pd.read_csv(path, dtype={"代码": str, "ts_code": str})
    records = df.fillna("").to_dict(orient="records")
    return {"stocks": records}


@app.get("/api/stocks/{code}/kline")
def get_stock_kline(code: str, adjust: str = "qfq", limit: int = 180) -> dict[str, Any]:
    safe_code = str(code).zfill(6)
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


@app.post("/api/ai/candidate-scores/score")
def post_score_candidates(request: CandidateAIScoreRequest) -> dict[str, Any]:
    try:
        return score_latest_candidates(strategy_id=request.strategy_id, max_candidates=request.max_candidates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI candidate scoring failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
