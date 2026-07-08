#!/usr/bin/env python3
"""
实验室应急安全管理系统 (LabSafe)
"""

from flask import Flask, render_template, jsonify, request, Response, redirect, url_for
import cv2
import threading
import time
import json
import os
import numpy as np
import logging
import requests
from datetime import datetime

# 导入AI分析模块；本地开发副本可能没有旧 ai_analysis.py，提供安全回退，保证界面可启动。
try:
    from app.ai_analysis import (
        set_api_key, capture_frame, analyze_image_with_ai,
        simple_fire_detection, generate_report
    )
except Exception as e:
    print(f"[Warning] AI analysis module not available: {e}")

    def set_api_key(api_key):
        return None

    def analyze_image_with_ai(frame_data):
        return {'analysis': 'AI 分析模块不可用，已使用本地回退。'}

    def simple_fire_detection(frame_data):
        return {'fire_detected': False, 'confidence': 0.0, 'source': 'fallback'}

    def generate_report(analysis_result, fire_result):
        return {
            'analysis': analysis_result,
            'fire': fire_result,
            'summary': 'AI 报告模块不可用，已返回基础结果。'
        }

# 导入目标检测模块
try:
    from app.detection_api import detection_api
    DETECTION_AVAILABLE = True
    # 自动加载模型 (根据配置选择 RKNN 或 YOLO)
    detection_api.load_model()
except Exception as e:
    print(f"[Warning] Detection module not available: {e}")
    DETECTION_AVAILABLE = False
    detection_api = None
try:
    from app.scheduler import get_scheduler
except Exception as e:
    print(f"[Warning] Scheduler module not available: {e}")

    class _FallbackScheduler:
        def __init__(self):
            self.tasks = []

        def add_task(self, **kwargs):
            task = {'id': len(self.tasks) + 1, **kwargs}
            self.tasks.append(task)
            return task

        def save_tasks(self):
            return True

        def remove_task(self, task_id):
            self.tasks = [task for task in self.tasks if task.get('id') != task_id]
            return True

        def toggle_task(self, task_id, enabled=None):
            for task in self.tasks:
                if task.get('id') == task_id:
                    task['enabled'] = (not task.get('enabled', True)) if enabled is None else bool(enabled)
                    return task
            return None

        def start(self):
            return True

    _fallback_scheduler = _FallbackScheduler()

    def get_scheduler():
        return _fallback_scheduler
from app.email_notify import send_security_report, send_alert
from app.emergency_call import (
    default_config as emergency_call_default_config,
    get_status as get_emergency_call_status,
    hangup_call as hangup_emergency_call,
    queue_auto_call as queue_emergency_auto_call,
    start_call as start_emergency_call,
)
from app.agent.client import get_agent_client
from app.agent.config import AGENT_SERVICE_URL

# 配置文件路径
PROJECT_ROOT = os.environ.get('LABSAFE_HOME') or os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CONFIG_FILE = os.environ.get('LABSAFE_CONFIG_FILE', '/home/elf/labsafe/config.json')
DHT11_STATE_FILE = os.environ.get('LABSAFE_DHT11_STATE_FILE', '/tmp/labsafe_dht11.json')

# 读取保存的配置
def load_config():
    global CAMERAS
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                if 'cameras' in config:
                    for cam_id, cam_config in config['cameras'].items():
                        if cam_id in CAMERAS:
                            CAMERAS[cam_id].update(cam_config)
                # 保存完整配置（包括fire, ai, notifications）
                if 'fire' in config or 'ai' in config or 'notifications' in config:
                    save_config()
                print(f"已加载配置: {CONFIG_FILE}")
        except Exception as e:
            print(f"加载配置失败: {e}")

# 保存配置
def save_config():
    try:
        # 读取现有配置，保留完整数据
        existing_config = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                existing_config = json.load(f)
        
        config = {
            'cameras': CAMERAS,
            'fire': existing_config.get('fire', {'temp_threshold': 40, 'sensitivity': 'medium', 'interval': 2}),
            'ai': existing_config.get('ai', {'model': 'MiniMax', 'api_key': ''}),
            'notifications': existing_config.get('notifications', {'sound': True, 'email': False, 'email_addr': ''}),
            'emergency_call': existing_config.get('emergency_call', emergency_call_default_config()),
            'agent': existing_config.get('agent', {})
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"配置已保存: {CONFIG_FILE}")
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False

app = Flask(__name__,
            template_folder=os.path.join(PROJECT_ROOT, 'templates'),
            static_folder=os.path.join(PROJECT_ROOT, 'static'))
app.config['SECRET_KEY'] = os.environ.get('LABSAFE_SECRET_KEY') or os.urandom(32).hex()
app.config['JSON_AS_ASCII'] = False
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# 全局状态
system_state = {
    'fire_detection': False,
    'alert_level': 'normal',  # normal, warning, danger
    'last_alert': None,
    'cameras': []
}

# 摄像头配置 (支持IP网络摄像头 RTSP/HTTP)
# 格式: rtsp://用户名:密码@IP地址:端口/路径
# 或者: http://IP地址:端口/mjpeg
CAMERAS = {
    'usb-camera': {
        'name': 'USB摄像头', 
        'url': '/dev/video11',
        'enabled': True,
        'type': 'usb'
    },
    'usb-camera-2': {
        'name': 'USB摄像头2', 
        'url': '/dev/video21',
        'enabled': True,
        'type': 'usb'
    }
}

# 加载保存的配置
load_config()

# 火灾检测状态
fire_state = {
    'detecting': False,
    'flame_detected': False,
    'temperature': 25.0,
    'humidity': None,
    'sensor_status': 'unknown',
    'sensor_updated_at': None,
    'sensor_error': '',
    'last_check': None,
    'alarm_active': False,
    'alarm_reason': '',
    'alarm_classes': [],
    'last_email_sent': None,
    'last_email_error': ''
}


