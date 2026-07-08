"""SQLite state store for conversations, tool calls, and confirmations."""

import json
import os
import sqlite3
import threading
import time

from .config import AGENT_DB_FILE


class StateStore:
    def __init__(self, db_path=AGENT_DB_FILE):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    session_id TEXT DEFAULT 'default',
                    created_at REAL NOT NULL,
                    sender TEXT,
                    user_message TEXT,
                    reply TEXT,
                    risk_level TEXT,
                    reason TEXT,
                    provider TEXT,
                    model TEXT,
                    fallback_used INTEGER DEFAULT 0,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    latency_ms REAL,
                    success INTEGER NOT NULL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS pending_actions (
                    token TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    reason TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    executed_at REAL,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS uploaded_files (
                    file_id TEXT PRIMARY KEY,
                    original_name TEXT,
                    stored_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    thumbnail_path TEXT,
                    mime_type TEXT,
                    kind TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    parse_status TEXT,
                    extracted_text TEXT,
                    analysis_json TEXT,
                    created_at REAL NOT NULL
                );
                """
            )
            self._ensure_column(conn, "agent_runs", "session_id", "TEXT DEFAULT 'default'")

    @staticmethod
    def _ensure_column(conn, table, column, definition):
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {row["name"] for row in rows}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def get_setting(self, key, default=None):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if not row:
                return default
            try:
                return json.loads(row["value"])
            except Exception:
                return row["value"]

    def set_setting(self, key, value):
        encoded = json.dumps(value, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, encoded, time.time()),
            )

    def record_run(self, trace_id, sender, user_message, reply, risk_level, reason, provider, model, fallback_used, error, session_id="default"):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs(trace_id, session_id, created_at, sender, user_message, reply, risk_level,
                                       reason, provider, model, fallback_used, error)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    session_id or "default",
                    time.time(),
                    sender,
                    user_message,
                    reply,
                    risk_level,
                    reason,
                    provider,
                    model,
                    1 if fallback_used else 0,
                    error or "",
                ),
            )

    def conversation_memory(self, session_id="default", limit=8, max_chars=6000, max_item_chars=900):
        session_id = session_id or "default"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT trace_id, created_at, sender, user_message, reply, risk_level, reason
                FROM agent_runs
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, int(limit)),
            ).fetchall()
        items = []
        total = 0
        for row in reversed(rows):
            user_text = self._clip(row["user_message"] or "", max_item_chars)
            reply_text = self._clip(row["reply"] or "", max_item_chars)
            total += len(user_text) + len(reply_text)
            if total > max_chars and items:
                continue
            items.append({
                "trace_id": row["trace_id"],
                "created_at": row["created_at"],
                "sender": row["sender"],
                "user": user_text,
                "assistant": reply_text,
                "risk_level": row["risk_level"],
                "reason": self._clip(row["reason"] or "", 240),
            })
        return items

    def record_tool_call(self, trace_id, tool_name, started_at, latency_ms, success, error=""):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_calls(trace_id, tool_name, started_at, latency_ms, success, error)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (trace_id, tool_name, started_at, latency_ms, 1 if success else 0, error or ""),
            )

    def create_pending_action(self, token, trace_id, action_type, title, payload, reason, ttl_seconds):
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pending_actions(token, trace_id, action_type, title, payload_json, reason,
                                            status, created_at, expires_at)
                VALUES(?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    token,
                    trace_id,
                    action_type,
                    title,
                    json.dumps(payload or {}, ensure_ascii=False),
                    reason or "",
                    now,
                    now + float(ttl_seconds),
                ),
            )

    def get_pending_action(self, token):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM pending_actions WHERE token = ?", (token,)).fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["payload"] = json.loads(data.pop("payload_json") or "{}")
            except Exception:
                data["payload"] = {}
            return data

    def finish_pending_action(self, token, status, result):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE pending_actions
                SET status = ?, executed_at = ?, result_json = ?
                WHERE token = ?
                """,
                (status, time.time(), json.dumps(result or {}, ensure_ascii=False), token),
            )

    def recent_runs(self, limit=10):
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT trace_id, session_id, created_at, risk_level, reason, provider, model, error
                FROM agent_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _clip(value, limit):
        value = str(value or "")
        limit = int(limit)
        if limit <= 0 or len(value) <= limit:
            return value
        return value[:limit].rstrip() + "..."

    def pending_actions(self, limit=10):
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT token, trace_id, action_type, title, reason, status, created_at, expires_at
                FROM pending_actions
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_upload(self, meta):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO uploaded_files(file_id, original_name, stored_name, file_path, thumbnail_path,
                                           mime_type, kind, size_bytes, parse_status, extracted_text,
                                           analysis_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta.get("file_id"),
                    meta.get("original_name", ""),
                    meta.get("stored_name", ""),
                    meta.get("file_path", ""),
                    meta.get("thumbnail_path", ""),
                    meta.get("mime_type", ""),
                    meta.get("kind", "file"),
                    int(meta.get("size_bytes") or 0),
                    meta.get("parse_status", ""),
                    meta.get("extracted_text", ""),
                    json.dumps(meta.get("analysis") or {}, ensure_ascii=False),
                    meta.get("created_at") or time.time(),
                ),
            )

    def update_upload_analysis(self, file_id, parse_status=None, extracted_text=None, analysis=None, thumbnail_path=None):
        fields = []
        values = []
        if parse_status is not None:
            fields.append("parse_status = ?")
            values.append(parse_status)
        if extracted_text is not None:
            fields.append("extracted_text = ?")
            values.append(extracted_text)
        if analysis is not None:
            fields.append("analysis_json = ?")
            values.append(json.dumps(analysis or {}, ensure_ascii=False))
        if thumbnail_path is not None:
            fields.append("thumbnail_path = ?")
            values.append(thumbnail_path)
        if not fields:
            return
        values.append(file_id)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE uploaded_files SET {', '.join(fields)} WHERE file_id = ?", values)

    def get_upload(self, file_id):
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM uploaded_files WHERE file_id = ?", (file_id,)).fetchone()
            return self._decode_upload(row)

    def list_uploads(self, file_ids):
        if not file_ids:
            return []
        placeholders = ",".join(["?"] * len(file_ids))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM uploaded_files WHERE file_id IN ({placeholders})",
                list(file_ids),
            ).fetchall()
        by_id = {row["file_id"]: self._decode_upload(row) for row in rows}
        return [by_id[file_id] for file_id in file_ids if file_id in by_id]

    @staticmethod
    def _decode_upload(row):
        if not row:
            return None
        data = dict(row)
        try:
            data["analysis"] = json.loads(data.pop("analysis_json") or "{}")
        except Exception:
            data["analysis"] = {}
        data["preview_url"] = f"/api/agent/uploads/{data['file_id']}/content"
        if data.get("thumbnail_path"):
            data["thumbnail_url"] = f"/api/agent/uploads/{data['file_id']}/thumbnail"
        else:
            data["thumbnail_url"] = ""
        return data
