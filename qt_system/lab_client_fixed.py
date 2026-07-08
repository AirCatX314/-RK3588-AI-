#!/usr/bin/env python3
"""
实验室安全监控系统 - Qt工控客户端
修复版：CameraWorker解决VIDIOC_QBUF Bad file descriptor问题
"""

import sys
import json
import asyncio
import threading
import time
import cv2
import numpy as np
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtNetwork import *
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

SERVER_IP = "127.0.0.1"
SERVER_PORT = 8765
SHARE_PORT = 5001


class CameraWorker(QObject):
    """修复版摄像头Worker - 解决read()永久阻塞问题"""
    frame_ready = pyqtSignal(object)  # QImage
    status = pyqtSignal(str)
    
    DEVICES = ['/dev/video21', '/dev/video22']
    PUSH_URL = 'http://127.0.0.1:5000/api/camera/push_frame'
    
    def __init__(self):
        super().__init__()
        self._thread = None
        self._stop = None
        self._cap = None
        self._session = None
        self._failures = 0
        self._last_frame = 0
        self._reconnect_delay = 1.0
        self._push_session = None
    
    def _open_camera(self):
        for dev in self.DEVICES:
            try:
                cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(dev)
                if cap.isOpened():
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_FPS, 20)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    self._cap = cap
                    self._failures = 0
                    self._reconnect_delay = 1.0
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    self.status.emit(f"opened {dev} {w}x{h}@{fps}fps")
                    return True
            except Exception as e:
                self.status.emit(f"open {dev} failed: {e}")
        self.status.emit("all devices failed")
        return False
    
    def _release(self):
        if self._cap:
            try:
                self._cap.release()
            except Exception as e:
                self.status.emit(f"release error: {e}")
            self._cap = None
    
    def _backoff(self):
        delay = min(1.0 * (2 ** self._reconnect_delay), 10.0)
        self._reconnect_delay += 1
        self.status.emit(f"reconnect in {delay:.1f}s")
        start = time.time()
        while time.time() - start < delay:
            if self._stop and self._stop.is_set():
                return True
            time.sleep(0.05)
        return False
    
    def _push_frame(self, frame):
        try:
            if self._push_session is None:
                self._push_session = requests.Session()
            _, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            self._push_session.post(self.PUSH_URL, files={'frame': jpg.tobytes()}, timeout=0.5)
        except Exception as e:
            self.status.emit(f"push failed: {e}")
    
    def run(self):
        """Worker线程主循环"""
        self.status.emit("worker started")
        while self._stop and not self._stop.is_set():
            if self._cap is None or not self._cap.isOpened():
                if not self._open_camera():
                    if self._backoff():
                        break
                    continue
            
            try:
                ret, frame = self._cap.read()
                if ret and frame is not None:
                    self._failures = 0
                    self._reconnect_delay = 1.0
                    self._last_frame = time.time()
                    # 转换并emit到UI线程
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w = rgb.shape[:2]
                    img = QImage(rgb.data, w, h, QImage.Format_RGB888)
                    self.frame_ready.emit(img.copy())
                    # 推送后端
                    self._push_frame(frame)
                else:
                    self._failures += 1
                    if self._failures >= 3:
                        self.status.emit("3 failures, releasing camera")
                        self._release()
                        self._push_to_remote(None)  # 通知后端
            except Exception as e:
                err = str(e)
                self.status.emit(f"read error: {err}")
                if 'bad file descriptor' in err.lower() or 'no device' in err.lower():
                    self._release()
                    self.status.emit("camera error, will retry")
                self._failures += 1
            
            # stall检测
            if self._last_frame > 0 and time.time() - self._last_frame > 3:
                self.status.emit("stalled 3s, releasing")
                self._release()
                self._failures = 0
        
        self._release()
        self.status.emit("worker exit")
    
    def start(self):
        """启动worker"""
        if self._thread and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self.run, name='CameraWorker', daemon=True)
        self._thread.start()
    
    def stop(self):
        """停止worker（从UI线程调用"""
        self.status.emit("stop requested")
        if self._stop:
            self._stop.set()
        # 不在这里release cap，worker线程自己会处理
        self._thread = None


class WebSocketThread(QThread):
    message_received = pyqtSignal(dict)
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    
    def __init__(self):
        super().__init__()
        self.running = True
        self.ws = None
        
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.connect_server())
        
    async def connect_server(self):
        while self.running:
            try:
                async with websockets.connect(f"ws://{ SERVER_IP}:{SERVER_PORT}") as ws:
                    self.ws = ws
                    self.connected.emit()
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            self.message_received.emit(data)
                        except: pass
            except Exception as e:
                self.disconnected.emit()
            if self.running:
                await asyncio.sleep(3)
    
    def send_message(self, msg: dict):
        if self.ws:
            try:
                asyncio.run(self.ws.send(json.dumps(msg)))
            except: pass
    
    def stop(self):
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except: pass


class TitleBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setFixedHeight(45)
        self.setup_ui()
        self.start_move = None
    
    def setup_ui(self):
        self.setStyleSheet("""
            TitleBar { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1a237e, stop:1 #283593);
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 8, 0)
        layout.setSpacing(8)
        title = QLabel("🧪 实验室安全监控系统")
        title.setStyleSheet("color: #fff; font-size: 15px; font-weight: bold;")
        layout.addWidget(title)
        layout.addStretch()
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(2)
        for icon, tip in [("—", "最小化"), ("☐", "全屏"), ("×", "关闭")]:
            btn = QPushButton(icon)
            btn.setFixedSize(40, 35)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setToolTip(tip)
            btn.setStyleSheet("QPushButton { background: transparent; color: #fff; border: none; border-radius: 4px; font-size: 16px; } QPushButton:hover { background: rgba(255,255,255,0.15); } QPushButton:pressed { background: rgba(255,255,255,0.25); }")
            btn.clicked.connect(lambda: self.minimize() if icon == "—" else self.toggle_maximize() if icon == "☐" else self.close())
            btn_layout.addWidget(btn)
        layout.addLayout(btn_layout)
    
    def minimize(self):
        if self.parent_window:
            self.parent_window.showMinimized()
    
    def toggle_maximize(self):
        if self.parent_window:
            if self.parent_window.isFullScreen():
                self.parent_window.showNormal()
            else:
                self.parent_window.showFullScreen()
    
    def close(self):
        if self.parent_window:
            self.parent_window.close()
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_move = event.globalPos()
    
    def mouseMoveEvent(self, event):
        if self.start_move and event.buttons() == Qt.LeftButton:
            if self.parent_window and not self.parent_window.isFullScreen():
                delta = event.globalPos() - self.start_move
                self.parent_window.move(self.parent_window.pos() + delta)
                self.start_move = event.globalPos()
    
    def mouseReleaseEvent(self, event):
        self.start_move = None


class LabClient(QMainWindow):
    frame_ready = pyqtSignal(object)  # QImage
    
    def __init__(self):
        super().__init__()
        self._camera = CameraWorker()
        self._camera.frame_ready.connect(self._on_frame, Qt.QueuedConnection)
        self._camera.status.connect(lambda s: print(f"[Camera] {s}"), Qt.QueuedConnection)
        self.init_ui()
        self.connect_server()
        self._camera.start()
    
    def _on_frame(self, img):
        """UI线程显示图像"""
        if img and not img.isNull():
            pixmap = QPixmap.fromImage(img)
            scaled = pixmap.scaled(
                self.camera_label.width(),
                self.camera_label.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.camera_label.setPixmap(scaled)
    
    def init_ui(self):
        self.setWindowTitle("实验室安全监控系统")
        self.setGeometry(100, 100, 1024, 700)
        self.setMinimumSize(800, 550)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setStyleSheet("""
            QMainWindow { background: qlineargradient(x1:0, y1:0, x1:1, y2:1, stop:0 #1a1a2e, stop:1 #16213e); }
            QGroupBox { color: #fff; font-size: 13px; border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; margin-top: 10px; padding-top: 10px; font-weight: bold; }
            QPushButton { padding: 10px 20px; background: #2196F3; color: white; border: none; border-radius: 5px; font-size: 13px; }
            QPushButton:hover { background: #1976D2; }
        """)
        title = TitleBar(self)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(title)
        content = QWidget()
        content_layout = QHBoxLayout(content)
        video_box = QGroupBox("📹 视频监控")
        video_layout = QVBoxLayout()
        self.camera_label = QLabel("摄像头加载中...")
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setMinimumSize(640, 360)
        self.camera_label.setStyleSheet("QLabel { background: rgba(0,0,0,0.5); border: 1px solid rgba(255,255,255,0.2); border-radius: 8px; }")
        video_layout.addWidget(self.camera_label)
        video_box.setLayout(video_layout)
        content_layout.addWidget(video_box, 3)
        ctrl_box = QGroupBox("⚙️ 控制面板")
        ctrl_layout = QVBoxLayout()
        self.fire_toggle = QPushButton("🔥 火灾检测")
        self.fire_toggle.setCheckable(True)
        self.fire_toggle.clicked.connect(self._toggle_fire)
        ctrl_layout.addWidget(self.fire_toggle)
        self.ai_btn = QPushButton("🤖 AI分析")
        self.ai_btn.clicked.connect(self._analyze)
        ctrl_layout.addWidget(self.ai_btn)
        self.status_label = QLabel("状态: 正常运行")
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        ctrl_layout.addWidget(self.status_label)
        ctrl_layout.addStretch()
        ctrl_box.setLayout(ctrl_layout)
        content_layout.addWidget(ctrl_box, 1)
        layout.addWidget(content)
        self.ws_thread = WebSocketThread()
        self.ws_thread.message_received.connect(self._on_message)
        self.ws_thread.start()
    
    def _toggle_fire(self, checked):
        self.fire_toggle.setText("🔥 ON" if checked else "🔥 OFF")
        try:
            requests.post(f"http://127.0.0.1:5000/api/fire/{'start' if checked else 'stop'}", timeout=2)
        except: pass
    
    def _analyze(self):
        self.status_label.setText("分析中...")
        try:
            resp = requests.post("http://127.0.0.1:5000/api/ai/analyze", json={"camera": "usb-camera"}, timeout=30)
            result = resp.json()
            self.status_label.setText("分析完成" if result.get("success") else f"失败: {result.get('error', 'unknown')}")
        except Exception as e:
            self.status_label.setText(f"请求失败: {e}")
    
    def _on_message(self, data):
        if data.get("type") == "alert":
            self.status_label.setText(f"⚠️ {data.get('content')}")
            self.status_label.setStyleSheet("color: #f44336; font-weight: bold;")
    
    def connect_server(self):
        pass  # WebSocket handled by thread
    
    def closeEvent(self, event):
        self._camera.stop()
        self.ws_thread.running = False
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    window = LabClient()
    window.show()
    sys.exit(app.exec_())
