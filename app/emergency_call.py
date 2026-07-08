#!/usr/bin/env python3
"""Emergency phone call control through the EC600N AT serial port."""

import json
import os
import re
import select
import subprocess
import threading
import time
from datetime import datetime


DEFAULT_SETTINGS = {
    "enabled": True,
    "admin_phone": "",
    "at_port": "/dev/ttyUSB1",
    "auto_call": True,
    "auto_call_cooldown_seconds": 180,
}
FALLBACK_AT_PORT = "/dev/ttyUSB1"

_at_lock = threading.Lock()
_state_lock = threading.Lock()
_last_error = ""
_last_auto_attempt_ts = 0.0
_last_auto_reason = ""
_last_call_started_at = ""


def load_settings(config_file):
    settings = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(config_file):
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            call_config = config.get("emergency_call", {})
            if isinstance(call_config, dict):
                settings.update(call_config)
    except Exception as e:
        _set_last_error(f"读取应急通话配置失败: {e}")
    settings["enabled"] = bool(settings.get("enabled", True))
    settings["auto_call"] = bool(settings.get("auto_call", True))
    try:
        settings["auto_call_cooldown_seconds"] = max(
            0.0, float(settings.get("auto_call_cooldown_seconds", 180))
        )
    except Exception:
        settings["auto_call_cooldown_seconds"] = 180.0
    settings["admin_phone"] = str(settings.get("admin_phone") or DEFAULT_SETTINGS["admin_phone"]).strip()
    settings["at_port"] = str(settings.get("at_port") or DEFAULT_SETTINGS["at_port"]).strip()
    return settings


def default_config():
    return dict(DEFAULT_SETTINGS)


def _set_last_error(error):
    global _last_error
    with _state_lock:
        _last_error = error or ""


def _get_runtime_state():
    with _state_lock:
        return {
            "last_error": _last_error,
            "last_auto_attempt_at": (
                datetime.fromtimestamp(_last_auto_attempt_ts).isoformat()
                if _last_auto_attempt_ts
                else ""
            ),
            "last_auto_reason": _last_auto_reason,
            "last_call_started_at": _last_call_started_at,
        }


def _clean_phone(phone):
    phone = str(phone or "").strip()
    if re.fullmatch(r"\+?\d{3,20}", phone):
        return phone
    return ""


def _mask_phone(phone):
    phone = str(phone or "")
    if len(phone) < 8:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


def _port_candidates(settings):
    candidates = []
    for port in (settings.get("at_port"), DEFAULT_SETTINGS["at_port"], FALLBACK_AT_PORT):
        if port and port not in candidates:
            candidates.append(port)
    return candidates


def _resolve_port(settings):
    candidates = _port_candidates(settings)
    for port in candidates:
        if os.path.exists(port):
            return port
    return candidates[0] if candidates else FALLBACK_AT_PORT


