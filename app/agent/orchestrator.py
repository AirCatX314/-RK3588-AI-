"""Agent orchestration: intent, tools, RAG, model call, and fallback."""

import base64
import json
import mimetypes
import os
import re
import time
import uuid
from copy import deepcopy

from .config import load_agent_config
from .guard import ActionGuard
from .knowledge import KnowledgeBase
from .model_providers import ModelResponse, ModelRouter
from .observability import log_event, setup_logger
from .policy import PPE_CLASSES, SafetyPolicy
from .state_store import StateStore
from .tools import LabSafeTools
from .uploads import UploadManager
from .web_search import WebSearchTool


SAFETY_KEYWORDS = (
    "实验室", "安全", "火灾", "火焰", "烟雾", "报警", "危险", "应急", "温度", "湿度",
    "摄像头", "通话", "传感器", "现在安全吗", "状态", "撤离", "风险", "画面", "检测",
)

CAMERA_REQUEST_KEYWORDS = (
    "当前照片", "当前图片", "当前画面", "现在画面", "实时画面", "摄像头画面",
    "摄像头图片", "查看摄像头", "看摄像头", "给我截图", "拍照", "当前截图",
    "实验室画面", "实验室照片", "实验室图片", "实验室当前画面", "当前实验室画面",
    "看看实验室", "看一下实验室", "查看画面", "看一下画面", "抓拍", "拍一张",
)

CAMERA_VISUAL_TERMS = ("画面", "照片", "图片", "截图", "拍照", "抓拍", "看一下", "看看", "查看")
CAMERA_SCOPE_TERMS = ("实验室", "摄像头", "当前", "现在", "实时", "现场")

SEARCH_REQUEST_KEYWORDS = (
    "联网", "上网", "网页", "网上", "搜索", "搜一下", "检索", "必应", "bing",
    "google", "谷歌", "百度", "最新", "新闻", "官网", "实时资讯", "实时信息",
)

PPE_QUERY_KEYWORDS = ("ppe", "PPE", "实验服", "手套", "护目镜", "口罩", "面罩", "个人防护", "防护用品")


