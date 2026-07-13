"""Small, dependency-free SQLite store used by the local console.

CSV remains the market-data cache and YAML remains user-editable configuration.
SQLite stores generated records that need history, querying and restart recovery.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "oversell.db"
logger = logging.getLogger(__name__)


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _decode(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                error TEXT,
                logs_json TEXT NOT NULL DEFAULT '[]',
                result_json TEXT,
                request_json TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_updated ON pipeline_runs(updated_at DESC);

            CREATE TABLE IF NOT EXISTS candidate_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                pick_date TEXT NOT NULL,
                run_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                meta_json TEXT NOT NULL,
                UNIQUE(strategy_id, pick_date, run_date)
            );
            CREATE INDEX IF NOT EXISTS idx_candidate_runs_latest ON candidate_runs(strategy_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_run_id INTEGER NOT NULL REFERENCES candidate_runs(id) ON DELETE CASCADE,
                rank_no INTEGER NOT NULL,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                candidate_date TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                close REAL NOT NULL,
                turnover_n REAL NOT NULL,
                score REAL NOT NULL,
                extra_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_candidates_run ON candidates(candidate_run_id, rank_no);

            CREATE TABLE IF NOT EXISTS research_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_url TEXT,
                source_type TEXT NOT NULL DEFAULT 'manual',
                captured_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_research_documents_created ON research_documents(created_at DESC);

            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id TEXT NOT NULL,
                source_key TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source_url TEXT,
                source_type TEXT NOT NULL,
                evidence_level TEXT NOT NULL DEFAULT 'secondary',
                published_at TEXT,
                captured_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(knowledge_id, source_key)
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_documents_latest
                ON knowledge_documents(knowledge_id, active, published_at DESC, updated_at DESC);

            CREATE TABLE IF NOT EXISTS sector_score_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                model TEXT,
                methodology_version TEXT NOT NULL,
                source_count INTEGER NOT NULL DEFAULT 0,
                sources_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sector_score_runs_latest ON sector_score_runs(generated_at DESC);

            CREATE TABLE IF NOT EXISTS candidate_ai_score_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT NOT NULL,
                strategy_id TEXT,
                pick_date TEXT,
                model TEXT,
                methodology_version TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_candidate_ai_runs_latest ON candidate_ai_score_runs(strategy_id, generated_at DESC);

            CREATE TABLE IF NOT EXISTS stocks (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ts_code TEXT,
                market TEXT,
                list_date TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_prices (
                code TEXT NOT NULL,
                adjust TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0,
                amount REAL NOT NULL DEFAULT 0,
                pct_chg REAL NOT NULL DEFAULT 0,
                change_value REAL NOT NULL DEFAULT 0,
                turnover REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(code, adjust, trade_date)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_prices_lookup ON daily_prices(adjust, code, trade_date);
            """
        )


def upsert_pipeline_run(payload: dict[str, Any], request_payload: dict[str, Any] | None = None) -> None:
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs(run_id, status, stage, started_at, finished_at, error, logs_json, result_json, request_json, updated_at)
            VALUES(:run_id, :status, :stage, :started_at, :finished_at, :error, :logs_json, :result_json, :request_json, :updated_at)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status, stage=excluded.stage, started_at=excluded.started_at,
                finished_at=excluded.finished_at, error=excluded.error, logs_json=excluded.logs_json,
                result_json=excluded.result_json,
                request_json=COALESCE(excluded.request_json, pipeline_runs.request_json), updated_at=excluded.updated_at
            """,
            {
                "run_id": payload["run_id"],
                "status": payload.get("status", "queued"),
                "stage": payload.get("stage", "等待开始"),
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "error": payload.get("error"),
                "logs_json": _json(payload.get("logs", [])),
                "result_json": _json(payload.get("result")) if payload.get("result") is not None else None,
                "request_json": _json(request_payload) if request_payload is not None else None,
                "updated_at": now,
            },
        )


def get_pipeline_run(run_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,)).fetchone()
    return _pipeline_row(row) if row else None


def get_current_pipeline_run() -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM pipeline_runs
               ORDER BY CASE WHEN status IN ('queued','running','cancelling') THEN 0 ELSE 1 END, updated_at DESC
               LIMIT 1"""
        ).fetchone()
    return _pipeline_row(row) if row else None


