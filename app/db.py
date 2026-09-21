"""SQLite 持久化：任务记录存取。数据库随应用启动自动初始化。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json  TEXT NOT NULL
);
"""


def _db_path() -> str:
    return os.environ.get("DATABASE_PATH", "./data/tide_windows.db")


def _connect() -> sqlite3.Connection:
    path = Path(_db_path())
    if str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（幂等），应用启动时调用。"""
    with _connect() as conn:
        conn.executescript(SCHEMA)


def save_task(task_id: str, created_at: str, request_json: str, result_json: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tasks (task_id, status, created_at, request_json, result_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (task_id, "done", created_at, request_json, result_json),
        )


def get_task(task_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT task_id, status, created_at, request_json, result_json"
            " FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "task_id": row["task_id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "request": json.loads(row["request_json"]),
        "result": json.loads(row["result_json"]),
    }
