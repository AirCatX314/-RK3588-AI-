"""HTTP tools used by the agent service to interact with LabSafe Flask APIs."""

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import LABSAFE_BASE_URL


@dataclass
class ToolResult:
    name: str
    success: bool
    data: dict
    latency_ms: float
    error: str = ""


class LabSafeTools:
    def __init__(self, base_url=LABSAFE_BASE_URL, timeout=3.0, state_store=None):
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.state_store = state_store

    def _request(self, method, path, payload=None, timeout=None):
        body = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _request_file(self, path, file_path, field_name="file", timeout=None):
        boundary = f"----LabSafeAgent{int(time.time() * 1000)}"
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = head + file_bytes + tail
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}

    def _request_binary(self, path, timeout=None):
        req = urllib.request.Request(
            self.base_url + path,
            method="GET",
            headers={"Cache-Control": "no-store"},
        )
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            raw = resp.read()
            mime = resp.headers.get_content_type() or "application/octet-stream"
        return raw, mime

    def call(self, trace_id, name, method, path, payload=None, timeout=None):
        started = time.time()
        try:
            data = self._request(method, path, payload=payload, timeout=timeout)
            latency = (time.time() - started) * 1000
            result = ToolResult(name=name, success=True, data=data, latency_ms=latency)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as e:
            latency = (time.time() - started) * 1000
            result = ToolResult(name=name, success=False, data={}, latency_ms=latency, error=str(e))
        if self.state_store is not None:
            self.state_store.record_tool_call(
                trace_id, name, started, result.latency_ms, result.success, result.error
            )
        return result

    def get_system_status(self, trace_id):
        return self.call(trace_id, "get_system_status", "GET", "/api/status")

    def get_latest_detections(self, trace_id):
        return self.call(
            trace_id,
            "get_latest_detections",
            "GET",
            "/api/camera/usb-camera/detections",
        )

    def get_emergency_call_status(self, trace_id):
        return self.call(trace_id, "get_emergency_call_status", "GET", "/api/emergency-call/status")

    def add_message(self, trace_id, sender, content, message_type="chat"):
        return self.call(
            trace_id,
            "add_message",
            "POST",
            "/api/messages/send",
            payload={"sender": sender, "content": content, "type": message_type},
        )

    def start_emergency_call(self, trace_id, reason):
        return self.call(
            trace_id,
            "start_emergency_call",
            "POST",
            "/api/emergency-call/start",
            payload={"reason": reason},
            timeout=8.0,
        )

    def send_emergency_alert(self, trace_id, lab_name, alert_type, message):
        return self.call(
            trace_id,
            "send_emergency_alert",
            "POST",
            "/api/alert/emergency",
            payload={"lab_name": lab_name, "type": alert_type, "message": message},
            timeout=8.0,
        )

    def analyze_uploaded_image(self, trace_id, file_path):
        started = time.time()
        try:
            data = self._request_file("/api/detection/analyze-upload", file_path, timeout=10.0)
            latency = (time.time() - started) * 1000
            result = ToolResult(name="analyze_uploaded_image", success=True, data=data, latency_ms=latency)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as e:
            latency = (time.time() - started) * 1000
            result = ToolResult(name="analyze_uploaded_image", success=False, data={}, latency_ms=latency, error=str(e))
        if self.state_store is not None:
            self.state_store.record_tool_call(
                trace_id, result.name, started, result.latency_ms, result.success, result.error
            )
        return result

    def get_camera_snapshot_image(self, trace_id, detect=True):
        """Fetch current camera JPEG bytes for multimodal model input."""
        started = time.time()
        path = "/api/camera/usb-camera/snapshot/detect" if detect else "/api/camera/usb-camera/snapshot"
        try:
            raw, mime = self._request_binary(path, timeout=max(self.timeout + 2.0, 5.0))
            success = bool(raw) and str(mime).startswith("image/")
            data = {"bytes": raw, "mime": mime, "path": path, "size_bytes": len(raw)}
            result = ToolResult(
                name="get_camera_snapshot_image",
                success=success,
                data=data if success else {},
                latency_ms=(time.time() - started) * 1000,
                error="" if success else f"snapshot returned non-image content-type: {mime}",
            )
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            result = ToolResult(
                name="get_camera_snapshot_image",
                success=False,
                data={},
                latency_ms=(time.time() - started) * 1000,
                error=str(e),
            )
        if self.state_store is not None:
            self.state_store.record_tool_call(
                trace_id, result.name, started, result.latency_ms, result.success, result.error
            )
        return result

    def camera_snapshot_attachment(self, detect=True):
        suffix = int(time.time() * 1000)
        primary = "/api/camera/usb-camera/snapshot/detect" if detect else "/api/camera/usb-camera/snapshot"
        return {
            "type": "image",
            "title": "当前摄像头画面",
            "url": f"{primary}?ts={suffix}",
            "fallback_url": f"/api/camera/usb-camera/snapshot?ts={suffix}",
            "mime": "image/jpeg",
            "source": "camera",
            "cam_id": "usb-camera",
            "detected": bool(detect),
        }

    def collect_snapshot(self, trace_id):
        status = self.get_system_status(trace_id)
        detections = self.get_latest_detections(trace_id)
        emergency = self.get_emergency_call_status(trace_id)
        return {
            "status": status.data if status.success else {},
            "detections": detections.data if detections.success else {},
            "emergency_call": emergency.data if emergency.success else {},
            "tool_health": {
                status.name: {"success": status.success, "error": status.error, "latency_ms": status.latency_ms},
                detections.name: {
                    "success": detections.success,
                    "error": detections.error,
                    "latency_ms": detections.latency_ms,
                },
                emergency.name: {
                    "success": emergency.success,
                    "error": emergency.error,
                    "latency_ms": emergency.latency_ms,
                },
            },
        }