def _configure_port(fd):
    import termios

    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] &= ~(termios.PARENB | termios.CSTOPB | termios.CSIZE)
    attrs[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
    attrs[3] = 0
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def _read_response(fd, timeout):
    deadline = time.time() + timeout
    chunks = []
    while time.time() < deadline:
        remaining = max(0.05, deadline - time.time())
        readable, _, _ = select.select([fd], [], [], min(0.2, remaining))
        if not readable:
            continue
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        if not chunk:
            continue
        chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8", "ignore")
        if any(token in text for token in ("\r\nOK\r\n", "\r\nERROR\r\n", "NO CARRIER", "+CME ERROR")):
            time.sleep(0.05)
            break
    return b"".join(chunks).decode("utf-8", "ignore")


def _send_at(settings, command, timeout=1.5):
    port = _resolve_port(settings)
    if not os.path.exists(port):
        error = f"AT 串口不存在: {port}"
        _set_last_error(error)
        return {"ok": False, "port": port, "command": command, "response": "", "error": error}

    with _at_lock:
        fd = None
        try:
            fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            _configure_port(fd)
            os.write(fd, (command.rstrip("\r\n") + "\r").encode("ascii", "ignore"))
            response = _read_response(fd, timeout)
            ok = "ERROR" not in response and "+CME ERROR" not in response
            if not ok:
                _set_last_error(response.strip() or f"AT 命令失败: {command}")
            return {
                "ok": ok,
                "port": port,
                "command": command,
                "response": response,
                "error": "" if ok else (response.strip() or f"AT 命令失败: {command}"),
            }
        except Exception as e:
            error = f"AT 命令异常 {command}: {e}"
            _set_last_error(error)
            return {"ok": False, "port": port, "command": command, "response": "", "error": error}
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass


def _parse_cpin(response):
    upper = (response or "").upper()
    if "+CME ERROR: 10" in upper or "SIM NOT INSERTED" in upper:
        return {"status": "missing", "raw": "NOT INSERTED"}
    match = re.search(r"\+CPIN:\s*([^\r\n]+)", response or "", re.IGNORECASE)
    if not match:
        return {"status": "unknown", "raw": ""}
    raw = match.group(1).strip().upper()
    if raw == "READY":
        return {"status": "ready", "raw": raw}
    if "NOT INSERTED" in raw or "NOT INSERT" in raw:
        return {"status": "missing", "raw": raw}
    if "PIN" in raw:
        return {"status": "pin_required", "raw": raw}
    if "PUK" in raw:
        return {"status": "puk_required", "raw": raw}
    return {"status": "unknown", "raw": raw}


def _parse_csq(response):
    match = re.search(r"\+CSQ:\s*(\d+)\s*,\s*(\d+)", response or "", re.IGNORECASE)
    if not match:
        return {"rssi": None, "ber": None, "dbm": None, "text": "unknown"}
    rssi = int(match.group(1))
    ber = int(match.group(2))
    dbm = None if rssi == 99 else -113 + (2 * rssi)
    if rssi == 99:
        text = "unknown"
    elif rssi <= 9:
        text = "weak"
    elif rssi <= 14:
        text = "fair"
    elif rssi <= 19:
        text = "good"
    else:
        text = "strong"
    return {"rssi": rssi, "ber": ber, "dbm": dbm, "text": text}


def _parse_qsimstat(response):
    match = re.search(r"\+QSIMSTAT:\s*(\d+)\s*,\s*(\d+)", response or "", re.IGNORECASE)
    if not match:
        return {"enabled": None, "inserted": None, "raw": ""}
    enabled = int(match.group(1))
    inserted = int(match.group(2))
    return {"enabled": enabled, "inserted": inserted, "raw": f"{enabled},{inserted}"}


def _parse_qsimdet(response):
    match = re.search(r"\+QSIMDET:\s*(\d+)\s*,\s*(\d+)", response or "", re.IGNORECASE)
    if not match:
        return {"enabled": None, "level": None, "raw": ""}
    enabled = int(match.group(1))
    level = int(match.group(2))
    return {"enabled": enabled, "level": level, "raw": f"{enabled},{level}"}


def _parse_clcc(response):
    calls = []
    for line in (response or "").replace("\r", "\n").splitlines():
        line = line.strip()
        if not line.upper().startswith("+CLCC:"):
            continue
        payload = line.split(":", 1)[1].strip()
        parts = [p.strip().strip('"') for p in payload.split(",")]
        if len(parts) < 3:
            continue
        try:
            stat = int(parts[2])
        except Exception:
            stat = -1
        calls.append({
            "index": parts[0],
            "direction": parts[1],
            "state_code": stat,
            "number": parts[5] if len(parts) > 5 else "",
        })
    state_codes = {c["state_code"] for c in calls}
    return {
        "calls": calls,
        "call_active": bool(state_codes & {0, 1, 2, 3, 4, 5}),
        "dialing": bool(state_codes & {2, 3}),
        "ringing": bool(state_codes & {4, 5}),
        "in_call": bool(state_codes & {0, 1}),
    }


def _parse_cpas(response):
    match = re.search(r"\+CPAS:\s*(\d+)", response or "", re.IGNORECASE)
    code = int(match.group(1)) if match else None
    return {
        "code": code,
        "ringing": code == 3,
        "in_call": code == 4,
    }


def _run_mmcli(args, timeout=8):
    try:
        proc = subprocess.run(
            ["mmcli"] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "output": proc.stdout or "",
            "error": "" if proc.returncode == 0 else (proc.stdout or "").strip(),
        }
    except FileNotFoundError:
        return {"ok": False, "output": "", "error": "mmcli not found"}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)}