def _refresh_dht11_state():
    """Load latest root-side DHT11 reading without touching GPIO in Flask."""
    try:
        with open(DHT11_STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        fire_state['sensor_status'] = 'missing'
        fire_state['sensor_error'] = 'DHT11 state file not found'
        return
    except Exception as e:
        fire_state['sensor_status'] = 'error'
        fire_state['sensor_error'] = str(e)[:120]
        return

    temp = data.get('temperature')
    humidity = data.get('humidity')
    if temp is not None:
        fire_state['temperature'] = float(temp)
    if humidity is not None:
        fire_state['humidity'] = float(humidity)
    fire_state['sensor_status'] = data.get('status') or 'unknown'
    fire_state['sensor_updated_at'] = data.get('updated_at')
    fire_state['sensor_error'] = data.get('error') or ''

# 初始化摄像头 (支持IP网络摄像头)
def init_cameras():
    for cam_id, cam in CAMERAS.items():
        try:
            if cam['enabled']:
                url = cam.get('url', cam.get('path', ''))
                cap = cv2.VideoCapture(url)
                if cap.isOpened():
                    cam['cap'] = cap
                    print(f"摄像头 {cam['name']} 已开启: {url}")
                else:
                    print(f"摄像头 {cam['name']} 无法打开: {url}")
        except Exception as e:
            print(f"摄像头 {cam['name']} 开启失败: {e}")

# 摄像头缓存
CAMERA_CAPS = {}
CAMERA_ENABLED = {}  # 摄像头启用状态

def get_camera_frame(cam_url, width=640, height=360):
    """获取摄像头帧 - 只使用Qt推送的帧"""
    global shared_frame_cache
    return shared_frame_cache if shared_frame_cache else None

# 火灾检测模拟（实际需要接入AI模型）
def fire_detection_loop():
    while True:
        if system_state['fire_detection']:
            # 模拟火灾检测
            # 实际项目中需要接入火焰检测模型
            import random
            fire_state['temperature'] = 25.0 + random.uniform(0, 5)
            fire_state['last_check'] = datetime.now().isoformat()
            
            # 模拟检测（实际需要真实模型）
            if random.random() < 0.01:  # 1%概率模拟火灾
                fire_state['flame_detected'] = True
                system_state['alert_level'] = 'danger'
                system_state['last_alert'] = {
                    'type': 'fire',
                    'time': datetime.now().isoformat(),
                    'message': '检测到火焰！'
                }
            else:
                fire_state['flame_detected'] = False
                if system_state['alert_level'] == 'danger':
                    system_state['alert_level'] = 'normal'
        
        time.sleep(2)

# 路由
@app.route('/')
def index():
    return render_template('index.html', cameras=CAMERAS)

@app.route('/admin')
def admin():
    return render_template('admin.html')

@app.route('/admin/cameras')
def admin_cameras():
    return render_template('admin_cameras.html', cameras=CAMERAS)

@app.route('/admin/messages')
def admin_messages():
    return render_template('admin_messages.html')

@app.route('/admin/send')
def admin_send():
    return render_template('admin_send.html')

@app.route('/admin/settings')
def admin_settings():
    return render_template('admin_settings.html', cameras=CAMERAS)

@app.route('/monitor')
def monitor():
    return render_template('monitor.html', cameras=CAMERAS)

@app.route('/analysis')
def analysis():
    return redirect(url_for('agent_page'), code=302)

@app.route('/agent')
def agent_page():
    return render_template('agent.html')

@app.route('/logs')
def logs():
    return render_template('logs.html')

@app.route('/settings')
def settings():
    return render_template('settings.html', cameras=CAMERAS)

# API接口
@app.route('/api/status')
def api_status():
    _refresh_dht11_state()
    return jsonify({
        'status': 'running',
        'uptime': time.time(),
        'fire_detection': system_state['fire_detection'],
        'alert_level': system_state['alert_level'],
        'fire_state': fire_state,
        'notifications': _load_notification_settings()
    })

@app.route('/api/cameras')
def api_cameras():
    return jsonify(CAMERAS)

# 共享帧缓存（用于网页显示）
shared_frame_cache = None
shared_frame_ndarray = None
shared_frame_version = 0
shared_frame_decoded_version = 0
shared_frame_condition = threading.Condition()

# RKNN 检测后的帧缓存。检测在后台 worker 中完成，HTTP 客户端只读取缓存，避免重复 NPU 推理。
detected_frame_cache = None
detected_frame_version = 0
detected_frame_condition = threading.Condition()
latest_detections = []
latest_detection_info = {
    'frame_version': 0,
    'timestamp': None,
    'latency_ms': None,
    'image_width': None,
    'image_height': None,
    'detections': [],
    'fire_alarm': False,
    'alarm_reason': ''
}
DETECTION_INTERVAL = float(os.environ.get('LABSAFE_DETECTION_INTERVAL', '0.08'))
FIRE_ALERT_CLASSES = {'flame': '火焰', 'smoke': '烟雾'}
FIRE_EMAIL_COOLDOWN = float(os.environ.get('LABSAFE_FIRE_EMAIL_COOLDOWN', '300'))
FIRE_EMAIL_RETRY_COOLDOWN = float(os.environ.get('LABSAFE_FIRE_EMAIL_RETRY_COOLDOWN', '30'))
FIRE_ALARM_CONFIRM_SECONDS = float(os.environ.get('LABSAFE_FIRE_ALARM_CONFIRM_SECONDS', '1.5'))
fire_email_lock = threading.Lock()
last_fire_email_ts = 0.0
last_fire_email_signature = ''
last_fire_email_attempt_ts = 0.0
last_fire_email_attempt_signature = ''
fire_candidate_since = 0.0
fire_candidate_signature = ''


def _load_notification_settings():
    """读取通知配置，返回不含敏感字段的运行开关。"""
    settings = {'sound': True, 'email': False, 'email_addr': ''}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            notif = config.get('notifications', {})
            settings.update({
                'sound': bool(notif.get('sound', True)),
                'email': bool(notif.get('email', False)),
                'email_addr': notif.get('email_addr', '')
            })
        except Exception as e:
            print(f"[FireAlert] 读取通知配置失败: {e}")
    return settings


def _send_fire_alert_email(alert_info, image_data):
    """在后台线程发送火灾/烟雾邮件，避免阻塞检测线程。"""
    global last_fire_email_ts, last_fire_email_signature
    classes = ', '.join(alert_info.get('classes') or ['fire'])
    detections = alert_info.get('detections') or []
    det_lines = []
    for det in detections[:10]:
        name = det.get('class_name', 'unknown')
        score = float(det.get('score', 0.0))
        bbox = det.get('bbox') or []
        det_lines.append(f"<li>{name}: confidence={score:.2f}, bbox={bbox}</li>")
    det_html = ''.join(det_lines) or '<li>无详细检测框</li>'

    title = f"视觉检测到火灾/烟雾: {classes}"
    content = f"""
    <p><b>LabSafe 已触发视觉火灾告警。</b></p>
    <p>告警原因: {alert_info.get('reason', '')}</p>
    <p>发生时间: {alert_info.get('time', '')}</p>
    <p>检测帧号: {alert_info.get('frame_version', '')}</p>
    <p>检测详情:</p>
    <ul>{det_html}</ul>
    <p>请立即确认实验室现场，必要时断电、疏散并启动消防处置流程。</p>
    """
    ok = False
    error = ''
    try:
        ok = bool(send_alert(title=title, content=content, image_data=image_data))
    except TypeError:
        ok = bool(send_alert(title=title, content=content))
    except Exception as e:
        error = str(e)
        ok = False

    with fire_email_lock:
        if ok:
            last_fire_email_ts = time.time()
            last_fire_email_signature = ','.join(sorted(alert_info.get('classes') or []))
            fire_state['last_email_sent'] = datetime.now().isoformat()
            fire_state['last_email_error'] = ''
            print(f"[FireAlert] email sent: {title}", flush=True)
        else:
            fire_state['last_email_error'] = error or 'send_alert returned False'
            print(f"[FireAlert] email send failed: {fire_state['last_email_error']}", flush=True)


def _queue_fire_alert_email(alert_info):
    """按冷却时间排队发送邮件。"""
    global last_fire_email_attempt_ts, last_fire_email_attempt_signature
    settings = _load_notification_settings()
    if not settings.get('email') or not settings.get('email_addr'):
        print("[FireAlert] email disabled or receiver missing", flush=True)
        return

    classes = sorted(alert_info.get('classes') or [])
    signature = ','.join(classes)
    now_ts = time.time()
    with fire_email_lock:
        cooling = (now_ts - last_fire_email_ts) < FIRE_EMAIL_COOLDOWN
        if cooling and signature == last_fire_email_signature:
            return
        retry_cooling = (now_ts - last_fire_email_attempt_ts) < FIRE_EMAIL_RETRY_COOLDOWN
        if retry_cooling and signature == last_fire_email_attempt_signature:
            return
        last_fire_email_attempt_ts = now_ts
        last_fire_email_attempt_signature = signature
        print(f"[FireAlert] queue email for {signature or 'fire'}", flush=True)

    with shared_frame_condition:
        image_data = shared_frame_cache
    thread = threading.Thread(
        target=_send_fire_alert_email,
        args=(alert_info, image_data),
        name='FireAlertEmail',
        daemon=True
    )
    thread.start()

@app.route('/api/camera/push_frame', methods=['POST'])
def api_push_frame():
    """接收Qt推送的摄像头帧"""
    global shared_frame_cache, shared_frame_ndarray, shared_frame_version
    if 'frame' in request.files:
        frame_data = request.files['frame'].read()
        with shared_frame_condition:
            shared_frame_cache = frame_data
            shared_frame_version += 1
            shared_frame_condition.notify_all()
        return 'ok', 200
    return 'no frame', 400


def _set_fire_state_from_detections(detections, frame_version=None):
    """把 RKNN 的 flame/smoke 检测同步到首页火灾状态。"""
    global fire_candidate_since, fire_candidate_signature
    danger = [d for d in detections if d.get('class_name') in ('flame', 'smoke')]
    now = datetime.now().isoformat()
    fire_state['last_check'] = now
    fire_state['detecting'] = bool(danger)
    fire_confirmed = False
    if danger:
        now_ts = time.time()
        if fire_candidate_since <= 0.0:
            fire_candidate_since = now_ts
        fire_candidate_signature = ','.join(sorted({d.get('class_name', 'fire') for d in danger}))
        fire_confirmed = (now_ts - fire_candidate_since) >= FIRE_ALARM_CONFIRM_SECONDS
    else:
        fire_candidate_since = 0.0
        fire_candidate_signature = ''
    fire_state['flame_detected'] = fire_confirmed
    fire_state['alarm_active'] = fire_confirmed
    if danger:
        classes = sorted({d.get('class_name', 'fire') for d in danger})
        class_text = '、'.join(FIRE_ALERT_CLASSES.get(c, c) for c in classes)
        if not fire_confirmed:
            fire_state['alarm_reason'] = ''
            fire_state['alarm_classes'] = []
            if system_state.get('alert_level') == 'danger':
                system_state['alert_level'] = 'normal'
            return {
                'active': False,
                'classes': classes,
                'reason': '',
                'time': now,
                'frame_version': frame_version,
                'detections': danger
            }
        fire_state['detecting'] = True
        if fire_state.get('sensor_status') != 'ok':
            fire_state['temperature'] = max(fire_state.get('temperature', 25.0), 40.0)
        fire_state['alarm_classes'] = classes
        fire_state['alarm_reason'] = f"视觉检测到 {class_text}"
        system_state['alert_level'] = 'danger'
        top = max(danger, key=lambda d: d.get('score', 0))
        system_state['last_alert'] = {
            'type': top.get('class_name', 'fire'),
            'time': now,
            'message': f"RKNN检测到 {top.get('class_name')}，置信度 {top.get('score', 0):.2f}"
        }
        alert_info = {
            'active': True,
            'classes': classes,
            'reason': fire_state['alarm_reason'],
            'time': now,
            'frame_version': frame_version,
            'detections': danger
        }
        _queue_fire_alert_email(alert_info)
        try:
            queue_emergency_auto_call(CONFIG_FILE, f"视觉火灾告警: {fire_state['alarm_reason']}")
        except Exception as e:
            print(f"[EmergencyCall] auto call queue failed: {e}", flush=True)
        return alert_info
    else:
        fire_state['detecting'] = False
        if fire_state.get('sensor_status') != 'ok' and fire_state.get('temperature', 25.0) >= 40.0:
            fire_state['temperature'] = 25.0
        fire_state['alarm_reason'] = ''
        fire_state['alarm_classes'] = []
        if system_state.get('alert_level') == 'danger':
            system_state['alert_level'] = 'normal'
    return {
        'active': False,
        'classes': [],
        'reason': '',
        'time': now,
        'frame_version': frame_version,
        'detections': []
    }


def _run_detection_once(frame_data, frame_version):
    """对一帧 JPEG 做 RKNN 检测并更新检测缓存。"""
    global detected_frame_cache, detected_frame_version, latest_detections, latest_detection_info
    if not DETECTION_AVAILABLE or detection_api is None:
        return False

    nparr = np.frombuffer(frame_data, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        return False
    image_h, image_w = frame.shape[:2]

    start = time.time()
    annotated, detections = detection_api.detect_frame(frame, cam_id='usb-camera')
    latency_ms = (time.time() - start) * 1000.0
    ok, img_encoded = cv2.imencode('.jpg', annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        return False

    latest_detections = detections
    alarm_info = _set_fire_state_from_detections(detections, frame_version=frame_version)
    latest_detection_info = {
        'frame_version': frame_version,
        'timestamp': datetime.now().isoformat(),
        'latency_ms': latency_ms,
        'image_width': image_w,
        'image_height': image_h,
        'detections': detections,
        'fire_alarm': alarm_info.get('active', False),
        'alarm_reason': alarm_info.get('reason', '')
    }

    with detected_frame_condition:
        detected_frame_cache = img_encoded.tobytes()
        detected_frame_version = frame_version
        detected_frame_condition.notify_all()
    return True


def detection_loop():
    """后台 RKNN 检测线程：只处理最新帧，按固定间隔限速。"""
    last_processed_version = 0
    last_infer_ts = 0.0
    while True:
        with shared_frame_condition:
            shared_frame_condition.wait_for(
                lambda: shared_frame_version != last_processed_version and shared_frame_cache is not None,
                timeout=1.0
            )
            if shared_frame_cache is None or shared_frame_version == last_processed_version:
                continue
            frame_data = shared_frame_cache
            frame_version = shared_frame_version

        now = time.time()
        wait_s = DETECTION_INTERVAL - (now - last_infer_ts)
        if wait_s > 0:
            time.sleep(wait_s)

        try:
            if _run_detection_once(frame_data, frame_version):
                last_processed_version = frame_version
                last_infer_ts = time.time()
        except Exception as e:
            print(f"[DetectionWorker] error: {e}")
            import traceback
            traceback.print_exc()
            last_processed_version = frame_version
            time.sleep(0.2)

@app.route('/api/camera/<cam_id>/toggle', methods=['POST'])
def api_toggle_camera(cam_id):
    """开启/关闭摄像头"""
    if cam_id not in CAMERAS:
        return jsonify({'error': 'Camera not found'}), 404
    
    cam = CAMERAS[cam_id]
    cam_url = cam.get('url', cam.get('path', ''))
    
    # 切换状态
    current_state = CAMERA_ENABLED.get(cam_url, True)
    CAMERA_ENABLED[cam_url] = not current_state
    
    # 如果关闭，释放摄像头
    if not CAMERA_ENABLED[cam_url]:
        if cam_url in CAMERA_CAPS:
            CAMERA_CAPS[cam_url].release()
            del CAMERA_CAPS[cam_url]
    
    return jsonify({'camera': cam_id, 'enabled': CAMERA_ENABLED[cam_url]})

@app.route('/api/camera/<cam_id>/snapshot')
def api_snapshot(cam_id):
    global shared_frame_cache
    # 如果有Qt推送的帧，优先使用
    if shared_frame_cache and cam_id == 'usb-camera':
        return Response(shared_frame_cache, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})
    
    if cam_id not in CAMERAS:
        return jsonify({'error': 'Camera not found'}), 404
    
    cam = CAMERAS[cam_id]
    cam_url = cam.get('url', cam.get('path', ''))
    frame = get_camera_frame(cam_url)
    
    if frame is not None:
        if isinstance(frame, (bytes, bytearray)):
            return Response(frame, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})
        _, img_encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        return Response(img_encoded.tobytes(), mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})
    else:
        return jsonify({'error': 'Cannot get frame'}), 500