class AgentOrchestrator:
    def __init__(self, config_file=None, tools=None, state_store=None, logger=None):
        self.config = load_agent_config(config_file) if config_file else load_agent_config()
        self.state_store = state_store or StateStore()
        self.tools = tools or LabSafeTools(
            timeout=self.config.get("tool_timeout_seconds", 3),
            state_store=self.state_store,
        )
        self.policy = SafetyPolicy(self.config)
        self.model_router = ModelRouter(self.config)
        self.guard = ActionGuard(self.state_store, self.tools, self.config)
        search_config = self.config.get("web_search") or {}
        self.web_search_tool = WebSearchTool(
            timeout=search_config.get("timeout_seconds", 6),
            max_results=search_config.get("max_results", 5),
            provider_order=search_config.get("provider_order") or ["bing_rss", "bing", "duckduckgo"],
        )
        self.knowledge = KnowledgeBase(self.state_store.db_path, self.config.get("knowledge") or {})
        self.uploads = UploadManager(self.state_store, self.tools, self.config.get("uploads") or {})
        self.logger = logger or setup_logger()

    def is_enabled(self):
        return bool(self.state_store.get_setting("enabled", self.config.get("enabled", True)))

    def set_enabled(self, enabled):
        self.state_store.set_setting("enabled", bool(enabled))
        return {"success": True, "enabled": bool(enabled)}

    def status(self):
        trace_id = self._trace_id()
        snapshot = self.tools.collect_snapshot(trace_id)
        decision = self.policy.evaluate(snapshot)
        return {
            "success": True,
            "enabled": self.is_enabled(),
            "risk_level": decision["risk_level"],
            "reason": decision["reason"],
            "recommendations": decision["recommendations"],
            "model_status": self.model_router.last_status,
            "tool_health": snapshot.get("tool_health", {}),
            "pending_actions": self.state_store.pending_actions(limit=10),
            "recent_runs": self.state_store.recent_runs(limit=10),
            "models": self.models(),
            "trace_id": trace_id,
        }

    def chat(self, message, sender="user", deep_thinking=False, web_search=False, attachment_ids=None, session_id=None):
        trace_id = self._trace_id()
        started = time.time()
        message = (message or "").strip()
        session_id = self._normalize_session_id(session_id, sender)
        deep_thinking = bool(deep_thinking)
        web_search = bool(web_search)
        attachment_ids = self._normalize_ids(attachment_ids)
        if not message:
            return self._empty_response(trace_id, "请输入需要咨询的问题。", success=False, session_id=session_id)
        if not self.is_enabled():
            return self._empty_response(trace_id, "Agent 当前已关闭，原有 LabSafe 报警链路仍保持运行。", session_id=session_id)

        log_event(self.logger, "agent_chat_start", trace_id=trace_id, sender=sender, session_id=session_id)
        self.tools.add_message(trace_id, sender, message, "chat")

        # Tool phase: collect live state and uploaded attachment context.
        snapshot = self.tools.collect_snapshot(trace_id)
        decision = self.policy.evaluate(snapshot)
        upload_context = self.uploads.context_for_ids(attachment_ids)
        camera_requested = self._is_camera_request(message)
        explicit_safety_request = self._is_safety_related(message)
        danger_context = decision["risk_level"] == "danger"
        safety_related = (
            explicit_safety_request
            or danger_context
            or camera_requested
            or self._has_image_upload(upload_context)
        )
        search_requested = bool(web_search or self._is_search_request(message))
        ppe_requested = self._is_ppe_request(message)

        response_attachments = []
        camera_image_result = None
        if camera_requested:
            response_attachments.append(self.tools.camera_snapshot_attachment(detect=True))
            camera_image_result = self.tools.get_camera_snapshot_image(trace_id, detect=True)
            if not camera_image_result.success:
                fallback_image = self.tools.get_camera_snapshot_image(trace_id, detect=False)
                if fallback_image.success:
                    camera_image_result = fallback_image
        response_attachments.extend(self._attachments_from_uploads(upload_context))
        vision_images = self._vision_images(camera_image_result, upload_context)
        vision_required = bool(vision_images)

        # Retrieval phase: local KB first, optional web search second.
        knowledge_hits = self._knowledge_hits(message, safety_related, upload_context)
        search_result = self._maybe_search(trace_id, message, search_requested)
        conversation_memory = self._conversation_memory(session_id)
        tool_context = self._tool_context(
            snapshot=snapshot,
            decision=decision,
            upload_context=upload_context,
            knowledge_hits=knowledge_hits,
            search_result=search_result,
            camera_requested=camera_requested,
            camera_image_result=camera_image_result,
            vision_images=vision_images,
            conversation_memory=conversation_memory,
        )

        prompt_messages = self._build_prompt(
            message=message,
            snapshot=snapshot,
            decision=decision,
            safety_related=safety_related,
            deep_thinking=deep_thinking,
            search_result=search_result,
            upload_context=upload_context,
            knowledge_hits=knowledge_hits,
            camera_requested=camera_requested,
            vision_images=vision_images,
            ppe_requested=ppe_requested,
            conversation_memory=conversation_memory,
        )
        model_result = self._generate_model(
            prompt_messages,
            deep_thinking=deep_thinking,
            web_search=search_requested,
            require_vision=vision_required,
        )
        if model_result.success:
            reply = model_result.content.strip()
            fallback_used = bool(model_result.provider and model_result.provider != self._active_provider())
            error = ""
        else:
            fallback_used = model_result.provider != "rules_only"
            error = model_result.error
            rules_only = model_result.provider == "rules_only"
            if decision["risk_level"] == "normal" and not rules_only and safety_related:
                decision = dict(decision)
                decision["risk_level"] = "notice"
                decision["reason"] = decision["reason"] + "；模型 API 暂时不可用，已使用规则模式"
            reply = self._rule_reply(
                message,
                decision,
                snapshot,
                safety_related,
                model_result.error,
                search_result=search_result,
                upload_context=upload_context,
                camera_requested=camera_requested,
                knowledge_hits=knowledge_hits,
                vision_required=vision_required,
                conversation_memory=conversation_memory,
            )

        proposed_actions = []
        if decision["risk_level"] == "danger":
            proposed_actions.append(
                self.guard.propose(
                    trace_id,
                    "start_emergency_call",
                    "确认拨打应急电话",
                    {"reason": f"Agent 危险判断: {decision['reason']}"},
                    decision["reason"],
                )
            )

        public_search = self._public_search_result(search_result)
        response = {
            "reply": reply,
            "risk_level": decision["risk_level"],
            "reason": decision["reason"],
            "recommendations": decision["recommendations"],
            "requires_confirmation": any(a.get("requires_confirmation") for a in proposed_actions),
            "proposed_actions": proposed_actions,
            "trace_id": trace_id,
            "session_id": session_id,
            "model": {
                "provider": model_result.provider,
                "model": model_result.model,
                "success": model_result.success,
                "fallback_used": fallback_used,
                "error": error,
            },
            "ignored_ppe_classes": decision.get("ignored_ppe_classes", []),
            "deep_thinking": deep_thinking,
            "vision": self._public_vision(vision_images, model_result),
            "web_search": public_search,
            "search": public_search,
            "attachments": response_attachments,
            "tool_context": tool_context,
            "conversation_memory": {
                "enabled": self._memory_config().get("enabled", True),
                "turn_count": len(conversation_memory),
            },
            "knowledge_hits": self._public_knowledge_hits(knowledge_hits),
            "success": True,
        }
        self.tools.add_message(trace_id, "agent", reply, "agent")
        self.state_store.record_run(
            trace_id,
            sender,
            message,
            reply,
            decision["risk_level"],
            decision["reason"],
            model_result.provider,
            model_result.model,
            fallback_used,
            error,
            session_id=session_id,
        )
        log_event(
            self.logger,
            "agent_chat_done",
            trace_id=trace_id,
            session_id=session_id,
            risk_level=decision["risk_level"],
            provider=model_result.provider,
            fallback_used=fallback_used,
            latency_ms=(time.time() - started) * 1000,
        )
        return response

    def upload_file(self, file_storage):
        trace_id = self._trace_id()
        result = self.uploads.save_upload(file_storage, trace_id=trace_id)
        result["trace_id"] = trace_id
        return result

    def upload_file_path(self, file_id, thumbnail=False):
        return self.uploads.get_file_path(file_id, thumbnail=thumbnail)

    def confirm_action(self, token):
        result = self.guard.confirm(token)
        log_event(self.logger, "agent_action_confirm", token=token, success=result.get("success"))
        return result

    def models(self):
        active_provider = self._active_provider()
        active_model = self._active_model(active_provider)
        providers = []
        for name, provider_config in (self.config.get("providers") or {}).items():
            providers.append({
                "id": name,
                "label": self._provider_label(name),
                "model": self._active_model(name),
                "default_model": provider_config.get("model", ""),
                "base_url": provider_config.get("base_url", ""),
                "enabled": bool(provider_config.get("enabled", True)),
                "api_key_configured": bool(provider_config.get("api_key")),
                "supports_vision": bool(provider_config.get("supports_vision", False)),
                "active": name == active_provider,
            })
        providers.append({
            "id": "rules_only",
            "label": "规则模式",
            "model": "rules_only",
            "default_model": "rules_only",
            "base_url": "",
            "enabled": True,
            "api_key_configured": True,
            "supports_vision": False,
            "active": active_provider == "rules_only",
        })
        return {
            "success": True,
            "active_provider": active_provider,
            "active_model": active_model,
            "providers": providers,
            "last_status": self.model_router.last_status,
        }

    def select_model(self, provider, model=""):
        provider = (provider or "").strip()
        valid = set((self.config.get("providers") or {}).keys()) | {"rules_only"}
        if provider not in valid:
            return {"success": False, "message": f"不支持的模型 Provider: {provider}"}
        self.state_store.set_setting("active_provider", provider)
        if provider != "rules_only" and model:
            self.state_store.set_setting(f"active_model:{provider}", str(model).strip())
        log_event(self.logger, "agent_model_selected", provider=provider, model=model or self._active_model(provider))
        return self.models()

    def test_model(self, provider, model=""):
        provider = (provider or "").strip() or self._active_provider()
        if provider == "rules_only":
            self.model_router.last_status = {
                "provider": "rules_only",
                "model": "rules_only",
                "success": True,
                "error": "",
                "latency_ms": 0,
            }
            return {"success": True, "provider": "rules_only", "model": "rules_only", "message": "规则模式可用"}
        runtime_config = self._runtime_model_config(provider=provider, model=model)
        runtime_config["provider_order"] = [provider]
        router = ModelRouter(runtime_config)
        result = router.generate([
            {"role": "system", "content": "你是 LabSafe Agent 模型连通性测试。"},
            {"role": "user", "content": "请只回复 ok。"},
        ])
        self.model_router.last_status = router.last_status
        return {
            "success": result.success,
            "provider": result.provider or provider,
            "model": result.model or model or self._active_model(provider),
            "message": "模型连接正常" if result.success else "模型连接失败",
            "error": result.error,
            "latency_ms": result.latency_ms,
        }

    def _generate_model(self, messages, deep_thinking=False, web_search=False, require_vision=False):
        active_provider = self._active_provider()
        if active_provider == "rules_only":
            self.model_router.last_status = {
                "provider": "rules_only",
                "model": "rules_only",
                "success": True,
                "error": "",
                "latency_ms": 0,
            }
            return ModelResponse(
                success=False,
                provider="rules_only",
                model="rules_only",
                error="rules_only selected",
            )
        runtime_config = self._runtime_model_config(provider=active_provider, require_vision=require_vision)
        if deep_thinking:
            runtime_config["max_tokens"] = max(
                int(runtime_config.get("max_tokens", 800)),
                int(runtime_config.get("deep_thinking_max_tokens", 1400)),
            )
            runtime_config["request_timeout_seconds"] = max(
                float(runtime_config.get("request_timeout_seconds", 30)),
                float(runtime_config.get("deep_thinking_timeout_seconds", 60)),
            )
        elif web_search:
            runtime_config["max_tokens"] = max(int(runtime_config.get("max_tokens", 800)), 1100)
            runtime_config["request_timeout_seconds"] = max(float(runtime_config.get("request_timeout_seconds", 30)), 30.0)
        if require_vision:
            runtime_config["max_tokens"] = max(int(runtime_config.get("max_tokens", 800)), 1400)
            runtime_config["request_timeout_seconds"] = max(
                float(runtime_config.get("request_timeout_seconds", 30)),
                45.0,
            )
        router = ModelRouter(runtime_config)
        result = router.generate(messages, require_vision=require_vision)
        self.model_router.last_status = router.last_status
        return result

    def _runtime_model_config(self, provider=None, model="", require_vision=False):
        provider = provider or self._active_provider()
        runtime_config = deepcopy(self.config)
        if provider in runtime_config.get("providers", {}):
            order = [provider]
            for name in runtime_config.get("provider_order", []):
                if name not in order:
                    order.append(name)
            if require_vision:
                for name in runtime_config.get("providers", {}):
                    if name not in order and runtime_config["providers"][name].get("supports_vision"):
                        order.append(name)
            runtime_config["provider_order"] = order
            runtime_config["providers"][provider]["enabled"] = True
            selected_model = (model or self._active_model(provider)).strip()
            if selected_model:
                runtime_config["providers"][provider]["model"] = selected_model
        return runtime_config

    def _active_provider(self):
        provider = self.state_store.get_setting("active_provider", self.config.get("primary_provider", "minimax"))
        valid = set((self.config.get("providers") or {}).keys()) | {"rules_only"}
        return provider if provider in valid else self.config.get("primary_provider", "minimax")

    def _active_model(self, provider):
        if provider == "rules_only":
            return "rules_only"
        provider_config = (self.config.get("providers") or {}).get(provider, {})
        default_model = provider_config.get("model", "")
        return self.state_store.get_setting(f"active_model:{provider}", default_model)

    @staticmethod
    def _provider_label(provider):
        return {
            "minimax": "MiniMax",
            "deepseek": "DeepSeek",
            "local_openai_compatible": "本地模型",
            "rules_only": "规则模式",
        }.get(provider, provider)

    def _build_prompt(
        self,
        message,
        snapshot,
        decision,
        safety_related,
        deep_thinking=False,
        search_result=None,
        upload_context=None,
        knowledge_hits=None,
        camera_requested=False,
        vision_images=None,
        ppe_requested=False,
        conversation_memory=None,
    ):
        system = (
            "你是 LabSafe 智能 Agent。你按用户问题直接回答，但在需要时会像工程 Agent 一样先调用工具、检索知识库、融合实时状态再作答。"
            "不要在每次回复里主动附加传感器状态、风险等级、安全建议、免责声明、功能菜单或示例问题。"
            "你有短期会话记忆，可以用 conversation_memory 理解用户的代词、继续追问和上文提到的对象；"
            "但实验室实时状态、摄像头画面、上传文件和联网搜索结果必须以本轮工具上下文为准，不能用旧记忆覆盖当前状态。"
            "不要主动复述历史对话，除非用户明确要求总结或追问上文。"
            "只有用户明确询问实验室安全、状态、风险、传感器、摄像头、报警、温湿度、处置建议，或用户上传图片/文件要求分析时，才结合 LabSafe 状态和知识库。"
            "如果当前存在 danger 级真实危险，即使用户没有询问安全，也只用一句话先提醒危险，再回答用户问题。"
            "如果只是 notice/warning 且用户没有问安全，不要主动提及这些状态。"
            "所有危险动作必须建议人工确认，不能声称已经拨号、报警或操作硬件。"
            "PPE 缺失检测第一版不启用。除非用户明确询问 PPE、实验服、手套、护目镜或口罩，"
            "否则不要把未穿戴 PPE 作为风险点、建议、提醒或结论，也不要主动说明 PPE 已禁用。"
            "如果用户请求联网搜索但搜索失败或没有结果，必须明确说明未取得网页结果，不能编造实时新闻、来源或声称已完成搜索。"
            "如果联网搜索成功，回答中要用搜索结果标题和链接说明依据。"
            "如果 vision_input.sent_to_model=true，说明你已收到图片，可以直接观察图片内容；"
            "但必须区分你的视觉观察、本地目标检测结果和实时传感器状态。"
            "如果没有视觉输入，只能基于本地图像检测结果、图片尺寸、用户问题和知识库回答；不能编造未检测到的细节。"
            "当 camera_snapshot_requested=true 时，后端会通过 attachments 字段返回当前摄像头图片 URL；"
            "你应明确告诉用户已附上当前摄像头画面，并基于 detections、帧时间和火灾状态描述，不要说无法回传图像。"
            "最终答案要有信息量，给出关键依据；普通问题可以简洁，实验室状态/图片/文件分析不要过度简略。"
        )
        if deep_thinking:
            system += (
                "本次用户开启了深度思考模式：请在内部做更充分的问题拆解、事实核对和边界检查，"
                "但最终答案不要展示完整推理过程，只给结论、关键依据和必要步骤。"
            )

        context = {
            "user_message": message,
            "intent": {
                "safety_related": bool(safety_related),
                "camera_snapshot_requested": bool(camera_requested),
                "has_uploads": bool(upload_context),
                "web_search_requested": self._search_was_requested(search_result),
            },
            "uploads": self._safe_upload_context(upload_context or [], include_ppe=ppe_requested),
            "vision_input": {
                "sent_to_model": bool(vision_images),
                "image_count": len(vision_images or []),
                "images": self._public_vision_images(vision_images or []),
            },
            "conversation_memory": self._public_conversation_memory(conversation_memory or []),
            "knowledge_hits": self._public_knowledge_hits(knowledge_hits or []),
            "web_search": self._public_search_result(search_result),
        }
        if camera_requested:
            context["camera_snapshot"] = {
                "returned_as_attachment": True,
                "attachment_title": "当前摄像头画面",
                "preferred_url": "/api/camera/usb-camera/snapshot/detect",
                "fallback_url": "/api/camera/usb-camera/snapshot",
            }
        if safety_related:
            context["lab_state"] = {
                "risk": self._decision_for_prompt(decision, include_ppe=ppe_requested),
                "system_status": self._compact_status(snapshot.get("status")),
                "detections": self._compact_detections(snapshot.get("detections"), include_ppe=ppe_requested),
                "emergency_call": self._compact_emergency_call(snapshot.get("emergency_call")),
            }

        user = (
            "请根据以下已检索/已调用工具上下文回答用户。"
            "如果上下文不足，请说明限制，不要编造。\n\n"
            + json.dumps(context, ensure_ascii=False, default=str)
        )
        user_content = user
        if vision_images:
            user_content = [{"type": "text", "text": user}]
            for image in vision_images:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": image["data_url"]},
                })
        return [{"role": "system", "content": system}, {"role": "user", "content": user_content}]

    def _rule_reply(
        self,
        message,
        decision,
        snapshot,
        safety_related,
        model_error,
        search_result=None,
        upload_context=None,
        camera_requested=False,
        knowledge_hits=None,
        vision_required=False,
        conversation_memory=None,
    ):
        if camera_requested:
            return self._camera_rule_reply(snapshot, decision, model_error, vision_required=vision_required)
        if upload_context:
            return self._upload_rule_reply(upload_context, decision, safety_related, model_error, vision_required=vision_required)
        if not safety_related:
            if conversation_memory and self._is_memory_question(message):
                return self._memory_rule_reply(conversation_memory, model_error)
            if search_result and search_result.get("success"):
                lines = ["模型暂时不可用，已完成联网搜索。搜索结果如下："]
                for item in search_result.get("results", [])[:5]:
                    lines.append(f"- {item.get('title', '')}: {item.get('url', '')}")
                return "\n".join(lines)
            if self._search_was_requested(search_result):
                return (
                    "联网搜索暂时失败，未取得网页结果。"
                    f"错误：{search_result.get('error', 'unknown') if search_result else 'unknown'}。"
                    "我不能确认最新信息，请稍后重试或切换网络后再试。"
                )
            if self._is_greeting(message):
                return "你好，我是 LabSafe Agent。"
            return (
                "当前大模型接口暂时不可用，通用问题无法完整回答。"
                "我仍可基于本地规则回答实验室安全、温湿度、摄像头和报警状态。"
            )

        fire_state = (snapshot.get("status") or {}).get("fire_state") or {}
        detection = snapshot.get("detections") or {}
        temp = fire_state.get("temperature")
        humidity = fire_state.get("humidity")
        frame_version = detection.get("frame_version")
        lines = [
            f"当前风险等级：{decision['risk_level']}。",
            f"判断依据：{decision['reason']}。",
        ]
        if temp is not None or humidity is not None:
            lines.append(f"环境状态：温度 {temp}°C，湿度 {humidity}%。")
        if frame_version is not None:
            detections = detection.get("detections") or []
            lines.append(
                f"摄像头检测：帧号 {frame_version}，火灾报警 {bool(detection.get('fire_alarm'))}，"
                f"检测目标 {self._summarize_detections(detections)}。"
            )
        if knowledge_hits:
            titles = "；".join(hit.get("title", "") for hit in knowledge_hits[:3] if hit.get("title"))
            if titles:
                lines.append(f"本地知识库依据：{titles}。")
        if decision.get("recommendations"):
            lines.append("建议：" + "；".join(decision["recommendations"]))
        if model_error == "rules_only selected":
            lines.append("当前使用规则模式，未调用外部模型。")
        else:
            lines.append(f"模型回退原因：{model_error or '模型不可用'}。")
        return "\n".join(lines)

    def _camera_rule_reply(self, snapshot, decision, model_error, vision_required=False):
        detection = snapshot.get("detections") or {}
        fire_state = (snapshot.get("status") or {}).get("fire_state") or {}
        lines = ["已返回当前摄像头画面附件。"]
        lines.append(
            f"当前检测帧：{detection.get('frame_version') or '暂无'}，"
            f"延迟约 {detection.get('latency_ms') or '未知'}ms，"
            f"火灾报警：{bool(detection.get('fire_alarm'))}。"
        )
        lines.append(f"画面检测目标：{self._summarize_detections(detection.get('detections') or [])}。")
        lines.append(f"环境状态：温度 {fire_state.get('temperature')}°C，湿度 {fire_state.get('humidity')}%。")
        lines.append(f"风险等级：{decision['risk_level']}；依据：{decision['reason']}。")
        if vision_required and model_error and model_error != "rules_only selected":
            lines.append("视觉模型暂时不可用，以上仅基于本地检测结果和实时状态。")
        if model_error and model_error != "rules_only selected":
            lines.append(f"模型回退原因：{model_error}。")
        return "\n".join(lines)

    def _upload_rule_reply(self, upload_context, decision, safety_related, model_error, vision_required=False):
        lines = ["已读取你上传的附件。"]
        for item in upload_context:
            name = item.get("name") or item.get("file_id")
            if item.get("kind") == "image":
                analysis = item.get("analysis") or {}
                local = (analysis.get("local_detection") or {}).get("data") or {}
                detections = local.get("detections") or []
                lines.append(
                    f"- {name}: 图片 {analysis.get('width', '?')}x{analysis.get('height', '?')}，"
                    f"本地检测目标 {self._summarize_detections(detections)}。"
                )
            elif item.get("text"):
                preview = str(item.get("text") or "").replace("\n", " ")[:180]
                lines.append(f"- {name}: 已抽取文本，开头为“{preview}”。")
            else:
                lines.append(f"- {name}: {item.get('parse_status') or '已保存'}。")
        if safety_related:
            lines.append(f"当前实验室风险等级：{decision['risk_level']}；依据：{decision['reason']}。")
        if vision_required and model_error and model_error != "rules_only selected":
            lines.append("视觉模型暂时不可用，图片细节只依据本地目标检测和文件元数据，不能作复杂视觉判断。")
        if model_error == "rules_only selected":
            lines.append("当前使用规则模式，未调用外部模型；复杂文件理解需要模型恢复后更完整。")
        elif model_error:
            lines.append(f"模型回退原因：{model_error}。")
        return "\n".join(lines)

    def _memory_config(self):
        return self.config.get("conversation_memory") or {}

    def _conversation_memory(self, session_id):
        config = self._memory_config()
        if not config.get("enabled", True):
            return []
        try:
            return self.state_store.conversation_memory(
                session_id=session_id,
                limit=int(config.get("max_turns", 8)),
                max_chars=int(config.get("max_chars", 6000)),
                max_item_chars=int(config.get("max_item_chars", 900)),
            )
        except Exception as e:
            log_event(self.logger, "conversation_memory_failed", session_id=session_id, error=str(e))
            return []

    @staticmethod
    def _public_conversation_memory(items):
        public = []
        for item in items or []:
            public.append({
                "trace_id": item.get("trace_id"),
                "sender": item.get("sender"),
                "user": item.get("user"),
                "assistant": item.get("assistant"),
                "risk_level": item.get("risk_level"),
            })
        return public

    @staticmethod
    def _memory_rule_reply(conversation_memory, model_error):
        recent = list(conversation_memory or [])[-3:]
        if not recent:
            return "当前会话里还没有可用的上下文记忆。"
        lines = ["根据当前会话记忆："]
        for item in recent:
            user_text = str(item.get("user") or "").strip()
            assistant_text = str(item.get("assistant") or "").strip()
            if user_text:
                lines.append(f"- 你说过：{user_text}")
            if assistant_text:
                lines.append(f"  我回复：{assistant_text[:240]}")
        if model_error and model_error != "rules_only selected":
            lines.append(f"当前模型暂时不可用，以上是本地 SQLite 记忆回退结果。模型错误：{model_error}")
        return "\n".join(lines)

    def _vision_images(self, camera_image_result, upload_context):
        config = self.config.get("vision") or {}
        if not config.get("enabled", True):
            return []
        max_images = int(config.get("max_images", 3))
        images = []

        if camera_image_result and camera_image_result.success:
            data = camera_image_result.data or {}
            item = self._image_bytes_to_vision_item(
                title="当前摄像头画面",
                source="camera",
                raw=data.get("bytes") or b"",
                mime=data.get("mime") or "image/jpeg",
                origin=data.get("path") or "/api/camera/usb-camera/snapshot",
            )
            if item:
                images.append(item)

        for upload in upload_context or []:
            if len(images) >= max_images:
                break
            if upload.get("kind") != "image":
                continue
            file_path = upload.get("file_path")
            if not file_path or not os.path.exists(file_path):
                continue
            try:
                with open(file_path, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            mime = upload.get("mime_type") or mimetypes.guess_type(file_path)[0] or "image/jpeg"
            item = self._image_bytes_to_vision_item(
                title=upload.get("name") or "上传图片",
                source="upload",
                raw=raw,
                mime=mime,
                origin=upload.get("file_id") or "",
            )
            if item:
                images.append(item)
        return images[:max_images]

    def _image_bytes_to_vision_item(self, title, source, raw, mime, origin=""):
        if not raw:
            return None
        prepared, prepared_mime = self._prepare_image_bytes(raw, mime)
        if not prepared:
            return None
        encoded = base64.b64encode(prepared).decode("ascii")
        return {
            "title": title,
            "source": source,
            "origin": origin,
            "mime": prepared_mime,
            "size_bytes": len(prepared),
            "original_size_bytes": len(raw),
            "data_url": f"data:{prepared_mime};base64,{encoded}",
        }

    def _prepare_image_bytes(self, raw, mime):
        config = self.config.get("vision") or {}
        max_bytes = int(config.get("max_image_bytes", 4000000))
        mime = str(mime or "image/jpeg").split(";")[0].strip() or "image/jpeg"
        if len(raw) <= max_bytes and mime.startswith("image/"):
            return raw, mime
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(raw, np.uint8)
            image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if image is None:
                return None, mime
            max_side = int(config.get("max_side_px", 1280))
            h, w = image.shape[:2]
            scale = min(1.0, float(max_side) / max(h, w))
            if scale < 1.0:
                image = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))))
            start_quality = int(config.get("jpeg_quality", 82))
            last = None
            for quality in (start_quality, 75, 68, 60, 52, 45):
                ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
                if not ok:
                    continue
                data = buf.tobytes()
                last = data
                if len(data) <= max_bytes:
                    return data, "image/jpeg"
            if last and len(last) <= max_bytes:
                return last, "image/jpeg"
        except Exception:
            return None, mime
        return None, mime

    def _safe_upload_context(self, upload_context, include_ppe=False):
        safe = []
        for item in upload_context or []:
            entry = {key: value for key, value in item.items() if key != "file_path"}
            if not include_ppe and entry.get("analysis"):
                entry["analysis"] = self._filter_ppe_from_analysis(entry.get("analysis") or {})
            safe.append(entry)
        return safe

    @staticmethod
    def _decision_for_prompt(decision, include_ppe=False):
        data = dict(decision or {})
        if not include_ppe:
            data.pop("ignored_ppe_classes", None)
        return data

    @staticmethod
    def _filter_ppe_from_analysis(analysis):
        data = deepcopy(analysis or {})
        try:
            local_data = data.get("local_detection", {}).get("data", {})
            detections = local_data.get("detections")
            if isinstance(detections, list):
                local_data["detections"] = [
                    det for det in detections
                    if (det.get("class_name") or det.get("label")) not in PPE_CLASSES
                ]
        except Exception:
            pass
        return data

    @staticmethod
    def _public_vision_images(vision_images):
        return [
            {
                "title": item.get("title"),
                "source": item.get("source"),
                "origin": item.get("origin"),
                "mime": item.get("mime"),
                "size_bytes": item.get("size_bytes"),
                "original_size_bytes": item.get("original_size_bytes"),
            }
            for item in vision_images or []
        ]

    def _public_vision(self, vision_images, model_result):
        return {
            "sent_to_model": bool(vision_images),
            "image_count": len(vision_images or []),
            "images": self._public_vision_images(vision_images or []),
            "model_success": bool(getattr(model_result, "success", False)) if vision_images else False,
            "provider": getattr(model_result, "provider", "") if vision_images else "",
            "error": getattr(model_result, "error", "") if vision_images and not getattr(model_result, "success", False) else "",
        }

    def _knowledge_hits(self, message, safety_related, upload_context):
        if not (safety_related or upload_context):
            return []
        text_parts = [message]
        for item in upload_context or []:
            text_parts.append(item.get("name") or "")
            text_parts.append(str(item.get("text") or "")[:800])
        query = "\n".join(part for part in text_parts if part)
        limit = int((self.config.get("knowledge") or {}).get("max_hits", 5))
        try:
            return self.knowledge.search(query, limit=limit)
        except Exception as e:
            log_event(self.logger, "knowledge_search_failed", error=str(e))
            return []

    @staticmethod
    def _search_was_requested(search_result):
        if not search_result:
            return False
        error = search_result.get("error", "")
        return bool(search_result.get("query")) and error != "not requested"

    def _maybe_search(self, trace_id, message, web_search):
        config = self.config.get("web_search") or {}
        if not web_search:
            return {"success": False, "query": "", "results": [], "error": "not requested", "latency_ms": 0}
        if not config.get("enabled", True):
            return {"success": False, "query": message, "results": [], "error": "web search disabled", "latency_ms": 0}
        started = time.time()
        result = self.web_search_tool.search(message)
        if self.state_store is not None:
            self.state_store.record_tool_call(
                trace_id,
                "web_search",
                started,
                result.get("latency_ms", 0),
                result.get("success", False),
                result.get("error", ""),
            )
        return result

    @staticmethod
    def _public_search_result(result):
        result = result or {}
        return {
            "success": bool(result.get("success")),
            "query": result.get("query", ""),
            "original_query": result.get("original_query", ""),
            "results": result.get("results", [])[:5],
            "error": result.get("error", ""),
            "latency_ms": result.get("latency_ms", 0),
        }

    @staticmethod
    def _public_knowledge_hits(hits):
        public = []
        for hit in hits or []:
            public.append({
                "rank": hit.get("rank"),
                "source": hit.get("source"),
                "title": hit.get("title"),
                "content": hit.get("content"),
            })
        return public

    def _tool_context(
        self,
        snapshot,
        decision,
        upload_context,
        knowledge_hits,
        search_result,
        camera_requested,
        camera_image_result=None,
        vision_images=None,
        conversation_memory=None,
    ):
        health = snapshot.get("tool_health") or {}
        return {
            "snapshot_tools": {
                name: {
                    "success": info.get("success"),
                    "latency_ms": info.get("latency_ms"),
                    "error": info.get("error", ""),
                }
                for name, info in health.items()
            },
            "risk_level": decision.get("risk_level"),
            "uploads": [{"file_id": item.get("file_id"), "name": item.get("name"), "kind": item.get("kind")} for item in upload_context or []],
            "knowledge_hit_count": len(knowledge_hits or []),
            "web_search": {
                "requested": self._search_was_requested(search_result),
                "success": bool((search_result or {}).get("success")),
                "error": (search_result or {}).get("error", ""),
            },
            "camera_snapshot_requested": bool(camera_requested),
            "camera_snapshot_fetch": {
                "success": bool(camera_image_result.success) if camera_image_result else False,
                "latency_ms": camera_image_result.latency_ms if camera_image_result else 0,
                "error": camera_image_result.error if camera_image_result else "",
                "size_bytes": (camera_image_result.data or {}).get("size_bytes") if camera_image_result else 0,
            },
            "vision": {
                "sent_to_model": bool(vision_images),
                "image_count": len(vision_images or []),
                "images": self._public_vision_images(vision_images or []),
            },
            "conversation_memory": {
                "enabled": self._memory_config().get("enabled", True),
                "turn_count": len(conversation_memory or []),
            },
        }

    @staticmethod
    def _compact_detections(detection, include_ppe=False):
        detection = detection or {}
        detections = detection.get("detections", [])[:10]
        if not include_ppe:
            detections = [
                det for det in detections
                if (det.get("class_name") or det.get("label")) not in PPE_CLASSES
            ]
        return {
            "frame_version": detection.get("frame_version"),
            "timestamp": detection.get("timestamp"),
            "latency_ms": detection.get("latency_ms"),
            "fire_alarm": detection.get("fire_alarm"),
            "alarm_reason": detection.get("alarm_reason"),
            "detections": detections,
        }

    @staticmethod
    def _compact_status(status):
        status = status or {}
        fire_state = status.get("fire_state") or {}
        notifications = status.get("notifications") or {}
        return {
            "status": status.get("status"),
            "alert_level": status.get("alert_level"),
            "fire_detection": status.get("fire_detection"),
            "fire_state": {
                "alarm_active": fire_state.get("alarm_active"),
                "alarm_classes": fire_state.get("alarm_classes"),
                "alarm_reason": fire_state.get("alarm_reason"),
                "detecting": fire_state.get("detecting"),
                "flame_detected": fire_state.get("flame_detected"),
                "humidity": fire_state.get("humidity"),
                "last_check": fire_state.get("last_check"),
                "sensor_error": fire_state.get("sensor_error"),
                "sensor_status": fire_state.get("sensor_status"),
                "sensor_updated_at": fire_state.get("sensor_updated_at"),
                "temperature": fire_state.get("temperature"),
            },
            "notifications": {
                "sound": bool(notifications.get("sound")),
                "email_enabled": bool(notifications.get("email")),
            },
        }

    @staticmethod
    def _compact_emergency_call(status):
        status = status or {}
        signal = status.get("signal") or {}
        return {
            "success": status.get("success"),
            "enabled": status.get("enabled"),
            "ready": status.get("ready"),
            "state": status.get("state"),
            "message": status.get("message"),
            "auto_call": status.get("auto_call"),
            "call_active": status.get("call_active"),
            "dialing": status.get("dialing"),
            "in_call": status.get("in_call"),
            "last_error": status.get("last_error"),
            "port_exists": status.get("port_exists"),
            "signal": {
                "text": signal.get("text"),
                "dbm": signal.get("dbm"),
            },
            "sim_detected": status.get("sim_detected"),
            "sim_status": status.get("sim_status"),
        }

    @staticmethod
    def _summarize_detections(detections, include_ppe=False):
        if not detections:
            return "未检测到目标"
        counts = {}
        for det in detections:
            name = det.get("class_name") or det.get("label") or "unknown"
            if not include_ppe and name in PPE_CLASSES:
                continue
            counts[name] = counts.get(name, 0) + 1
        if not counts:
            return "未检测到目标"
        return "、".join(f"{name} x{count}" for name, count in counts.items())

    @staticmethod
    def _has_image_upload(upload_context):
        return any(item.get("kind") == "image" for item in upload_context or [])

    @staticmethod
    def _attachments_from_uploads(upload_context):
        attachments = []
        for item in upload_context or []:
            if item.get("kind") == "image":
                attachments.append({
                    "type": "image",
                    "title": item.get("name") or "上传图片",
                    "url": item.get("preview_url"),
                    "thumbnail_url": item.get("thumbnail_url"),
                    "mime": item.get("mime_type"),
                    "source": "upload",
                    "file_id": item.get("file_id"),
                })
            else:
                attachments.append({
                    "type": "file",
                    "title": item.get("name") or "上传文件",
                    "url": item.get("preview_url"),
                    "mime": item.get("mime_type"),
                    "source": "upload",
                    "file_id": item.get("file_id"),
                })
        return attachments

    @staticmethod
    def _is_safety_related(message):
        return any(keyword in message for keyword in SAFETY_KEYWORDS)

    @staticmethod
    def _is_camera_request(message):
        text = message or ""
        if any(keyword in text for keyword in CAMERA_REQUEST_KEYWORDS):
            return True
        return (
            any(keyword in text for keyword in CAMERA_VISUAL_TERMS)
            and any(keyword in text for keyword in CAMERA_SCOPE_TERMS)
        )

    @staticmethod
    def _is_search_request(message):
        text = (message or "").lower()
        return any(keyword in text for keyword in SEARCH_REQUEST_KEYWORDS)

    @staticmethod
    def _is_ppe_request(message):
        text = message or ""
        lowered = text.lower()
        return any(keyword in text or keyword.lower() in lowered for keyword in PPE_QUERY_KEYWORDS)

    @staticmethod
    def _is_memory_question(message):
        text = message or ""
        keywords = (
            "刚才", "之前", "上面", "前面", "上一", "上次", "我说的", "我提到",
            "记住", "记得", "继续", "那个", "这个", "它", "前文", "上下文",
        )
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def _is_greeting(message):
        normalized = (message or "").strip().lower()
        return normalized in {"你好", "您好", "hello", "hi", "hey", "在吗", "你在吗"}

    @staticmethod
    def _normalize_ids(values):
        if not values:
            return []
        if isinstance(values, str):
            values = [values]
        return [str(value).strip() for value in values if str(value).strip()]

    @staticmethod
    def _normalize_session_id(session_id, sender="user"):
        raw = str(session_id or "").strip()
        if not raw:
            raw = f"default:{sender or 'user'}"
        raw = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw)
        raw = raw.strip("._:-")
        return (raw or "default:user")[:96]

    @staticmethod
    def _trace_id():
        return uuid.uuid4().hex[:16]

    @staticmethod
    def _empty_response(trace_id, reply, success=True, session_id="default:user"):
        return {
            "reply": reply,
            "risk_level": "normal",
            "reason": "empty message" if not success else "agent disabled",
            "recommendations": [],
            "requires_confirmation": False,
            "proposed_actions": [],
            "attachments": [],
            "tool_context": {},
            "knowledge_hits": [],
            "search": {"success": False, "query": "", "original_query": "", "results": [], "error": "not requested", "latency_ms": 0},
            "web_search": {"success": False, "query": "", "original_query": "", "results": [], "error": "not requested", "latency_ms": 0},
            "trace_id": trace_id,
            "session_id": session_id,
            "conversation_memory": {"enabled": True, "turn_count": 0},
            "success": success,
        }