def mark_interrupted_runs() -> None:
    """A server restart cannot resume a Python thread; mark stale active rows honestly."""
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """UPDATE pipeline_runs SET status='failed', stage='服务重启中断',
               finished_at=?, error=COALESCE(error, '后端服务重启，未完成的本地任务已停止'), updated_at=?
               WHERE status IN ('queued','running','cancelling')""",
            (now, now),
        )


def _pipeline_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["run_id"], "status": row["status"], "stage": row["stage"],
        "started_at": row["started_at"], "finished_at": row["finished_at"], "error": row["error"],
        "logs": _decode(row["logs_json"], []), "result": _decode(row["result_json"], None),
    }


def save_candidate_run(payload: dict[str, Any]) -> None:
    init_db()
    strategy_id = str(payload.get("meta", {}).get("strategy") or "unknown")
    created_at = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM candidate_runs WHERE strategy_id=? AND pick_date=? AND run_date=?",
            (strategy_id, payload.get("pick_date", ""), payload.get("run_date", "")),
        ).fetchone()
        if existing:
            run_key = int(existing["id"])
            conn.execute("DELETE FROM candidates WHERE candidate_run_id=?", (run_key,))
            conn.execute("UPDATE candidate_runs SET created_at=?, meta_json=? WHERE id=?", (created_at, _json(payload.get("meta", {})), run_key))
        else:
            cur = conn.execute(
                "INSERT INTO candidate_runs(strategy_id,pick_date,run_date,created_at,meta_json) VALUES(?,?,?,?,?)",
                (strategy_id, payload.get("pick_date", ""), payload.get("run_date", ""), created_at, _json(payload.get("meta", {}))),
            )
            run_key = int(cur.lastrowid)
        conn.executemany(
            """INSERT INTO candidates(candidate_run_id,rank_no,code,name,candidate_date,strategy_id,close,turnover_n,score,extra_json)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                (run_key, index, str(item.get("code", "")).zfill(6), item.get("name", ""), item.get("date", ""),
                 item.get("strategy", strategy_id), float(item.get("close", 0) or 0),
                 float(item.get("turnover_n", 0) or 0), float(item.get("score", 0) or 0), _json(item.get("extra", {})))
                for index, item in enumerate(payload.get("candidates", []), 1)
            ],
        )


def latest_candidate_run(strategy_id: str | None = None) -> dict[str, Any] | None:
    init_db()
    query = "SELECT * FROM candidate_runs"
    params: tuple[Any, ...] = ()
    if strategy_id:
        query += " WHERE strategy_id=?"
        params = (strategy_id,)
    query += " ORDER BY created_at DESC, id DESC LIMIT 1"
    with _connect() as conn:
        run = conn.execute(query, params).fetchone()
        if not run:
            return None
        rows = conn.execute("SELECT * FROM candidates WHERE candidate_run_id=? ORDER BY rank_no", (run["id"],)).fetchall()
    return {
        "run_date": run["run_date"], "pick_date": run["pick_date"], "meta": _decode(run["meta_json"], {}),
        "candidates": [
            {"code": row["code"], "name": row["name"], "date": row["candidate_date"], "strategy": row["strategy_id"],
             "close": row["close"], "turnover_n": row["turnover_n"], "score": row["score"], "extra": _decode(row["extra_json"], {})}
            for row in rows
        ],
    }


def save_research_document(payload: dict[str, Any]) -> dict[str, Any]:
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO research_documents(title,content,source_url,source_type,captured_at,created_at)
               VALUES(?,?,?,?,?,?)""",
            (payload.get("title", "未命名研究素材"), payload.get("content", ""), payload.get("source_url"),
             payload.get("source_type", "manual"), payload.get("captured_at") or now, now),
        )
        row = conn.execute("SELECT * FROM research_documents WHERE id=?", (cur.lastrowid,)).fetchone()
    return _research_row(row)


def list_research_documents(limit: int = 30) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM research_documents ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    return [_research_row(row) for row in rows]


def delete_research_document(document_id: int) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM research_documents WHERE id=?", (document_id,))
    return bool(cur.rowcount)


def _research_row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in ["id", "title", "content", "source_url", "source_type", "captured_at", "created_at"]}


