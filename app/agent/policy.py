"""Deterministic safety policy for LabSafe agent risk levels."""

from datetime import datetime


PPE_CLASSES = {"lab_coat", "face_shield", "gloves", "goggles", "mask"}


class SafetyPolicy:
    def __init__(self, config):
        safety = (config or {}).get("safety", {})
        self.temperature_warning_c = float(safety.get("temperature_warning_c", 40.0))
        self.humidity_low_percent = float(safety.get("humidity_low_percent", 15.0))
        self.humidity_high_percent = float(safety.get("humidity_high_percent", 85.0))
        self.camera_stale_seconds = float(safety.get("camera_stale_seconds", 10.0))

    def evaluate(self, snapshot):
        status = snapshot.get("status") or {}
        fire_state = status.get("fire_state") or {}
        detection = snapshot.get("detections") or {}
        emergency = snapshot.get("emergency_call") or {}
        tool_health = snapshot.get("tool_health") or {}
        reasons = []
        recommendations = []

        fire_alarm = bool(detection.get("fire_alarm") or fire_state.get("alarm_active"))
        if fire_alarm:
            reason = detection.get("alarm_reason") or fire_state.get("alarm_reason") or "后端已确认火灾或烟雾报警"
            return {
                "risk_level": "danger",
                "reason": reason,
                "recommendations": [
                    "立即远离疑似危险区域，按实验室应急预案疏散。",
                    "确认现场安全后，可人工确认拨打应急电话。",
                    "不要让人员返回火源或烟雾区域取物。",
                ],
                "ignored_ppe_classes": self._ppe_present(detection),
            }

        temp = self._to_float(fire_state.get("temperature"))
        humidity = self._to_float(fire_state.get("humidity"))
        detecting_fire_candidate = bool(fire_state.get("detecting"))

        risk_level = "normal"
        if temp is not None and temp >= self.temperature_warning_c:
            risk_level = "warning"
            reasons.append(f"温度 {temp:.1f}°C 超过阈值 {self.temperature_warning_c:.1f}°C")
            recommendations.append("检查热源、电源和通风状态，必要时停止实验。")
            if detecting_fire_candidate:
                reasons.append("视觉链路正在检测火焰/烟雾候选，但尚未确认报警")

        if humidity is not None and (
            humidity < self.humidity_low_percent or humidity > self.humidity_high_percent
        ):
            risk_level = self._max_risk(risk_level, "warning")
            reasons.append(f"湿度 {humidity:.1f}% 超出建议范围")
            recommendations.append("检查实验材料对湿度的要求，必要时调整通风或除湿。")

        sensor_status = str(fire_state.get("sensor_status") or "unknown")
        if sensor_status != "ok":
            risk_level = self._max_risk(risk_level, "notice")
            reasons.append(f"DHT11 状态异常: {sensor_status}")
            recommendations.append("检查 DHT11 服务和 /tmp/labsafe_dht11.json 更新时间。")

        frame_version = self._to_int(detection.get("frame_version"))
        if frame_version is None or frame_version <= 0:
            risk_level = self._max_risk(risk_level, "warning")
            reasons.append("摄像头检测帧号未增长或暂无检测帧")
            recommendations.append("检查 Qt 采集进程和 gst-launch 是否正常吐帧。")
        elif self._timestamp_stale(detection.get("timestamp")):
            risk_level = self._max_risk(risk_level, "warning")
            reasons.append("检测结果时间戳已过期")
            recommendations.append("检查检测线程是否卡住。")

        if emergency.get("enabled", True) and not emergency.get("ready", True):
            risk_level = self._max_risk(risk_level, "warning")
            reasons.append(f"应急通话模块不可用: {emergency.get('message') or emergency.get('state')}")
            recommendations.append("检查 EC600N、SIM 状态和 ModemManager。")

        for name, health in tool_health.items():
            if not health.get("success"):
                risk_level = self._max_risk(risk_level, "notice")
                reasons.append(f"工具 {name} 暂时不可用")

        if not reasons:
            reasons.append("火灾报警未触发，温湿度和检测链路未发现高风险异常")
            recommendations.append("继续保持监控，危险动作需人工确认。")

        return {
            "risk_level": risk_level,
            "reason": "；".join(reasons),
            "recommendations": recommendations,
            "ignored_ppe_classes": self._ppe_present(detection),
        }

    def _timestamp_stale(self, value):
        if not value:
            return False
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            age = (datetime.now(dt.tzinfo) - dt).total_seconds()
            return age > self.camera_stale_seconds
        except Exception:
            return False

    def _ppe_present(self, detection):
        classes = set()
        for det in detection.get("detections") or []:
            name = det.get("class_name")
            if name in PPE_CLASSES:
                classes.add(name)
        return sorted(classes)

    @staticmethod
    def _to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _max_risk(current, candidate):
        order = {"normal": 0, "notice": 1, "warning": 2, "danger": 3}
        return candidate if order[candidate] > order[current] else current
