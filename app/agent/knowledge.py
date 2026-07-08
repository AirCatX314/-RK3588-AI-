"""Lightweight local knowledge base for LabSafe Agent.

The board-friendly first version uses SQLite FTS5 when available and falls
back to keyword scoring. It intentionally avoids vector models so the RK3588
keeps CPU and memory headroom for camera and RKNN workloads.
"""

import json
import os
import re
import sqlite3
import time


BUILTIN_DOCS = [
    {
        "source": "builtin:safety_policy",
        "title": "LabSafe Agent 安全策略",
        "content": (
            "LabSafe Agent 的风险分级由本地确定性规则完成，模型只负责语言理解和回复生成。"
            "高风险动作如拨打电话、报警、发送紧急通知必须经过人工确认。"
            "PPE 缺失检测第一版不启用，不因口罩、手套、护目镜、实验服缺失升级风险。"
            "danger 条件包括 fire_alarm=true、后端确认火焰或烟雾、温度异常叠加火灾或烟雾状态。"
        ),
    },
    {
        "source": "builtin:tools",
        "title": "LabSafe Agent 工具说明",
        "content": (
            "Agent 可读取 /api/status、/api/camera/usb-camera/detections、"
            "/api/emergency-call/status，并可写入消息中心。"
            "摄像头图片通过 /api/camera/usb-camera/snapshot/detect 或 /api/camera/usb-camera/snapshot 返回。"
            "上传文件通过 /api/agent/uploads 保存，聊天时通过 attachment_ids 关联。"
        ),
    },
    {
        "source": "builtin:models",
        "title": "LabSafe Agent 模型路由",
        "content": (
            "当前模型 Provider 支持 MiniMax、DeepSeek、本地 OpenAI-compatible 服务和规则模式。"
            "模型切换只影响语言生成，不影响风险分级和危险动作确认。"
            "断网、API key 缺失或模型超时时，Agent 回退到本地规则模式。"
        ),
    },
]