def upsert_knowledge_documents(documents: list[dict[str, Any]]) -> int:
    """Store versioned research evidence without duplicating periodic fetches."""
    if not documents:
        return 0
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in documents:
        knowledge_id = str(item.get("knowledge_id", "")).strip()
        source_key = str(item.get("source_key", "")).strip()
        if not knowledge_id or not source_key:
            raise ValueError("knowledge_id and source_key are required")
        rows.append(
            (
                knowledge_id,
                source_key,
                str(item.get("title") or "未命名知识条目"),
                str(item.get("content") or ""),
                item.get("source_url"),
                str(item.get("source_type") or "manual"),
                str(item.get("evidence_level") or "secondary"),
                item.get("published_at"),
                str(item.get("captured_at") or now),
                str(item.get("content_hash") or ""),
                _json(item.get("metadata", {})),
                1 if item.get("active", True) else 0,
                now,
                now,
            )
        )
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO knowledge_documents(
                   knowledge_id,source_key,title,content,source_url,source_type,evidence_level,
                   published_at,captured_at,content_hash,metadata_json,active,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(knowledge_id,source_key) DO UPDATE SET
                   title=excluded.title,content=excluded.content,source_url=excluded.source_url,
                   source_type=excluded.source_type,evidence_level=excluded.evidence_level,
                   published_at=excluded.published_at,captured_at=excluded.captured_at,
                   content_hash=excluded.content_hash,metadata_json=excluded.metadata_json,
                   active=excluded.active,updated_at=excluded.updated_at""",
            rows,
        )
    return len(rows)


def list_knowledge_documents(knowledge_id: str, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    safe_limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM knowledge_documents
               WHERE knowledge_id=? AND active=1
               ORDER BY COALESCE(published_at, captured_at) DESC, updated_at DESC
               LIMIT ?""",
            (knowledge_id, safe_limit),
        ).fetchall()
    return [_knowledge_row(row) for row in rows]


def knowledge_document_status(knowledge_id: str) -> dict[str, Any]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS document_count, MAX(captured_at) AS last_captured_at,
                      MAX(CASE WHEN source_type='bilibili_catalog' THEN captured_at END) AS last_public_refresh_at,
                      MAX(updated_at) AS last_updated_at, MAX(published_at) AS latest_published_at
               FROM knowledge_documents WHERE knowledge_id=? AND active=1""",
            (knowledge_id,),
        ).fetchone()
    return {
        "knowledge_id": knowledge_id,
        "document_count": int(row["document_count"] or 0),
        "last_captured_at": row["last_captured_at"],
        "last_public_refresh_at": row["last_public_refresh_at"],
        "last_updated_at": row["last_updated_at"],
        "latest_published_at": row["latest_published_at"],
    }


def _knowledge_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "knowledge_id": row["knowledge_id"],
        "source_key": row["source_key"],
        "title": row["title"],
        "content": row["content"],
        "source_url": row["source_url"],
        "source_type": row["source_type"],
        "evidence_level": row["evidence_level"],
        "published_at": row["published_at"],
        "captured_at": row["captured_at"],
        "content_hash": row["content_hash"],
        "metadata": _decode(row["metadata_json"], {}),
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def save_sector_scores(payload: dict[str, Any], sources: list[dict[str, Any]], model: str | None = None) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO sector_score_runs(generated_at,model,methodology_version,source_count,sources_json,payload_json)
               VALUES(?,?,?,?,?,?)""",
            (payload.get("generated_at") or datetime.now().isoformat(timespec="seconds"), model,
             payload.get("methodology", "super-boom-v2"), len(sources), _json(sources), _json(payload)),
        )


def latest_sector_scores() -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sector_score_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    payload = _decode(row["payload_json"], {})
    payload["source_count"] = row["source_count"]
    payload["sources"] = _decode(row["sources_json"], [])
    payload["methodology_version"] = row["methodology_version"]
    return payload


