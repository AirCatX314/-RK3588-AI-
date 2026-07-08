"""Action guard for high-risk agent actions."""

import secrets
import time


HIGH_RISK_ACTIONS = {"start_emergency_call", "send_emergency_alert"}


class ActionGuard:
    def __init__(self, state_store, tools, config):
        self.state_store = state_store
        self.tools = tools
        safety = (config or {}).get("safety", {})
        self.ttl_seconds = int(safety.get("confirm_ttl_seconds", 300))

    def propose(self, trace_id, action_type, title, payload, reason):
        if action_type not in HIGH_RISK_ACTIONS:
            return {
                "action_type": action_type,
                "title": title,
                "payload": payload or {},
                "requires_confirmation": False,
            }
        token = secrets.token_urlsafe(24)
        self.state_store.create_pending_action(
            token, trace_id, action_type, title, payload or {}, reason, self.ttl_seconds
        )
        return {
            "action_type": action_type,
            "title": title,
            "reason": reason,
            "requires_confirmation": True,
            "confirmation_token": token,
            "expires_in_seconds": self.ttl_seconds,
        }

    def confirm(self, token):
        action = self.state_store.get_pending_action(token)
        if not action:
            return {"success": False, "message": "确认 token 不存在"}
        if action["status"] != "pending":
            return {"success": False, "message": f"动作已处理: {action['status']}"}
        if time.time() > float(action["expires_at"]):
            self.state_store.finish_pending_action(token, "expired", {"success": False, "message": "token expired"})
            return {"success": False, "message": "确认 token 已过期"}

        trace_id = action["trace_id"]
        payload = action.get("payload") or {}
        if action["action_type"] == "start_emergency_call":
            result = self.tools.start_emergency_call(trace_id, payload.get("reason") or action["reason"])
        elif action["action_type"] == "send_emergency_alert":
            result = self.tools.send_emergency_alert(
                trace_id,
                payload.get("lab_name", "实验室1"),
                payload.get("type", "Agent紧急建议"),
                payload.get("message", action["reason"] or "Agent 建议触发紧急报警"),
            )
        else:
            result = None

        if result is None:
            final = {"success": False, "message": "未知动作类型"}
        else:
            final = result.data if result.success else {"success": False, "message": result.error}
        self.state_store.finish_pending_action(token, "executed" if final.get("success") else "failed", final)
        return final
