"""Client used by Flask main service to talk to the standalone agent service."""

import json
import urllib.error
import urllib.request

import requests

from .config import AGENT_SERVICE_URL


class AgentServiceClient:
    def __init__(self, service_url=AGENT_SERVICE_URL, timeout=5.0):
        self.service_url = service_url.rstrip("/")
        self.timeout = float(timeout)
        self._fallback = None

    def status(self):
        return self._request_or_fallback("GET", "/api/agent/status", fallback=lambda o: o.status())

    def chat(self, message, sender="user", deep_thinking=False, web_search=False, attachment_ids=None, session_id=None):
        payload = {
            "message": message,
            "sender": sender,
            "deep_thinking": bool(deep_thinking),
            "web_search": bool(web_search),
            "attachment_ids": attachment_ids or [],
            "session_id": session_id,
        }
        timeout = 60.0
        return self._request_or_fallback(
            "POST",
            "/api/agent/chat",
            payload=payload,
            timeout=timeout,
            fallback=lambda o: o.chat(
                message,
                sender,
                deep_thinking=deep_thinking,
                web_search=web_search,
                attachment_ids=attachment_ids or [],
                session_id=session_id,
            ),
        )

    def upload_file(self, file_storage):
        try:
            if hasattr(file_storage, "stream"):
                try:
                    file_storage.stream.seek(0)
                except Exception:
                    pass
            files = {
                "file": (
                    getattr(file_storage, "filename", "upload"),
                    file_storage.stream,
                    getattr(file_storage, "mimetype", "application/octet-stream"),
                )
            }
            resp = requests.post(self.service_url + "/api/agent/uploads", files=files, timeout=30)
            return resp.json()
        except Exception as e:
            try:
                if hasattr(file_storage, "stream"):
                    file_storage.stream.seek(0)
                return self._fallback_orchestrator().upload_file(file_storage)
            except Exception:
                return {"success": False, "error": str(e), "agent_service": "unavailable"}

    def confirm(self, token):
        return self._request_or_fallback(
            "POST", "/api/agent/action/confirm", payload={"token": token}, fallback=lambda o: o.confirm_action(token)
        )

    def set_enabled(self, enabled):
        path = "/api/agent/enable" if enabled else "/api/agent/disable"
        return self._request_or_fallback("POST", path, fallback=lambda o: o.set_enabled(enabled))

    def models(self):
        return self._request_or_fallback("GET", "/api/agent/models", fallback=lambda o: o.models())

    def select_model(self, provider, model=""):
        payload = {"provider": provider, "model": model}
        return self._request_or_fallback(
            "POST",
            "/api/agent/models/select",
            payload=payload,
            fallback=lambda o: o.select_model(provider, model),
        )

    def test_model(self, provider, model=""):
        payload = {"provider": provider, "model": model}
        return self._request_or_fallback(
            "POST",
            "/api/agent/models/test",
            payload=payload,
            timeout=45.0,
            fallback=lambda o: o.test_model(provider, model),
        )

    def _request_or_fallback(self, method, path, payload=None, fallback=None, timeout=None):
        try:
            return self._request(method, path, payload, timeout=timeout)
        except Exception as e:
            if fallback is None:
                return {"success": False, "error": str(e), "agent_service": "unavailable"}
            data = fallback(self._fallback_orchestrator())
            if isinstance(data, dict):
                data.setdefault("agent_service", "embedded_fallback")
                data.setdefault("service_error", str(e))
            return data

    def _request(self, method, path, payload=None, timeout=None):
        body = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.service_url + path,
            data=body,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _fallback_orchestrator(self):
        if self._fallback is None:
            from .orchestrator import AgentOrchestrator

            self._fallback = AgentOrchestrator()
        return self._fallback


_client = None


def get_agent_client():
    global _client
    if _client is None:
        _client = AgentServiceClient()
    return _client