# MJPEG 流式视频接口 - GStreamer硬件加速
@app.route('/api/camera/<cam_id>/stream')
def api_stream(cam_id):
    if cam_id not in CAMERAS:
        return jsonify({'error': 'Camera not found'}), 404
    
    cam = CAMERAS[cam_id]
    cam_url = cam.get('url', cam.get('path', ''))
    
    def generate():
        last_version = 0
        while True:
            with shared_frame_condition:
                has_new_frame = shared_frame_condition.wait_for(
                    lambda: shared_frame_version != last_version and shared_frame_cache is not None,
                    timeout=1.0
                )
                if not has_new_frame:
                    continue
                frame_data = shared_frame_cache
                last_version = shared_frame_version
            if frame_data:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n'
                       b'Cache-Control: no-store\r\n'
                       b'Access-Control-Allow-Origin: *\r\n\r\n' + frame_data + b'\r\n')
    
    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'}
    )


@app.route('/api/camera/<cam_id>/stream/detect')
def api_stream_detect(cam_id):
    """带 RKNN 检测框的 MJPEG 视频流。"""
    if cam_id not in CAMERAS:
        return jsonify({'error': 'Camera not found'}), 404
    if not DETECTION_AVAILABLE or detection_api is None:
        return jsonify({'error': 'Detection not available'}), 500

    def generate():
        last_version = 0
        while True:
            with detected_frame_condition:
                has_new_frame = detected_frame_condition.wait_for(
                    lambda: detected_frame_version != last_version and detected_frame_cache is not None,
                    timeout=1.0
                )
                if not has_new_frame:
                    continue
                frame_data = detected_frame_cache
                last_version = detected_frame_version
            if frame_data:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n'
                       b'Cache-Control: no-store\r\n'
                       b'Access-Control-Allow-Origin: *\r\n\r\n' + frame_data + b'\r\n')

    return Response(
        generate(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'}
    )

# 目标检测状态
@app.route('/api/detection/status')
def api_detection_status():
    """获取检测状态"""
    if not DETECTION_AVAILABLE:
        return jsonify({'available': False})
    return jsonify(detection_api.get_status())

@app.route('/api/detection/analyze-upload', methods=['POST'])
def api_detection_analyze_upload():
    """对 Agent 上传图片做一次本地检测，不更新现场火灾报警状态。"""
    if not DETECTION_AVAILABLE or detection_api is None:
        return jsonify({'success': False, 'error': 'Detection not available', 'detections': []}), 503
    file_storage = request.files.get('file')
    if not file_storage:
        return jsonify({'success': False, 'error': 'missing file', 'detections': []}), 400
    try:
        raw = file_storage.read()
        nparr = np.frombuffer(raw, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return jsonify({'success': False, 'error': 'cannot decode image', 'detections': []}), 400
        image_h, image_w = frame.shape[:2]
        start = time.time()
        _, detections = detection_api.detect_frame(frame, cam_id='agent-upload')
        latency_ms = (time.time() - start) * 1000.0
        danger = [d for d in detections if d.get('class_name') in ('flame', 'smoke')]
        return jsonify({
            'success': True,
            'image_width': image_w,
            'image_height': image_h,
            'latency_ms': latency_ms,
            'detections': detections,
            'fire_alarm': bool(danger),
            'alarm_reason': '上传图片检测到火焰/烟雾' if danger else ''
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'detections': []}), 500

# 目标检测截图
@app.route('/api/camera/<cam_id>/snapshot/detect')
def api_snapshot_detect(cam_id):
    """带目标检测的截图"""
    global shared_frame_ndarray, shared_frame_decoded_version
    if cam_id not in CAMERAS:
        return jsonify({'error': 'Camera not found'}), 404
    
    if not DETECTION_AVAILABLE or detection_api is None:
        return jsonify({'error': 'Detection not available'}), 500

    with detected_frame_condition:
        if detected_frame_cache is not None:
            return Response(detected_frame_cache, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})
    
    cam = CAMERAS[cam_id]
    cam_url = cam.get('url', cam.get('path', ''))
    frame = None
    with shared_frame_condition:
        if shared_frame_cache is not None:
            if shared_frame_ndarray is None or shared_frame_decoded_version != shared_frame_version:
                nparr = np.frombuffer(shared_frame_cache, np.uint8)
                shared_frame_ndarray = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                shared_frame_decoded_version = shared_frame_version
            frame = shared_frame_ndarray.copy() if shared_frame_ndarray is not None else None

    if frame is None:
        frame = get_camera_frame(cam_url)
    if isinstance(frame, (bytes, bytearray)):
        nparr = np.frombuffer(frame, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if frame is not None:
        frame, detections = detection_api.detect_frame(frame)
        _, img_encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return Response(img_encoded.tobytes(), mimetype='image/jpeg')
    else:
        return jsonify({'error': 'Cannot get frame'}), 500


@app.route('/api/camera/<cam_id>/detections')
def api_camera_detections(cam_id):
    """最新 RKNN 检测结果 JSON。"""
    if cam_id not in CAMERAS:
        return jsonify({'error': 'Camera not found'}), 404
    return jsonify(latest_detection_info)

@app.route('/api/fire/start', methods=['POST'])
def api_fire_start():
    system_state['fire_detection'] = True
    return jsonify({'status': 'started'})

@app.route('/api/fire/stop', methods=['POST'])
def api_fire_stop():
    system_state['fire_detection'] = False
    return jsonify({'status': 'stopped'})

@app.route('/api/fire/status')
def api_fire_status():
    _refresh_dht11_state()
    return jsonify(fire_state)

@app.route('/api/alerts')
def api_alerts():
    alert = system_state.get('last_alert')
    if alert:
        return jsonify([alert])
    return jsonify([])

@app.route('/api/settings')
def api_settings():
    """获取设置"""
    # 读取保存的配置
    saved_config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                saved_config = json.load(f)
        except:
            pass
    
    return jsonify({
        'cameras': CAMERAS,
        'fire': saved_config.get('fire', {'temp_threshold': 40, 'sensitivity': 'medium', 'interval': 2}),
        'ai': saved_config.get('ai', {'model': 'MiniMax', 'api_key': ''}),
        'notifications': saved_config.get('notifications', {'sound': True, 'email': False, 'email_addr': ''}),
        'agent': saved_config.get('agent', {})
    })

@app.route('/api/settings/save', methods=['POST'])
def api_settings_save():
    """保存设置"""
    data = request.json
    try:
        if 'cameras' in data:
            for cam_id, cam_config in data['cameras'].items():
                if cam_id in CAMERAS:
                    CAMERAS[cam_id]['url'] = cam_config.get('url', '')
                    CAMERAS[cam_id]['enabled'] = cam_config.get('enabled', False)
        
        existing_config = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    existing_config = json.load(f)
            except:
                existing_config = {}

        # 保存到文件 - 包含所有配置。敏感字段如果前端留空或不提交，则保留原值。
        existing_ai = existing_config.get('ai', {'model': 'MiniMax', 'api_key': ''})
        incoming_ai = data.get('ai', {})
        ai_data = {**existing_ai, **incoming_ai}
        for secret_key in ('api_key',):
            if not incoming_ai.get(secret_key) and existing_ai.get(secret_key):
                ai_data[secret_key] = existing_ai.get(secret_key)

        existing_notifications = existing_config.get('notifications', {'sound': True, 'email': False, 'email_addr': ''})
        incoming_notifications = data.get('notifications', {})
        notification_data = {**existing_notifications, **incoming_notifications}
        for secret_key in ('email_password',):
            if not incoming_notifications.get(secret_key) and existing_notifications.get(secret_key):
                notification_data[secret_key] = existing_notifications.get(secret_key)

        config = {
            'cameras': CAMERAS,
            'fire': data.get('fire', {'temp_threshold': 40, 'sensitivity': 'medium', 'interval': 2}),
            'ai': ai_data,
            'notifications': notification_data,
            'emergency_call': existing_config.get('emergency_call', emergency_call_default_config()),
            'agent': data.get('agent', existing_config.get('agent', {}))
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        # 如果开启了自动分析，创建/更新定时任务
        if ai_data.get('auto_analysis') and ai_data.get('send_time'):
            scheduler = get_scheduler()
            send_time = ai_data.get('send_time')
            
            # 检查是否已存在定时任务
            existing_task = None
            for task in scheduler.tasks:
                if task.get('name') == '安全分析报告':
                    existing_task = task
                    break
            
            if existing_task:
                # 更新现有任务
                existing_task['send_time'] = send_time
                existing_task['enabled'] = True
            else:
                # 创建新任务
                scheduler.add_task(name='安全分析报告', send_time=send_time, enabled=True)
            
            scheduler.save_tasks()
            print(f"✅ 已创建定时任务: 每天 {send_time} 发送报告")
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/ai/analyze', methods=['POST'])
def api_ai_analyze():
    """调用AI进行摄像头画面安全分析"""
    data = request.json or {}
    cam_id = data.get('camera', 'living-room')
    
    # 获取API密钥
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        except:
            pass
    
    api_key = config.get('ai', {}).get('api_key', '')
    if not api_key:
        # 如果没有API密钥，使用简单的火焰检测
        cam = CAMERAS.get(cam_id)
        if cam and cam.get('enabled'):
            url = cam.get('url', '')
            frame_data = capture_frame(url)
            if frame_data:
                fire_result = simple_fire_detection(frame_data)
                return jsonify({
                    'timestamp': datetime.now().isoformat(),
                    'result': '危险' if fire_result['fire_detected'] else '安全',
                    'confidence': fire_result['confidence'],
                    'fire_detected': fire_result['fire_detected'],
                    'details': '基于颜色检测结果'
                })
        return jsonify({'error': '请先在设置中配置AI API密钥', 'success': False})
    
    # 设置API密钥
    set_api_key(api_key)
    
    # 获取摄像头画面
    cam = CAMERAS.get(cam_id)
    if not cam or not cam.get('enabled'):
        return jsonify({'error': '摄像头未启用', 'success': False})
    
    url = cam.get('url', '')
    frame_data = capture_frame(url)
    
    if not frame_data:
        return jsonify({'error': '无法获取摄像头画面', 'success': False})
    
    # 火焰检测
    fire_result = simple_fire_detection(frame_data)
    
    # AI分析
    analysis_result = analyze_image_with_ai(frame_data)
    
    # 生成报告
    report = generate_report(analysis_result, fire_result)
    
    return jsonify({
        'timestamp': datetime.now().isoformat(),
        'result': '危险' if fire_result['fire_detected'] else '安全',
        'confidence': fire_result['confidence'],
        'fire_detected': fire_result['fire_detected'],
        'analysis': analysis_result.get('analysis', ''),
        'report': report,
        'success': True
    })

@app.route('/api/ai/analyze_and_email', methods=['POST'])
def api_analyze_and_email():
    """分析并发送邮件报告"""
    # 分析
    result = api_ai_analyze_internal()
    
    if result.get('success'):
        # 发送邮件
        report = result.get('report', '')
        image_data = result.get('image_data')
        send_security_report(report, image_data)
        return jsonify({'success': True, 'message': '报告已发送到邮箱'})
    else:
        return jsonify({'success': False, 'error': result.get('error', '分析失败')})

def api_ai_analyze_internal():
    """内部AI分析函数"""
    # 获取配置
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
        except:
            pass
    
    api_key = config.get('ai', {}).get('api_key', '')
    print(f"[DEBUG] API key configured: {bool(api_key)}")
    
    cam = CAMERAS.get('usb-camera')
    print(f"[DEBUG] Camera: {cam}")
    if not cam or not cam.get('enabled'):
        print("[DEBUG] Camera not enabled error")
        return {'error': '摄像头未启用', 'success': False}
    
    url = cam.get('url', '')
    frame_data = capture_frame(url)
    
    if not frame_data:
        return {'error': '无法获取摄像头画面', 'success': False}
    
    fire_result = simple_fire_detection(frame_data)
    
    if api_key:
        set_api_key(api_key)
        analysis_result = analyze_image_with_ai(frame_data)
    else:
        analysis_result = {'analysis': '未启用AI分析'}
    
    report = generate_report(analysis_result, fire_result)
    
    return {
        'success': True,
        'fire_result': fire_result,
        'analysis': analysis_result,
        'report': report,
        'image_data': frame_data  # 返回图片数据
    }

def capture_frame(cam_url):
    """捕获摄像头帧"""
    global shared_frame_cache
    try:
        # 优先使用Qt推送的共享帧缓存
        if shared_frame_cache:
            return shared_frame_cache
        
        if cam_url.startswith('/dev/'):
            import cv2
            import time
            
            # 直接用OpenCV打开，不预先设置格式
            cap = cv2.VideoCapture(cam_url, cv2.CAP_V4L2)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
            
            # 等待相机初始化
            time.sleep(0.3)
            
            ret, frame = cap.read()
            cap.release()
            
            if ret:
                _, img = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                return img.tobytes()
            return None
            
        elif cam_url.startswith('http://') or cam_url.startswith('https://'):
            import urllib.request
            return urllib.request.urlopen(cam_url, timeout=3).read()
        return None
    except Exception as e:
        print(f"[capture_frame error] {e}")
        return None

# 定时任务回调
def scheduled_analysis():
    """定时分析任务"""
    result = api_ai_analyze_internal()
    if result.get('success'):
        report = result.get('report', '')
        image_data = result.get('image_data')
        send_security_report(report, image_data)

@app.route('/api/schedule/tasks', methods=['GET'])
def api_schedule_list():
    """获取定时任务列表"""
    scheduler = get_scheduler()
    return jsonify({'tasks': scheduler.tasks})

@app.route('/api/schedule/add', methods=['POST'])
def api_schedule_add():
    """添加定时任务"""
    data = request.json
    scheduler = get_scheduler()
    task = scheduler.add_task(
        name=data.get('name', '安全分析'),
        interval_hours=data.get('interval_hours', 24),
        send_time=data.get('send_time'),  # 固定时间 "HH:MM"
        enabled=True
    )
    return jsonify({'success': True, 'task': task})

@app.route('/api/schedule/<int:task_id>', methods=['DELETE'])
def api_schedule_delete(task_id):
    """删除定时任务"""
    scheduler = get_scheduler()
    scheduler.remove_task(task_id)
    return jsonify({'success': True})

@app.route('/api/schedule/<int:task_id>/toggle', methods=['POST'])
def api_schedule_toggle(task_id):
    """开关定时任务"""
    data = request.json
    scheduler = get_scheduler()
    scheduler.toggle_task(task_id, data.get('enabled', True))
    return jsonify({'success': True})

@app.route('/api/agent/status', methods=['GET'])
def api_agent_status():
    """Agent 状态、风险等级、工具健康和最近审计记录。"""
    return jsonify(get_agent_client().status())

@app.route('/api/agent/chat', methods=['POST'])
def api_agent_chat():
    """Agent 主对话入口。"""
    data = request.get_json(silent=True) or {}
    return jsonify(get_agent_client().chat(
        data.get('message', ''),
        data.get('sender', 'user'),
        deep_thinking=data.get('deep_thinking', False),
        web_search=data.get('web_search', False),
        attachment_ids=data.get('attachment_ids') or [],
        session_id=data.get('session_id') or data.get('conversation_id'),
    ))

@app.route('/api/agent/uploads', methods=['POST'])
def api_agent_uploads():
    """上传 Agent 附件，代理到独立 Agent 服务。"""
    return jsonify(get_agent_client().upload_file(request.files.get('file')))

@app.route('/api/agent/uploads/<file_id>/content', methods=['GET'])
def api_agent_upload_content(file_id):
    return _proxy_agent_upload_asset(file_id, 'content')

@app.route('/api/agent/uploads/<file_id>/thumbnail', methods=['GET'])
def api_agent_upload_thumbnail(file_id):
    return _proxy_agent_upload_asset(file_id, 'thumbnail')

def _proxy_agent_upload_asset(file_id, asset):
    try:
        resp = requests.get(f"{AGENT_SERVICE_URL.rstrip('/')}/api/agent/uploads/{file_id}/{asset}", timeout=8)
        mimetype = resp.headers.get('Content-Type', 'application/octet-stream')
        return Response(resp.content, status=resp.status_code, mimetype=mimetype, headers={'Cache-Control': 'no-store'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 502

@app.route('/api/agent/action/confirm', methods=['POST'])
def api_agent_action_confirm():
    """确认执行 Agent 提出的高风险动作。"""
    data = request.get_json(silent=True) or {}
    return jsonify(get_agent_client().confirm(data.get('token', '')))

@app.route('/api/agent/enable', methods=['POST'])
def api_agent_enable():
    """启用 Agent，不改变原有报警链路。"""
    return jsonify(get_agent_client().set_enabled(True))

@app.route('/api/agent/disable', methods=['POST'])
def api_agent_disable():
    """关闭 Agent，不改变原有报警链路。"""
    return jsonify(get_agent_client().set_enabled(False))

@app.route('/api/agent/models', methods=['GET'])
def api_agent_models():
    """返回 Agent 可选模型和当前选择。"""
    return jsonify(get_agent_client().models())

@app.route('/api/agent/models/select', methods=['POST'])
def api_agent_models_select():
    """切换 Agent 当前模型，下一次对话生效。"""
    data = request.get_json(silent=True) or {}
    return jsonify(get_agent_client().select_model(data.get('provider', ''), data.get('model', '')))

@app.route('/api/agent/models/test', methods=['POST'])
def api_agent_models_test():
    """测试指定模型连通性，不触发任何实验室动作。"""
    data = request.get_json(silent=True) or {}
    return jsonify(get_agent_client().test_model(data.get('provider', ''), data.get('model', '')))

@app.route('/api/llm/chat', methods=['POST'])
def api_llm_chat():
    """兼容旧入口，内部转发到 Agent。"""
    data = request.get_json(silent=True) or {}
    result = get_agent_client().chat(
        data.get('message', ''),
        data.get('sender', 'user'),
        deep_thinking=data.get('deep_thinking', False),
        web_search=data.get('web_search', False),
        attachment_ids=data.get('attachment_ids') or [],
        session_id=data.get('session_id') or data.get('conversation_id'),
    )
    return jsonify(result)

@app.route('/api/messages', methods=['GET'])
def api_messages():
    """获取消息列表"""
    messages_file = '/home/elf/labsafe/messages.json'
    if os.path.exists(messages_file):
        try:
            with open(messages_file, 'r') as f:
                return jsonify(json.load(f))
        except:
            pass
    return jsonify([])

@app.route('/api/messages/send', methods=['POST'])
def api_messages_send():
    """发送消息（保存到历史）"""
    data = request.json
    message = {
        'sender': data.get('sender', 'admin'),
        'content': data.get('content', ''),
        'time': datetime.now().strftime('%H:%M'),
        'type': data.get('type', 'chat')
    }
    
    messages_file = '/home/elf/labsafe/messages.json'
    all_messages = []
    if os.path.exists(messages_file):
        try:
            with open(messages_file, 'r') as f:
                all_messages = json.load(f)
        except:
            pass
    
    all_messages.append(message)
    all_messages = all_messages[-100:]
    
    with open(messages_file, 'w') as f:
        json.dump(all_messages, f, indent=2)
    
    return jsonify({'success': True})

@app.route('/api/messages/clear', methods=['POST'])
def api_messages_clear():
    """清空消息历史"""
    messages_file = '/home/elf/labsafe/messages.json'
    try:
        with open(messages_file, 'w') as f:
            json.dump([], f)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/emergency-call/status')
def api_emergency_call_status():
    """查询 4G 应急电话状态。"""
    try:
        return jsonify(get_emergency_call_status(CONFIG_FILE))
    except Exception as e:
        return jsonify({
            'success': False,
            'state': 'error',
            'ready': False,
            'message': str(e)
        })

@app.route('/api/emergency-call/start', methods=['POST'])
def api_emergency_call_start():
    """拨打配置中的管理员电话，不接受前端传入号码。"""
    data = request.get_json(silent=True) or {}
    reason = data.get('reason', 'Qt手动拨号')
    try:
        return jsonify(start_emergency_call(CONFIG_FILE, reason=reason, manual=True))
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e),
            'status': get_emergency_call_status(CONFIG_FILE)
        })

@app.route('/api/emergency-call/hangup', methods=['POST'])
def api_emergency_call_hangup():
    """挂断当前应急电话。"""
    try:
        return jsonify(hangup_emergency_call(CONFIG_FILE))
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e),
            'status': get_emergency_call_status(CONFIG_FILE)
        })

