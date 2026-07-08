"""Model provider adapters for MiniMax, DeepSeek, and local OpenAI-compatible APIs."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class ModelResponse:
    success: bool
    content: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    error: str = ""


class OpenAICompatibleProvider:
    def __init__(self, name, config, timeout=12.0):
        self.name = name
        self.config = config or {}
        self.timeout = float(timeout)

    @property
    def enabled(self):
        return bool(self.config.get("enabled", True)) and bool(self.config.get("api_key") or self.name == "local_openai_compatible")

    @property
    def model(self):
        return self.config.get("model") or "default"

    @property
    def supports_vision(self):
        return bool(self.config.get("supports_vision", False))

    def generate(self, messages, temperature=0.2, max_tokens=800, require_vision=False):
        started = time.time()
        if require_vision and not self.supports_vision:
            return ModelResponse(
                success=False,
                provider=self.name,
                model=self.model,
                latency_ms=0.0,
                error="provider does not support vision input",
            )
        endpoint = self._endpoint()
        headers = {"Content-Type": "application/json"}
        api_key = self.config.get("api_key") or ""
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        extra_body = self.config.get("extra_body")
        if isinstance(extra_body, dict):
            payload.update(extra_body)
        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                method="POST",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            content = self._extract_content(data)
            if not content:
                raise ValueError("empty model response")
            return ModelResponse(
                success=True,
                content=content,
                provider=self.name,
                model=self.model,
                latency_ms=(time.time() - started) * 1000,
            )
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            detail = f"HTTP {e.code}: {e.reason}"
            if body:
                detail = f"{detail}; {body[:300]}"
            return ModelResponse(
                success=False,
                provider=self.name,
                model=self.model,
                latency_ms=(time.time() - started) * 1000,
                error=detail,
            )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
            return ModelResponse(
                success=False,
                provider=self.name,
                model=self.model,
                latency_ms=(time.time() - started) * 1000,
                error=str(e),
            )

    def _endpoint(self):
        endpoint = self.config.get("endpoint")
        if endpoint:
            return endpoint
        base = str(self.config.get("base_url") or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        if self.name == "deepseek":
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    @staticmethod
    def _extract_content(data):
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            if msg.get("content"):
                return str(msg["content"])
            if choices[0].get("text"):
                return str(choices[0]["text"])
        if data.get("reply"):
            return str(data["reply"])
        if data.get("output_text"):
            return str(data["output_text"])
        return ""


class ModelRouter:
    def __init__(self, config):
        self.config = config or {}
        timeout = self.config.get("request_timeout_seconds", 30)
        providers_config = self.config.get("providers", {})
        self.providers = {
            name: OpenAICompatibleProvider(name, provider_config, timeout=timeout)
            for name, provider_config in providers_config.items()
        }
        self.last_status = {
            "provider": "",
            "model": "",
            "success": False,
            "error": "not called",
        }

    def generate(self, messages, require_vision=False):
        errors = []
        skipped = []
        temperature = float(self.config.get("temperature", 0.2))
        max_tokens = int(self.config.get("max_tokens", 800))
        for name in self.config.get("provider_order", []):
            provider = self.providers.get(name)
            if not provider:
                continue
            if not provider.enabled:
                skipped.append(f"{name}: disabled or missing api_key")
                continue
            if require_vision and not provider.supports_vision:
                errors.append(f"{name}: provider does not support vision input")
                continue
            result = provider.generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                require_vision=require_vision,
            )
            self.last_status = {
                "provider": result.provider,
                "model": result.model,
                "success": result.success,
                "error": result.error,
                "latency_ms": result.latency_ms,
            }
            if result.success:
                return result
            errors.append(f"{name}: {result.error}")
        if errors:
            error = "; ".join(errors)
        elif skipped:
            error = "; ".join(skipped)
        else:
            error = "no enabled provider"
        self.last_status = {"provider": "", "model": "", "success": False, "error": error}
        return ModelResponse(success=False, error=error)