def save_candidate_ai_scores(payload: dict[str, Any], model: str | None = None) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO candidate_ai_score_runs(generated_at,strategy_id,pick_date,model,methodology_version,payload_json)
               VALUES(?,?,?,?,?,?)""",
            (payload.get("generated_at") or datetime.now().isoformat(timespec="seconds"), payload.get("strategy_id"),
             payload.get("pick_date"), model, payload.get("methodology", "super-boom-v2"), _json(payload)),
        )


def latest_candidate_ai_scores(strategy_id: str | None = None) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        if strategy_id:
            row = conn.execute(
                "SELECT * FROM candidate_ai_score_runs WHERE strategy_id=? ORDER BY id DESC LIMIT 1", (strategy_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM candidate_ai_score_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    payload = _decode(row["payload_json"], {})
    payload["methodology_version"] = row["methodology_version"]
    return payload


def upsert_stocks(stock_list: pd.DataFrame) -> None:
    """Persist TUShare stock_basic output while keeping the CSV cache usable."""
    if stock_list is None or stock_list.empty:
        return
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for item in stock_list.fillna("").to_dict(orient="records"):
        code = str(item.get("代码", item.get("symbol", ""))).zfill(6)
        if not code or code == "000000":
            continue
        rows.append((code, str(item.get("名称", item.get("name", ""))), str(item.get("ts_code", "")),
                     str(item.get("market", "")), str(item.get("list_date", "")), now))
    with _connect() as conn:
        conn.executemany(
            """INSERT INTO stocks(code,name,ts_code,market,list_date,updated_at) VALUES(?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET name=excluded.name,ts_code=excluded.ts_code,market=excluded.market,
               list_date=excluded.list_date,updated_at=excluded.updated_at""",
            rows,
        )


def load_stocks() -> pd.DataFrame:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT code AS 代码,name AS 名称,ts_code,market,list_date FROM stocks ORDER BY code").fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def _price_rows(code: str, adjust: str, frame: pd.DataFrame, now: str) -> list[tuple[Any, ...]]:
    if frame is None or frame.empty:
        return []
    normalized = frame.copy()
    if not isinstance(normalized.index, pd.DatetimeIndex):
        normalized.index = pd.to_datetime(normalized.index, errors="coerce")
    normalized = normalized[~normalized.index.isna()]
    if normalized.empty:
        return []
    fields = ["open", "high", "low", "close", "volume", "amount", "pct_chg", "change", "turnover"]
    for field in fields:
        if field not in normalized.columns:
            normalized[field] = 0.0
    return [
        (str(code).zfill(6), adjust or "bfq", index.strftime("%Y-%m-%d"),
         float(row.open or 0), float(row.high or 0), float(row.low or 0), float(row.close or 0),
         float(row.volume or 0), float(row.amount or 0), float(row.pct_chg or 0), float(row.change or 0),
         float(row.turnover or 0), now)
        for index, row in normalized[fields].iterrows()
    ]


_UPSERT_PRICE_SQL = """INSERT INTO daily_prices(code,adjust,trade_date,open,high,low,close,volume,amount,pct_chg,change_value,turnover,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(code,adjust,trade_date) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,
    close=excluded.close,volume=excluded.volume,amount=excluded.amount,pct_chg=excluded.pct_chg,
    change_value=excluded.change_value,turnover=excluded.turnover,updated_at=excluded.updated_at"""


def upsert_daily_prices(code: str, adjust: str, frame: pd.DataFrame) -> None:
    """Upsert normalized daily bars. Called after a fetch batch, never per API request."""
    init_db()
    rows = _price_rows(code, adjust, frame, datetime.now().isoformat(timespec="seconds"))
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(_UPSERT_PRICE_SQL, rows)


def upsert_price_batch(prices: dict[str, pd.DataFrame], adjust: str) -> None:
    init_db()
    now = datetime.now().isoformat(timespec="seconds")
    items = list(prices.items())
    total = len(items)
    chunk_size = 250
    for offset in range(0, total, chunk_size):
        chunk = items[offset : offset + chunk_size]
        with _connect() as conn:
            for code, frame in chunk:
                rows = _price_rows(code, adjust, frame, now)
                if rows:
                    conn.executemany(_UPSERT_PRICE_SQL, rows)
        # The run log handler writes pipeline status to the same database, so log
        # only after the price transaction has committed and released its lock.
        logger.info("SQLite 行情同步进度 %d/%d", min(offset + len(chunk), total), total)


def price_codes(adjust: str) -> set[str]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT DISTINCT code FROM daily_prices WHERE adjust=?", (adjust or "bfq",)).fetchall()
    return {str(row["code"]).zfill(6) for row in rows}


def rescale_qfq_history(ratios: dict[str, float], before_date: str) -> int:
    """Rebase cached qfq OHLC after the latest adjustment factor changes."""
    effective = {code: ratio for code, ratio in ratios.items() if abs(float(ratio) - 1.0) > 1e-10}
    if not effective:
        return 0
    init_db()
    updated = 0
    with _connect() as conn:
        for code, ratio in effective.items():
            cur = conn.execute(
                """UPDATE daily_prices SET open=open*?, high=high*?, low=low*?, close=close*?, updated_at=?
                   WHERE code=? AND adjust='qfq' AND trade_date<=?""",
                (ratio, ratio, ratio, ratio, datetime.now().isoformat(timespec="seconds"), code, before_date),
            )
            updated += int(cur.rowcount or 0)
    logger.info("前复权基准更新：%d 只股票，重标 %d 条历史行情", len(effective), updated)
    return updated


def price_data_signature(adjust: str) -> tuple[int, str | None, str | None]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT code) AS total, MAX(trade_date) AS latest_date, MAX(updated_at) AS updated_at FROM daily_prices WHERE adjust=?",
            (adjust or "bfq",),
        ).fetchone()
    return int(row["total"] or 0), row["latest_date"], row["updated_at"]


def market_turnover_snapshot(adjust: str = "qfq", trade_date: str | None = None) -> dict[str, Any]:
    """Return full-market turnover and the scoring coefficient for one trading day."""
    init_db()
    with _connect() as conn:
        resolved_date = trade_date
        if resolved_date:
            row = conn.execute(
                "SELECT MAX(trade_date) AS trade_date FROM daily_prices WHERE adjust=? AND trade_date<=?",
                (adjust or "qfq", resolved_date),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT MAX(trade_date) AS trade_date FROM daily_prices WHERE adjust=?",
                (adjust or "qfq",),
            ).fetchone()
        resolved_date = row["trade_date"] if row else None
        if not resolved_date:
            return {"trade_date": None, "amount_trillion": None, "coefficient": 1.0}
        amount_row = conn.execute(
            "SELECT SUM(amount) AS total_amount FROM daily_prices WHERE adjust=? AND trade_date=?",
            (adjust or "qfq", resolved_date),
        ).fetchone()
    # TUShare daily.amount is expressed in thousands of CNY.
    amount_trillion = float(amount_row["total_amount"] or 0) / 1_000_000_000
    coefficient = 0.8 if amount_trillion < 0.8 else 1.2 if amount_trillion > 1.5 else 1.0
    return {
        "trade_date": resolved_date,
        "amount_trillion": round(amount_trillion, 4),
        "coefficient": coefficient,
    }


def load_daily_prices(adjust: str, n_turnover_days: int, symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Read normalized OHLCV data for strategies, including the derived turnover_n column."""
    init_db()
    clauses = ["adjust=?"]
    params: list[Any] = [adjust or "bfq"]
    if symbols:
        codes = [str(code).zfill(6) for code in symbols]
        clauses.append(f"code IN ({','.join('?' for _ in codes)})")
        params.extend(codes)
    query = "SELECT * FROM daily_prices WHERE " + " AND ".join(clauses) + " ORDER BY code, trade_date"
    logger.info("SQLite 开始读取标准行情，adjust=%s", adjust)
    with _connect() as conn:
        frame = pd.read_sql_query(query, conn, params=params)
    if frame.empty:
        return {}
    logger.info("SQLite 已读取 %d 行行情，开始按股票整理", len(frame))
    result: dict[str, pd.DataFrame] = {}
    grouped = frame.groupby("code", sort=False)
    total = frame["code"].nunique()
    for index, (code, group) in enumerate(grouped, 1):
        group = group.copy()
        group.index = pd.to_datetime(group.pop("trade_date"), errors="coerce")
        group = group.drop(columns=["code", "adjust", "updated_at"], errors="ignore")
        group = group.rename(columns={"change_value": "change"})
        group["turnover_n"] = group["amount"].rolling(max(1, int(n_turnover_days)), min_periods=1).sum()
        result[str(code).zfill(6)] = group
        if index % 500 == 0 or index == total:
            logger.info("SQLite 行情整理进度 %d/%d", index, total)
    return result