@app.route('/api/alert/emergency', methods=['POST'])
def api_emergency_alert():
    """紧急报警 - 发送邮件和推送"""
    data = request.get_json(silent=True) or {}
    lab_name = data.get('lab_name', '实验室1')
    alert_type = data.get('type', 'SOS紧急求助')
    message = data.get('message', '需要紧急援助')
    
    # 发送邮件
    from app.email_notify import send_alert
    send_alert(
        title=f"🚨 {lab_name} - {alert_type}",
        content=f"{message}\n\n实验室: {lab_name}\n报警类型: {alert_type}\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    # 保存消息
    msg = {
        'sender': 'system',
        'content': f"🚨 {lab_name} - {alert_type}: {message}",
        'time': datetime.now().strftime('%H:%M'),
        'type': 'emergency'
    }
    messages_file = '/home/elf/labsafe/messages.json'
    all_messages = []
    if os.path.exists(messages_file):
        try:
            with open(messages_file, 'r') as f:
                all_messages = json.load(f)
        except:
            pass
    all_messages.append(msg)
    all_messages = all_messages[-100:]
    with open(messages_file, 'w') as f:
        json.dump(all_messages, f, indent=2)

    try:
        call_result = queue_emergency_auto_call(CONFIG_FILE, f"{lab_name} - {alert_type}: {message}")
    except Exception as e:
        call_result = {'success': False, 'queued': False, 'message': str(e)}
    
    return jsonify({'success': True, 'emergency_call': call_result})

if __name__ == '__main__':
    print("=" * 50)
    print("🧪 实验室应急安全管理系统 (LabSafe)")
    print("=" * 50)
    print("访问地址: http://localhost:5000")
    print()
    
    # 启动火灾检测线程
    fire_thread = threading.Thread(target=fire_detection_loop, daemon=True)
    fire_thread.start()

    # 启动 RKNN 实时检测线程
    if DETECTION_AVAILABLE and detection_api is not None:
        detection_thread = threading.Thread(target=detection_loop, daemon=True, name='RKNNDetectionWorker')
        detection_thread.start()
        print("RKNN 实时检测线程已启动")
    
    # 启动定时任务调度器
    scheduler = get_scheduler()
    scheduler.start()
    
    # 启动Web服务
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
