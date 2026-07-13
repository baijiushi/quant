from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

import yaml

from storage.database import (
    knowledge_document_status,
    list_knowledge_documents,
    upsert_knowledge_documents,
)

ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = ROOT / "skills" / "benben-super-boom" / "references"
EVIDENCE_FILE = PACK_DIR / "evidence.yaml"
PRIVATE_EVIDENCE_FILE = ROOT / "data" / "knowledge" / "private_evidence.yaml"
METHODOLOGY_FILE = PACK_DIR / "methodology.md"
CONFIG_FILE = ROOT / "config" / "ai_scoring.yaml"
KNOWLEDGE_ID = "benben-super-boom"
BILIBILI_COLLECTION_API = "https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_methodology() -> str:
    if not METHODOLOGY_FILE.exists():
        return ""
    return METHODOLOGY_FILE.read_text(encoding="utf-8")


def sync_static_knowledge() -> int:
    captured_at = _now()
    documents = []
    for evidence_file in (EVIDENCE_FILE, PRIVATE_EVIDENCE_FILE):
        payload = _read_yaml(evidence_file)
        knowledge_id = str(payload.get("knowledge_id") or KNOWLEDGE_ID)
        for raw in payload.get("documents", []) or []:
            content = str(raw.get("content") or "").strip()
            documents.append(
                {
                    **raw,
                    "knowledge_id": knowledge_id,
                    "captured_at": raw.get("captured_at") or captured_at,
                    "content_hash": _hash(content),
                    "metadata": {
                        "pack_version": payload.get("version", "unknown"),
                        "private_local": evidence_file == PRIVATE_EVIDENCE_FILE,
                        **(raw.get("metadata") or {}),
                    },
                }
            )
    return upsert_knowledge_documents(documents)


def _fetch_json(url: str) -> dict[str, Any]:
    req = request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 oversell-knowledge/1.0",
            "Referer": "https://space.bilibili.com/11473291/",
        },
    )
    with request.urlopen(req, timeout=20) as response:
        return json.loads(response.read(2_000_000).decode("utf-8"))


def _collection_documents(collection: dict[str, Any]) -> list[dict[str, Any]]:
    season_id = int(collection["season_id"])
    page_size = 30
    page_num = 1
    captured_at = _now()
    documents: list[dict[str, Any]] = []
    while True:
        query = parse.urlencode(
            {
                "mid": int(collection.get("mid", 11473291)),
                "season_id": season_id,
                "sort_reverse": "false",
                "page_num": page_num,
                "page_size": page_size,
            }
        )
        payload = _fetch_json(f"{BILIBILI_COLLECTION_API}?{query}")
        if payload.get("code") != 0:
            raise RuntimeError(f"Bilibili collection {season_id} failed: {payload.get('message') or payload.get('code')}")
        data = payload.get("data") or {}
        archives = data.get("archives") or []
        for archive in archives:
            bvid = str(archive.get("bvid") or "")
            title = str(archive.get("title") or "未命名视频")
            published_at = None
            if archive.get("pubdate"):
                published_at = datetime.fromtimestamp(int(archive["pubdate"]), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
            content = (
                f"合集：{collection.get('name', season_id)}\n视频标题：{title}\n"
                "证据限制：该条来自公开合集目录，标题仅用于发现研究方向，不能单独证明具体观点或评分。"
            )
            documents.append(
                {
                    "knowledge_id": KNOWLEDGE_ID,
                    "source_key": f"bilibili-season-{season_id}-{bvid or archive.get('aid')}",
                    "title": title,
                    "content": content,
                    "source_url": f"https://www.bilibili.com/video/{bvid}/" if bvid else None,
                    "source_type": "bilibili_catalog",
                    "evidence_level": "catalog",
                    "published_at": published_at,
                    "captured_at": captured_at,
                    "content_hash": _hash(content),
                    "metadata": {
                        "season_id": season_id,
                        "collection_name": collection.get("name"),
                        "bvid": bvid,
                        "aid": archive.get("aid"),
                        "views": (archive.get("stat") or {}).get("view"),
                    },
                }
            )
        total = int((data.get("page") or {}).get("total") or len(documents))
        if not archives or page_num * page_size >= total:
            break
        page_num += 1
    return documents


def _knowledge_config() -> dict[str, Any]:
    return _read_yaml(CONFIG_FILE).get("knowledge", {})


def knowledge_status() -> dict[str, Any]:
    sync_static_knowledge()
    status = knowledge_document_status(KNOWLEDGE_ID)
    cfg = _knowledge_config()
    last = status.get("last_public_refresh_at")
    stale = True
    if last:
        try:
            parsed = datetime.fromisoformat(str(last))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            stale = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc) >= timedelta(
                hours=max(1, int(cfg.get("update_interval_hours", 24)))
            )
        except ValueError:
            stale = True
    return {
        **status,
        "enabled": bool(cfg.get("enabled", True)),
        "auto_update": bool(cfg.get("auto_update", True)),
        "stale": stale,
        "methodology_file": str(METHODOLOGY_FILE.relative_to(ROOT)),
    }


def refresh_public_knowledge(force: bool = False) -> dict[str, Any]:
    seeded = sync_static_knowledge()
    status = knowledge_status()
    cfg = _knowledge_config()
    if not cfg.get("enabled", True):
        return {**status, "refreshed": False, "reason": "disabled", "seeded": seeded}
    if not force and not status["stale"]:
        return {**status, "refreshed": False, "reason": "fresh", "seeded": seeded}

    documents: list[dict[str, Any]] = []
    errors: list[str] = []
    for collection in cfg.get("collections", []) or []:
        try:
            documents.extend(_collection_documents(collection))
        except Exception as exc:  # noqa: BLE001
            logger.warning("知识库合集更新失败 season=%s: %s", collection.get("season_id"), exc)
            errors.append(f"season {collection.get('season_id')}: {exc}")
    if documents:
        upsert_knowledge_documents(documents)
    return {
        **knowledge_status(),
        "refreshed": bool(documents),
        "fetched_documents": len(documents),
        "seeded": seeded,
        "errors": errors,
    }


def ensure_knowledge_fresh() -> dict[str, Any]:
    cfg = _knowledge_config()
    if not cfg.get("auto_update", True):
        sync_static_knowledge()
        return knowledge_status()
    return refresh_public_knowledge(force=False)


def knowledge_documents(limit: int = 120) -> list[dict[str, Any]]:
    sync_static_knowledge()
    return list_knowledge_documents(KNOWLEDGE_ID, limit=limit)


def build_methodology_context(max_chars: int = 18_000) -> str:
    priority = {"direct": 0, "user_primary": 1, "secondary": 2, "catalog": 3}
    documents = sorted(
        knowledge_documents(),
        key=lambda item: (priority.get(str(item.get("evidence_level")), 9), str(item.get("published_at") or "")),
    )
    sections = [load_methodology().strip(), "\n## 可追溯证据\n"]
    for item in documents:
        section = (
            f"[{item['source_key']}] 等级={item['evidence_level']} 类型={item['source_type']} "
            f"日期={item.get('published_at') or '未标注'}\n{item['content']}\n来源={item.get('source_url') or '用户提供/项目内'}\n"
        )
        if sum(len(part) for part in sections) + len(section) > max_chars:
            break
        sections.append(section)
    return "\n".join(sections)[:max_chars]