class KnowledgeBase:
    def __init__(self, db_path, config=None):
        self.db_path = db_path
        self.config = config or {}
        self._fts_available = None
        self._last_refresh = 0.0

    def search(self, query, limit=5):
        query = (query or "").strip()
        if not query or not self.config.get("enabled", True):
            return []
        self._ensure_index()
        if self._fts_available:
            hits = self._search_fts(query, limit)
            if hits:
                return hits
        return self._search_keyword(query, limit)

    def _connect(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_index(self):
        # Re-check at most once per minute; file mtimes still trigger refresh.
        now = time.time()
        sources = self._load_sources()
        signature = json.dumps(
            [(item["source"], item.get("mtime", 0), len(item.get("content", ""))) for item in sources],
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._connect() as conn:
            self._init_tables(conn)
            row = conn.execute("SELECT value FROM settings WHERE key = 'knowledge_signature'").fetchone()
            old_signature = row["value"] if row else ""
            if old_signature == signature and now - self._last_refresh < 60:
                return
            conn.execute("DELETE FROM agent_knowledge_chunks")
            if self._fts_available:
                conn.execute("DELETE FROM agent_knowledge_fts")
            for item in sources:
                self._insert_source(conn, item)
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES('knowledge_signature', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (signature, now),
            )
        self._last_refresh = now

    def _init_tables(self, conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_knowledge_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        if self._fts_available is False:
            return
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS agent_knowledge_fts
                USING fts5(title, content, source UNINDEXED, chunk_id UNINDEXED)
                """
            )
            self._fts_available = True
        except sqlite3.OperationalError:
            self._fts_available = False

    def _insert_source(self, conn, item):
        for index, chunk in enumerate(self._chunk_text(item.get("content", ""))):
            cursor = conn.execute(
                """
                INSERT INTO agent_knowledge_chunks(source, title, chunk_index, content)
                VALUES(?, ?, ?, ?)
                """,
                (item["source"], item.get("title", ""), index, chunk),
            )
            if self._fts_available:
                conn.execute(
                    """
                    INSERT INTO agent_knowledge_fts(title, content, source, chunk_id)
                    VALUES(?, ?, ?, ?)
                    """,
                    (item.get("title", ""), chunk, item["source"], cursor.lastrowid),
                )

    def _load_sources(self):
        items = [dict(item) for item in BUILTIN_DOCS]
        for item in items:
            item["mtime"] = 0
        for path in self._source_paths():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                if content.strip():
                    items.append({
                        "source": path,
                        "title": os.path.basename(path),
                        "content": self._summarize_config(path, content),
                        "mtime": os.path.getmtime(path),
                    })
            except Exception:
                continue
        return items

    def _source_paths(self):
        paths = list(self.config.get("sources") or [])
        extra = os.environ.get("LABSAFE_KNOWLEDGE_SOURCES", "")
        if extra:
            paths.extend([p.strip() for p in extra.split(os.pathsep) if p.strip()])
        seen = set()
        for path in paths:
            norm = os.path.abspath(os.path.expanduser(path))
            if norm not in seen:
                seen.add(norm)
                yield norm

    @staticmethod
    def _summarize_config(path, content):
        if not path.endswith(".json"):
            return content
        try:
            data = json.loads(content)
            safe = {
                "fire": data.get("fire", {}),
                "agent": data.get("agent", {}),
                "notifications": {
                    "sound": (data.get("notifications") or {}).get("sound"),
                    "email": (data.get("notifications") or {}).get("email"),
                },
                "emergency_call": {
                    key: value
                    for key, value in (data.get("emergency_call") or {}).items()
                    if "key" not in key.lower() and "password" not in key.lower()
                },
            }
            return json.dumps(safe, ensure_ascii=False, indent=2)
        except Exception:
            return content

    @staticmethod
    def _chunk_text(text, max_chars=900, overlap=120):
        text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
        if not text:
            return []
        chunks = []
        start = 0
        while start < len(text):
            end = min(len(text), start + max_chars)
            cut = text.rfind("\n\n", start, end)
            if cut <= start + 240:
                cut = end
            chunk = text[start:cut].strip()
            if chunk:
                chunks.append(chunk)
            if cut >= len(text):
                break
            start = max(cut - overlap, 0)
        return chunks

    def _search_fts(self, query, limit):
        tokens = self._query_tokens(query)
        if not tokens:
            return []
        fts_query = " OR ".join(tokens[:8])
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT c.source, c.title, c.content, bm25(agent_knowledge_fts) AS score
                    FROM agent_knowledge_fts
                    JOIN agent_knowledge_chunks c ON c.id = agent_knowledge_fts.chunk_id
                    WHERE agent_knowledge_fts MATCH ?
                    ORDER BY score
                    LIMIT ?
                    """,
                    (fts_query, int(limit)),
                ).fetchall()
            return [self._hit(row, rank=index + 1) for index, row in enumerate(rows)]
        except sqlite3.Error:
            return []

    def _search_keyword(self, query, limit):
        tokens = self._query_tokens(query)
        if not tokens:
            return []
        with self._connect() as conn:
            rows = conn.execute("SELECT source, title, content FROM agent_knowledge_chunks").fetchall()
        scored = []
        for row in rows:
            text = f"{row['title']} {row['content']}".lower()
            score = 0
            for token in tokens:
                score += text.count(token.lower())
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._hit(row, rank=index + 1, score=score) for index, (score, row) in enumerate(scored[: int(limit)])]

    @staticmethod
    def _query_tokens(query):
        tokens = re.findall(r"[A-Za-z0-9_./:-]+|[\u4e00-\u9fff]{2,}", query or "")
        if not tokens and query:
            tokens = [query[:20]]
        return [token.replace('"', '""') for token in tokens if token.strip()]

    @staticmethod
    def _hit(row, rank=1, score=None):
        content = row["content"]
        if len(content) > 420:
            content = content[:420].rstrip() + "..."
        return {
            "rank": rank,
            "source": row["source"],
            "title": row["title"] or row["source"],
            "content": content,
            "score": score,
        }