def _field_value(text, label):
    pattern = rf"{re.escape(label)}:\s*([^\n\r]+)"
    match = re.search(pattern, text or "", re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _parse_mmcli_calls(calls_text):
    calls = []
    for line in (calls_text or "").splitlines():
        match = re.search(
            r"(/org/freedesktop/ModemManager1/Call/(\d+))\s+(\S+)\s+\(([^)]+)\)",
            line,
        )
        if not match:
            continue
        calls.append({
            "path": match.group(1),
            "index": match.group(2),
            "direction": match.group(3),
            "state": match.group(4).strip().lower(),
        })
    return calls


def _parse_mmcli_status(settings, base_status):
    modem = _run_mmcli(["-m", "any"], timeout=8)
    if not modem["ok"]:
        return None
    modem_text = modem["output"]
    state = _field_value(modem_text, "state")
    registration = _field_value(modem_text, "registration")
    access_tech = _field_value(modem_text, "access tech")
    operator_name = _field_value(modem_text, "operator name")
    signal_quality = _field_value(modem_text, "signal quality")
    sim_path = _field_value(modem_text, "primary sim path")

    sim = _run_mmcli(["-i", "any"], timeout=8)
    sim_text = sim["output"] if sim["ok"] else ""
    iccid = _field_value(sim_text, "iccid")
    imsi = _field_value(sim_text, "imsi")

    voice = _run_mmcli(["-m", "any", "--voice-status"], timeout=8)
    voice_text = voice["output"] if voice["ok"] else ""
    emergency_only = "emergency only: yes" in voice_text.lower()

    calls_result = _run_mmcli(["-m", "any", "--voice-list-calls"], timeout=8)
    calls_text = calls_result["output"] if calls_result["ok"] else ""
    calls = _parse_mmcli_calls(calls_text)
    active_calls = [c for c in calls if c.get("state") != "terminated"]
    dialing = any(c.get("state") in ("dialing", "ringing-out") for c in active_calls)
    ringing = any(c.get("state") in ("ringing-in", "waiting") for c in active_calls)
    in_call = any(c.get("state") in ("active", "held") for c in active_calls)
    call_active = bool(active_calls)

    status = dict(base_status)
    status.update({
        "manager": "modemmanager",
        "modem_state": state,
        "registration": registration,
        "access_tech": access_tech,
        "operator_name": operator_name,
        "sim_path": sim_path,
        "iccid": iccid,
        "imsi": imsi,
        "voice_emergency_only": emergency_only,
        "calls": calls,
        "call_active": call_active,
        "dialing": dialing,
        "ringing": ringing,
        "in_call": in_call,
    })
    if signal_quality:
        match = re.search(r"(\d+)%", signal_quality)
        percent = int(match.group(1)) if match else None
        status["signal"] = {"rssi": None, "ber": None, "dbm": None, "text": signal_quality, "percent": percent}

    if sim_path and iccid:
        status["sim_status"] = "ready"
        status["sim_raw"] = "READY"
        status["sim_detected"] = True
        status["sim_detection"] = {"enabled": 1, "inserted": 1, "raw": "modemmanager"}
        if emergency_only:
            status.update({"state": "emergency_only", "ready": False, "message": "仅限紧急呼叫"})
        elif call_active:
            status.update({"state": "in_call", "ready": True, "message": "通话中"})
        elif state in ("registered", "connected") or registration in ("home", "roaming"):
            status.update({"state": "idle", "ready": True, "message": "待机"})
        else:
            status.update({"state": "registering", "ready": False, "message": "SIM 已识别，等待网络注册"})
        return status

    if "sim-missing" in modem_text.lower() or "sim-missing" in state.lower():
        status.update({"state": "sim_missing", "sim_status": "missing", "message": "SIM 卡缺失"})
        return status
    return None


def _start_call_mmcli(phone):
    create = _run_mmcli(["-m", "any", f"--voice-create-call=number={phone}"], timeout=12)
    if not create["ok"]:
        return {"used": True, "success": False, "message": create["error"] or "创建语音呼叫失败"}
    match = re.search(r"(/org/freedesktop/ModemManager1/Call/\d+)", create["output"])
    if not match:
        return {"used": True, "success": False, "message": create["output"].strip() or "未获得呼叫路径"}
    call_path = match.group(1)
    start = _run_mmcli(["-o", call_path, "--start"], timeout=12)
    if not start["ok"]:
        return {"used": True, "success": False, "message": start["error"] or "启动语音呼叫失败"}
    return {"used": True, "success": True, "message": "已拨打管理员", "call_path": call_path}


def _hangup_call_mmcli():
    result = _run_mmcli(["-m", "any", "--voice-hangup-all"], timeout=8)
    if result["ok"]:
        calls_result = _run_mmcli(["-m", "any", "--voice-list-calls"], timeout=8)
        for call in _parse_mmcli_calls(calls_result["output"] if calls_result["ok"] else ""):
            if call.get("state") == "terminated":
                _run_mmcli(["-m", "any", f"--voice-delete-call={call['path']}"], timeout=8)
        return {"used": True, "success": True, "message": "已发送挂断命令"}
    return {"used": True, "success": False, "message": result["error"] or "挂断失败"}


def get_status(config_file):
    settings = load_settings(config_file)
    runtime = _get_runtime_state()
    port = _resolve_port(settings)
    status = {
        "success": True,
        "enabled": settings["enabled"],
        "auto_call": settings["auto_call"],
        "admin_phone": _mask_phone(settings["admin_phone"]),
        "port": port,
        "port_exists": os.path.exists(port),
        "state": "unknown",
        "ready": False,
        "sim_status": "unknown",
        "sim_raw": "",
        "sim_detected": None,
        "sim_detection": {"enabled": None, "inserted": None, "raw": ""},
        "sim_detect_config": {"enabled": None, "level": None, "raw": ""},
        "signal": {"rssi": None, "ber": None, "dbm": None, "text": "unknown"},
        "call_active": False,
        "dialing": False,
        "ringing": False,
        "in_call": False,
        "calls": [],
        "message": "未知",
    }
    status.update(runtime)

    if not settings["enabled"]:
        status.update({"state": "disabled", "message": "应急通话已禁用"})
        return status
    if not status["port_exists"]:
        status.update({"success": False, "state": "port_missing", "message": f"AT 串口不存在: {port}"})
        return status

    mm_status = _parse_mmcli_status(settings, status)
    if mm_status and mm_status.get("sim_status") == "ready":
        return mm_status

    cpin = _send_at(settings, "AT+CPIN?", timeout=1.5)
    if not cpin["ok"] and not cpin["response"]:
        status.update({
            "success": False,
            "state": "error",
            "message": cpin["error"] or "无法读取 SIM 状态",
            "last_error": cpin["error"],
        })
        return status

    sim = _parse_cpin(cpin["response"])
    status["sim_status"] = sim["status"]
    status["sim_raw"] = sim["raw"]

    qsimstat = _parse_qsimstat(_send_at(settings, "AT+QSIMSTAT?", timeout=1.2)["response"])
    qsimdet = _parse_qsimdet(_send_at(settings, "AT+QSIMDET?", timeout=1.2)["response"])
    status["sim_detection"] = qsimstat
    status["sim_detect_config"] = qsimdet
    if qsimstat["inserted"] is not None:
        status["sim_detected"] = bool(qsimstat["inserted"])

    csq = _send_at(settings, "AT+CSQ", timeout=1.2)
    status["signal"] = _parse_csq(csq["response"])
    clcc = _parse_clcc(_send_at(settings, "AT+CLCC", timeout=1.5)["response"])
    cpas = _parse_cpas(_send_at(settings, "AT+CPAS", timeout=1.2)["response"])

    status.update(clcc)
    status["ringing"] = status["ringing"] or cpas["ringing"]
    status["in_call"] = status["in_call"] or cpas["in_call"]
    status["call_active"] = status["call_active"] or status["ringing"] or status["in_call"]

    if status["sim_status"] == "missing" and status.get("sim_detected"):
        status.update({"state": "sim_unreadable", "message": "SIM 已插入但读卡失败"})
    elif status["sim_status"] == "missing":
        status.update({"state": "sim_missing", "message": "SIM 卡缺失"})
    elif status["sim_status"] == "pin_required":
        status.update({"state": "sim_pin", "message": "SIM 卡需要 PIN"})
    elif status["sim_status"] == "puk_required":
        status.update({"state": "sim_puk", "message": "SIM 卡需要 PUK"})
    elif status["dialing"]:
        status.update({"state": "dialing", "ready": True, "message": "正在拨号"})
    elif status["ringing"]:
        status.update({"state": "ringing", "ready": True, "message": "来电/等待接听"})
    elif status["in_call"]:
        status.update({"state": "in_call", "ready": True, "message": "通话中"})
    elif status["sim_status"] == "ready":
        status.update({"state": "idle", "ready": True, "message": "待机"})
    else:
        status.update({"state": "unknown", "message": "模块状态未知"})
    return status


def start_call(config_file, reason="", manual=False):
    global _last_call_started_at
    settings = load_settings(config_file)
    if not settings["enabled"]:
        return {"success": False, "message": "应急通话已禁用", "status": get_status(config_file)}

    phone = _clean_phone(settings["admin_phone"])
    if not phone:
        error = "管理员号码无效"
        _set_last_error(error)
        return {"success": False, "message": error, "status": get_status(config_file)}

    status = get_status(config_file)
    if status.get("call_active"):
        return {"success": True, "message": "已有通话正在进行", "status": status}
    if status.get("sim_status") != "ready":
        error = status.get("message") or "4G 模块未就绪"
        _set_last_error(error)
        return {"success": False, "message": error, "status": status}

    if status.get("manager") == "modemmanager":
        mm_result = _start_call_mmcli(phone)
        time.sleep(0.5)
        status_after = get_status(config_file)
        if mm_result["success"]:
            with _state_lock:
                _last_call_started_at = datetime.now().isoformat()
            _set_last_error("")
            print(f"[EmergencyCall] mmcli dial started: {_mask_phone(phone)} reason={reason} manual={manual}", flush=True)
            return {"success": True, "message": mm_result["message"], "status": status_after}
        _set_last_error(mm_result["message"])
        print(f"[EmergencyCall] mmcli dial failed: {mm_result['message']}", flush=True)
        return {"success": False, "message": mm_result["message"], "status": status_after}

    result = _send_at(settings, f"ATD{phone};", timeout=3.0)
    time.sleep(0.5)
    status_after = get_status(config_file)
    success = bool(result["ok"]) and status_after.get("state") not in ("sim_missing", "error", "port_missing")
    if success:
        with _state_lock:
            _last_call_started_at = datetime.now().isoformat()
        _set_last_error("")
        print(f"[EmergencyCall] dial started: {_mask_phone(phone)} reason={reason} manual={manual}", flush=True)
        return {"success": True, "message": "已拨打管理员", "status": status_after}

    error = result["error"] or status_after.get("message") or "拨号失败"
    _set_last_error(error)
    print(f"[EmergencyCall] dial failed: {error}", flush=True)
    return {"success": False, "message": error, "status": status_after}


def hangup_call(config_file):
    settings = load_settings(config_file)
    if not settings["enabled"]:
        return {"success": False, "message": "应急通话已禁用", "status": get_status(config_file)}
    status = get_status(config_file)
    if status.get("manager") == "modemmanager":
        mm_result = _hangup_call_mmcli()
        time.sleep(0.3)
        status_after = get_status(config_file)
        if mm_result["success"]:
            _set_last_error("")
            print("[EmergencyCall] mmcli hangup requested", flush=True)
            return {"success": True, "message": mm_result["message"], "status": status_after}
        _set_last_error(mm_result["message"])
        return {"success": False, "message": mm_result["message"], "status": status_after}
    result = _send_at(settings, "ATH", timeout=2.0)
    time.sleep(0.3)
    status_after = get_status(config_file)
    if result["ok"]:
        _set_last_error("")
        print("[EmergencyCall] hangup requested", flush=True)
        return {"success": True, "message": "已发送挂断命令", "status": status_after}
    error = result["error"] or "挂断失败"
    _set_last_error(error)
    return {"success": False, "message": error, "status": status_after}


def queue_auto_call(config_file, reason=""):
    global _last_auto_attempt_ts, _last_auto_reason
    settings = load_settings(config_file)
    if not settings["enabled"] or not settings["auto_call"]:
        return {"success": False, "queued": False, "skipped": True, "message": "自动拨号未启用"}

    now = time.time()
    cooldown = settings["auto_call_cooldown_seconds"]
    with _state_lock:
        if _last_auto_attempt_ts and now - _last_auto_attempt_ts < cooldown:
            return {"success": False, "queued": False, "skipped": True, "message": "自动拨号冷却中"}
        _last_auto_attempt_ts = now
        _last_auto_reason = reason or "紧急报警"

    thread = threading.Thread(
        target=start_call,
        args=(config_file,),
        kwargs={"reason": reason or "紧急报警", "manual": False},
        name="EmergencyAutoCall",
        daemon=True,
    )
    thread.start()
    print(f"[EmergencyCall] auto call queued: {reason or '紧急报警'}", flush=True)
    return {"success": True, "queued": True, "skipped": False, "message": "已排队自动拨号"}
