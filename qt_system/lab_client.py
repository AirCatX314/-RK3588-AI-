#!/usr/bin/env python3
"""
实验室安全监控系统 - Qt工控客户端
带自定义标题栏 + 全屏支持
"""

import sys
import json
import asyncio
import threading
import datetime
import time
import os
import subprocess
import select
import cv2
import numpy as np
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtNetwork import *
from http.server import HTTPServer, BaseHTTPRequestHandler
import websockets
import requests
import io

SERVER_IP = "127.0.0.1"
SERVER_PORT = 8765
SHARE_PORT = 5001  # Qt共享视频流的端口
CAMERA_WIDTH = int(os.environ.get("LABSAFE_CAMERA_WIDTH", "1920"))
CAMERA_HEIGHT = int(os.environ.get("LABSAFE_CAMERA_HEIGHT", "1080"))
CAMERA_FPS = int(os.environ.get("LABSAFE_CAMERA_FPS", "30"))
DISPLAY_FPS = float(os.environ.get("LABSAFE_DISPLAY_FPS", "30"))
PUSH_FPS = float(os.environ.get("LABSAFE_PUSH_FPS", "20"))
JPEG_QUALITY = int(os.environ.get("LABSAFE_JPEG_QUALITY", "90"))
PUSH_WIDTH = int(os.environ.get("LABSAFE_PUSH_WIDTH", "1280"))
PUSH_HEIGHT = int(os.environ.get("LABSAFE_PUSH_HEIGHT", "720"))
V4L2_BATCH_FRAMES = int(os.environ.get("LABSAFE_V4L2_BATCH_FRAMES", "5"))
CAPTURE_BACKEND = os.environ.get("LABSAFE_CAPTURE_BACKEND", "gstlaunch")


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
                async with websockets.connect(f"ws://{SERVER_IP}:{SERVER_PORT}") as ws:
                    self.ws = ws
                    self.connected.emit()
                    print("已连接到服务器")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            self.message_received.emit(data)
                        except:
                            pass
            except Exception as e:
                print(f"连接断开: {e}")
                self.disconnected.emit()
            if self.running:
                await asyncio.sleep(3)
    
    def send_message(self, msg: dict):
        if self.ws:
            try:
                asyncio.run(self.ws.send(json.dumps(msg)))
            except:
                pass
    
    def stop(self):
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except:
                pass


class TitleBar(QWidget):
    """自定义标题栏"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setFixedHeight(45)
        self.setup_ui()
        self.start_move = None
        
    def setup_ui(self):
        self.setStyleSheet("""
            TitleBar {
                background: #fbfbfa;
                border-bottom: 1px solid #deded9;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 8, 0)
        layout.setSpacing(8)
        
        # 标题
        title = QLabel("LabSafe Device Panel")
        title.setStyleSheet("color: #18181b; font-size: 15px; font-weight: 800;")
        layout.addWidget(title)
        
        layout.addStretch()
        
        # 按钮容器
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(2)
        
        # 最小化按钮
        self.min_btn = QPushButton()
        self.min_btn.setFixedSize(40, 35)
        self.min_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.min_btn.setToolTip("最小化")
        self.min_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #52525b;
                border: none;
                border-radius: 4px;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #efefec;
            }
            QPushButton:pressed {
                background: #e4e4e0;
            }
        """)
        self.min_btn.clicked.connect(self.minimize_window)
        btn_layout.addWidget(self.min_btn)
        
        # 最大化/全屏按钮
        self.max_btn = QPushButton()
        self.max_btn.setFixedSize(40, 35)
        self.max_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.max_btn.setToolTip("全屏")
        self.max_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #52525b;
                border: none;
                border-radius: 4px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: #efefec;
            }
            QPushButton:pressed {
                background: #e4e4e0;
            }
        """)
        self.max_btn.clicked.connect(self.toggle_maximize)
        self.max_btn.setText("☐")
        btn_layout.addWidget(self.max_btn)
        
        # 关闭按钮
        self.close_btn = QPushButton()
        self.close_btn.setFixedSize(40, 35)
        self.close_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.close_btn.setToolTip("关闭")
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #52525b;
                border: none;
                border-radius: 4px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: #fef2f2;
                color: #dc2626;
            }
            QPushButton:pressed {
                background: #fee2e2;
                color: #b91c1c;
            }
        """)
        self.close_btn.clicked.connect(self.close_window)
        btn_layout.addWidget(self.close_btn)
        
        layout.addLayout(btn_layout)
        
    def update_buttons(self, is_fullscreen):
        """更新按钮状态"""
        if is_fullscreen:
            self.max_btn.setText("❐")
            self.max_btn.setToolTip("退出全屏")
        else:
            self.max_btn.setText("☐")
            self.max_btn.setToolTip("全屏")
        
    def minimize_window(self):
        if self.parent_window:
            self.parent_window.showMinimized()
    
    def toggle_maximize(self):
        if self.parent_window:
            if self.parent_window.isFullScreen():
                self.parent_window.showNormal()
                self.max_btn.setText("☐")
                self.max_btn.setToolTip("全屏")
            else:
                self.parent_window.showFullScreen()
                self.max_btn.setText("❐")
                self.max_btn.setToolTip("退出全屏")
    
    def close_window(self):
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
    
    # 在类初始化时添加信号
    camera_ready = pyqtSignal(object)
    frame_shared = pyqtSignal(object)  # 用于共享视频流
    fire_alarm_changed = pyqtSignal(bool, str)
    environment_status_changed = pyqtSignal(dict)
    emergency_call_status_changed = pyqtSignal(dict)
    agent_status_changed = pyqtSignal(dict)
    agent_chat_response_received = pyqtSignal(dict)
    agent_models_changed = pyqtSignal(dict)
    agent_action_result_received = pyqtSignal(dict)
    agent_upload_result_received = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.ws_thread = None
        self._camera_thread = None
        self._push_thread = None
        self._camera_stop = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame = None
        self._latest_jpeg = None
        self._latest_push_seq = 0
        self._pushed_seq = 0
        self._share_server = None
        self._current_frame = None
        self._push_session = None
        self._camera_proc = None
        self._last_camera_log_ts = 0.0
        self._annotated_session = None
        self._annotated_thread = None
        self._detection_session = None
        self._detection_thread = None
        self._detections_lock = threading.Lock()
        self._latest_detections = []
        self._detection_source_size = (CAMERA_WIDTH, CAMERA_HEIGHT)
        self._last_display_ts = 0
        self._last_detected_display_ts = 0
        self._display_interval = 1 / max(DISPLAY_FPS, 1.0)
        self._push_interval = 1 / max(PUSH_FPS, 1.0)
        self._last_push_ts = 0
        self._fire_alarm_active = False
        self._fire_alarm_reason = ''
        self._last_alarm_beep_ts = 0.0
        self._last_alarm_message_ts = 0.0
        self._last_status_fetch_ts = 0.0
        self._emergency_call_active = False
        self._emergency_call_state = 'unknown'
        self._call_status_busy = False
        self._call_action_busy = False
        self.agent_session_id = os.environ.get("LABSAFE_AGENT_SESSION_ID", "qt:lab-device-1")
        self._agent_status_busy = False
        self._agent_chat_busy = False
        self._agent_model_busy = False
        self._agent_confirm_busy = False
        self._agent_upload_busy = False
        self.agent_pending_uploads = []
        self._camera_label_normal_style = """
            QLabel {
                background: #0f0f10;
                color: #a1a1aa;
                border: 1px solid #deded9;
                border-radius: 8px;
            }
        """
        self._camera_label_alarm_style = """
            QLabel {
                background: #1f0a0a;
                color: #ffffff;
                border: 3px solid #dc2626;
                border-radius: 8px;
            }
        """
        self.detection_enabled = True
        # 连接摄像头信号
        self.camera_ready.connect(self._update_camera_display)
        self.frame_shared.connect(self._share_frame)
        self.fire_alarm_changed.connect(self._apply_fire_alarm_ui)
        self.environment_status_changed.connect(self._apply_environment_status)
        self.emergency_call_status_changed.connect(self._apply_emergency_call_status)
        self.agent_status_changed.connect(self._apply_agent_status)
        self.agent_chat_response_received.connect(self._apply_agent_chat_response)
        self.agent_models_changed.connect(self._apply_agent_models)
        self.agent_action_result_received.connect(self._apply_agent_action_result)
        self.agent_upload_result_received.connect(self._apply_agent_upload_result)
        self.init_ui()
        self.connect_server()
        self.start_share_server()
        self.call_status_timer = QTimer(self)
        self.call_status_timer.timeout.connect(self.refresh_emergency_call_status)
        self.call_status_timer.start(5000)
        self.agent_status_timer = QTimer(self)
        self.agent_status_timer.timeout.connect(self.refresh_agent_status)
        self.agent_status_timer.start(5000)
        QTimer.singleShot(1000, self.refresh_emergency_call_status)
        QTimer.singleShot(1200, self.refresh_agent_status)
        QTimer.singleShot(0, self.update_camera)
    
    def _share_frame(self, frame_bytes):
        """共享视频帧"""
        self._current_frame = frame_bytes
    
    def start_share_server(self):
        """启动HTTP共享服务器"""
        class VideoHandler(BaseHTTPRequestHandler):
            current_frame = None
            
            def do_GET(self):
                if self.path == '/stream':
                    self.send_response(200)
                    self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                    self.end_headers()
                    
                    while True:
                        try:
                            frame = self.server.shared_instance._current_frame
                            if frame:
                                self.wfile.write(b'--frame\r\n')
                                self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                                self.wfile.write(frame)
                                self.wfile.write(b'\r\n')
                            time.sleep(0.033)
                        except:
                            break
            
            def log_message(self, format, *args):
                pass  # 抑制日志
        
        class ShareServer(HTTPServer):
            shared_instance = None
        
        VideoHandler.shared_instance = self
        ShareServer.shared_instance = self
        
        def run_server():
            ShareServer(('', SHARE_PORT), VideoHandler).serve_forever()
        
        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        print(f"Video share server started on port {SHARE_PORT}")
    
    def _update_camera_display(self, image):
        """在主线程更新摄像头显示 - 自适应容器"""
        if isinstance(image, QImage):
            pixmap = QPixmap.fromImage(image)
        elif isinstance(image, QPixmap):
            pixmap = image
        else:
            return

        if pixmap and not pixmap.isNull():
            # 自适应容器大小
            scaled = pixmap.scaled(
                self.camera_label.width(),
                self.camera_label.height(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            )
            self.camera_label.setPixmap(scaled)
    
    def toggle_detection(self, state):
        """切换检测模式"""
        self.detection_enabled = (state == 2)  # 2 = Qt.Checked
    
    def toggle_detection_switch(self, checked, btn):
        """开关按钮切换检测模式"""
        self.detection_enabled = checked
        btn.setText(f"🔍 目标检测: {'开' if checked else '关'}")
        # 重新启动视频流
        if hasattr(self, '_mjpeg_thread') and self._mjpeg_thread:
            self._mjpeg_running = False
            time.sleep(0.2)
        self.start_mjpeg_stream()
    
    def toggle_detection_ios(self, checked):
        """iOS风格小开关按钮"""
        self.detection_enabled = checked
        if checked:
            self.detection_btn.setText("🔍")
            self.detection_btn.setStyleSheet("""
                QPushButton {
                    background-color: #18181b;
                    color: #ffffff;
                    border-radius: 20px;
                    font-size: 18px;
                    border: 1px solid #18181b;
                }
            """)
        else:
            self.detection_btn.setText("🔍")
            self.detection_btn.setStyleSheet("""
                QPushButton {
                    background-color: #ffffff;
                    color: #52525b;
                    border-radius: 20px;
                    font-size: 18px;
                    border: 1px solid #cfcfca;
                }
                QPushButton:hover {
                    background-color: #efefec;
                }
            """)
    
    def start_mjpeg_stream(self):
        """兼容旧调用：实际摄像头由 update_camera 的异步管线管理。"""
        self.update_camera()
    
    def update_camera(self):
        """刷新视频流"""
        self.start_mjpeg_stream()
        self.last_frame_time = time.time()
    
    def check_video_stream(self):
        """检查视频流是否正常"""
        import time
        if hasattr(self, 'last_frame_time'):
            # 如果超过10秒没有新帧，重启视频流
            if time.time() - self.last_frame_time > 10:
                self.start_mjpeg_stream()
                self.last_frame_time = time.time()
        
    def init_ui(self):
        self.setWindowTitle("LabSafe Device Safety Panel")
        self.setGeometry(100, 100, 1180, 760)
        self.setMinimumSize(980, 620)
        
        # 无边框窗口
        self.setWindowFlags(Qt.FramelessWindowHint)
        
        # 全局样式
        self.setStyleSheet("""
            QMainWindow { background: #f4f4f2; }
            QWidget { color: #18181b; font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 13px; }
            QGroupBox {
                color: #18181b;
                font-size: 13px;
                border: 1px solid #deded9;
                border-radius: 8px;
                margin-top: 12px;
                padding: 14px;
                font-weight: 700;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #52525b;
                background: #ffffff;
            }
            QListWidget {
                background: #ffffff;
                color: #18181b;
                border: 1px solid #deded9;
                border-radius: 8px;
                padding: 6px;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #eeeeec;
            }
            QLineEdit, QComboBox {
                padding: 10px 12px;
                border-radius: 8px;
                border: 1px solid #cfcfca;
                background: #ffffff;
                color: #18181b;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #18181b;
            }
            QLineEdit::placeholder {
                color: #a1a1aa;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #18181b;
                border: 1px solid #cfcfca;
                selection-background-color: #efefec;
                selection-color: #18181b;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: #c7c7c3;
                border-radius: 4px;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 0px;
            }
            QScrollBar::handle:horizontal {
                background: transparent;
                height: 0px;
            }
            QPushButton {
                padding: 10px 16px;
                background: #ffffff;
                color: #18181b;
                border: 1px solid #cfcfca;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 620;
            }
            QPushButton:hover { background: #efefec; border-color: #c4c4bf; }
            QPushButton:checked { background: #18181b; border-color: #18181b; color: #ffffff; }
            QPushButton:disabled { color: #a1a1aa; background: #f4f4f2; border-color: #deded9; }
            QCheckBox { color: #18181b; spacing: 8px; }
            QCheckBox::indicator:checked { background: #18181b; border: 1px solid #18181b; }
        """)
        
        # 主widget
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        # 主布局：垂直
        main_layout = QVBoxLayout(main_widget)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # 标题栏
        self.title_bar = TitleBar(self)
        main_layout.addWidget(self.title_bar)

        top_status = QFrame()
        top_status.setObjectName("deviceTopStatus")
        top_status.setStyleSheet("""
            QFrame#deviceTopStatus {
                background: #fbfbfa;
                border-bottom: 1px solid #deded9;
            }
        """)
        top_layout = QHBoxLayout(top_status)
        top_layout.setContentsMargins(18, 8, 18, 8)
        top_layout.setSpacing(12)
        self.device_risk_label = QLabel("● NORMAL")
        self.device_risk_label.setStyleSheet("color: #15803d; background: #f0fdf4; font-weight: 800; padding: 4px 10px; border: 1px solid #bbf7d0; border-radius: 12px;")
        top_layout.addWidget(self.device_risk_label)
        self.device_service_label = QLabel("service active · camera starting · agent checking")
        self.device_service_label.setStyleSheet("color: #52525b;")
        top_layout.addWidget(self.device_service_label, 1)
        self.device_clock_label = QLabel("--:--:--")
        self.device_clock_label.setStyleSheet("color: #71717a; font-family: Consolas;")
        top_layout.addWidget(self.device_clock_label)
        main_layout.addWidget(top_status)
        self.device_clock_timer = QTimer(self)
        self.device_clock_timer.timeout.connect(lambda: self.device_clock_label.setText(datetime.datetime.now().strftime("%H:%M:%S")))
        self.device_clock_timer.start(1000)
        self.device_clock_label.setText(datetime.datetime.now().strftime("%H:%M:%S"))
        
        # 内容布局：左侧导航 + 右侧内容
        content_layout = QHBoxLayout()
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(0, 0, 0, 0)
        
        # 左侧导航栏
        sidebar = QFrame()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("""
            QFrame {
                background: #f7f7f5;
                border-right: 1px solid #deded9;
            }
        """)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setSpacing(0)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        
        # Logo
        logo_frame = QFrame()
        logo_layout = QVBoxLayout(logo_frame)
        logo_layout.setContentsMargins(10, 15, 10, 15)
        
        logo_label = QLabel("LS")
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet("font-size: 22px; font-weight: 900; color: #18181b;")
        logo_layout.addWidget(logo_label)
        
        title_label = QLabel("LabSafe")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #18181b; font-size: 14px; font-weight: 800;")
        logo_layout.addWidget(title_label)
        subtitle_label = QLabel("Device Safety Panel")
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet("color: #71717a; font-size: 11px;")
        logo_layout.addWidget(subtitle_label)
        
        sidebar_layout.addWidget(logo_frame)
        
        # 导航按钮
        self.nav_buttons = []
        nav_items = [
            ("", "监控面板", 0),
            ("", "视频监控", 1),
            ("", "消息中心", 2),
            ("", "Agent助手", 3),
            ("", "系统设置", 4),
        ]
        
        for icon, text, index in nav_items:
            btn = QPushButton(f"  {text}")
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #52525b;
                    border: 1px solid transparent;
                    border-radius: 8px;
                    padding: 11px 14px;
                    font-size: 14px;
                    text-align: left;
                }
                QPushButton:hover {
                    background: #efefec;
                    color: #18181b;
                }
                QPushButton:checked, QPushButton:pressed {
                    background: #ffffff;
                    color: #18181b;
                    border: 1px solid #cfcfca;
                }
            """)
            btn.clicked.connect(lambda checked, i=index: self.switch_page(i))
            self.nav_buttons.append(btn)
            sidebar_layout.addWidget(btn)
        
        sidebar_layout.addStretch()
        
        # 状态
        self.status_label = QLabel("● 未连接")
        self.status_label.setStyleSheet("""
            color: #71717a;
            font-size: 12px;
            padding: 12px 15px;
            border-top: 1px solid #deded9;
        """)
        sidebar_layout.addWidget(self.status_label)
        
        content_layout.addWidget(sidebar)
        
        # 右侧内容区
        self.content_stack = QStackedWidget()
        content_layout.addWidget(self.content_stack, 1)
        
        main_layout.addLayout(content_layout)
        
        # 页面0: 监控面板
        self.page_monitor = self.create_monitor_page()
        self.content_stack.addWidget(self.page_monitor)
        
        # 页面1: 视频监控
        self.page_video = self.create_video_page()
        self.content_stack.addWidget(self.page_video)
        
        # 页面2: 消息中心
        self.page_messages = self.create_messages_page()
        self.content_stack.addWidget(self.page_messages)

        # 页面3: Agent 助手
        self.page_agent = self.create_agent_page()
        self.content_stack.addWidget(self.page_agent)
        
        # 页面4: 系统设置
        self.page_settings = self.create_settings_page()
        self.content_stack.addWidget(self.page_settings)
        
        self.nav_buttons[0].setChecked(True)
        
    def create_monitor_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("实时监控")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #18181b;")
        layout.addWidget(title)
        
        # 监控信息
        info_group = QGroupBox("监控信息")
        info_layout = QGridLayout()
        info_layout.setSpacing(15)
        
        info_layout.addWidget(QLabel("环境温度:"), 0, 0)
        self.temp_label = QLabel("25.0°C")
        self.temp_label.setStyleSheet("font-size: 16px; color: #15803d; font-weight: 800;")
        info_layout.addWidget(self.temp_label, 0, 1)
        
        info_layout.addWidget(QLabel("环境湿度:"), 0, 2)
        self.humidity_label = QLabel("--%")
        self.humidity_label.setStyleSheet("font-size: 16px; color: #0369a1; font-weight: 800;")
        info_layout.addWidget(self.humidity_label, 0, 3)

        info_layout.addWidget(QLabel("火灾检测:"), 1, 0)
        self.fire_label = QLabel("正常")
        self.fire_label.setStyleSheet("font-size: 16px; color: #15803d; font-weight: 800;")
        info_layout.addWidget(self.fire_label, 1, 1)
        
        info_layout.addWidget(QLabel("摄像头:"), 1, 2)
        self.camera_label = QLabel("在线")
        self.camera_label.setStyleSheet("font-size: 16px; color: #15803d; font-weight: 800;")
        info_layout.addWidget(self.camera_label, 1, 3)
        
        info_layout.addWidget(QLabel("系统状态:"), 2, 0)
        self.sys_label = QLabel("运行中")
        self.sys_label.setStyleSheet("font-size: 16px; color: #15803d; font-weight: 800;")
        info_layout.addWidget(self.sys_label, 2, 1)
        
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # 消息记录
        msg_group = QGroupBox("消息记录")
        msg_layout = QVBoxLayout()
        
        self.msg_list = QListWidget()
        msg_layout.addWidget(self.msg_list)
        msg_group.setLayout(msg_layout)
        layout.addWidget(msg_group, 1)
        
        # 发送消息
        send_group = QGroupBox("发送消息")
        send_layout = QHBoxLayout()
        
        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("输入消息内容...")
        send_layout.addWidget(self.msg_input, 1)
        
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self.send_normal_message)
        send_layout.addWidget(send_btn)
        
        send_group.setLayout(send_layout)
        layout.addWidget(send_group)
        
        call_status_layout = QHBoxLayout()
        call_status_layout.addWidget(QLabel("应急通话:"))
        self.call_status_label = QLabel("未就绪")
        self.call_status_label.setStyleSheet("font-size: 15px; color: #b45309; font-weight: 800;")
        call_status_layout.addWidget(self.call_status_label, 1)
        layout.addLayout(call_status_layout)
        
        # 紧急按钮
        emergency_layout = QHBoxLayout()
        
        emergency_btn = QPushButton("紧急求助")
        emergency_btn.setMinimumHeight(45)
        emergency_btn.setStyleSheet("""
            QPushButton {
                background: #fffbeb;
                color: #b45309;
                border: 1px solid #fde68a;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover { background: #fef3c7; }
        """)
        emergency_btn.clicked.connect(self.send_emergency)
        emergency_layout.addWidget(emergency_btn)
        
        fire_btn = QPushButton("应急警报")
        fire_btn.setMinimumHeight(45)
        fire_btn.setStyleSheet("""
            QPushButton {
                background: #fef2f2;
                color: #dc2626;
                border: 1px solid #fecaca;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover { background: #fee2e2; }
        """)
        fire_btn.clicked.connect(self.send_fire_alert)
        emergency_layout.addWidget(fire_btn)
        
        self.call_btn = QPushButton("拨打管理员")
        self.call_btn.setMinimumHeight(45)
        self.call_btn.setStyleSheet("""
            QPushButton {
                background: #18181b;
                color: #ffffff;
                border: 1px solid #18181b;
                font-size: 15px;
                font-weight: bold;
            }
            QPushButton:hover { background: #27272a; }
            QPushButton:disabled { background: #f4f4f2; color: #a1a1aa; border-color: #deded9; }
        """)
        self.call_btn.clicked.connect(self.toggle_emergency_call)
        emergency_layout.addWidget(self.call_btn)
        
        layout.addLayout(emergency_layout)
        
        return page
    
    def create_video_page(self):
        """创建视频监控页面"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("视频监控")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #18181b;")
        layout.addWidget(title)
        
        # 视频显示 - 无边框
        self.camera_label = QLabel("正在加载视频...")
        self.camera_label.setScaledContents(False)
        self.camera_label.setAlignment(Qt.AlignCenter)
        self.camera_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.camera_label.setStyleSheet(self._camera_label_normal_style)
        layout.addWidget(self.camera_label, 1)  # stretch factor = 1
        
        # 刷新按钮
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedSize(80, 35)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #ffffff;
                color: #18181b;
                border-radius: 8px;
                font-size: 14px;
                border: 1px solid #cfcfca;
            }
            QPushButton:hover {
                background: #efefec;
            }
        """)
        self.refresh_btn.clicked.connect(self.update_camera)
        
        # 底部添加刷新按钮
        layout.addWidget(self.refresh_btn)
        
        return page
    
    def create_messages_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("消息中心")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #18181b;")
        layout.addWidget(title)
        
        msg_group = QGroupBox("全部消息")
        msg_layout = QVBoxLayout()
        
        self.all_msg_list = QListWidget()
        msg_layout.addWidget(self.all_msg_list)
        msg_group.setLayout(msg_layout)
        layout.addWidget(msg_group, 1)
        
        send_group = QGroupBox("发送消息")
        send_layout = QHBoxLayout()
        
        self.msg_input2 = QLineEdit()
        self.msg_input2.setPlaceholderText("输入消息内容...")
        send_layout.addWidget(self.msg_input2, 1)
        
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(lambda: self.send_msg(self.msg_input2))
        send_layout.addWidget(send_btn)
        
        send_group.setLayout(send_layout)
        layout.addWidget(send_group)
        
        return page

    def create_agent_page(self):
        page = QWidget()
        page.setObjectName("agentPage")
        page.setStyleSheet("""
            QWidget#agentPage {
                background: #f4f4f2;
                color: #18181b;
            }
            QFrame#agentHeader,
            QFrame#agentActionsFrame,
            QFrame#agentComposer {
                background: #ffffff;
                border: 1px solid #deded9;
                border-radius: 8px;
            }
            QLabel {
                color: #18181b;
                background: transparent;
                border: none;
            }
            QPushButton {
                background: #ffffff;
                color: #18181b;
                border: 1px solid #cfcfca;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 620;
            }
            QPushButton:hover { background: #efefec; }
            QPushButton:checked {
                background: #18181b;
                border: 1px solid #18181b;
                color: #ffffff;
            }
            QPushButton:disabled {
                color: #a1a1aa;
                background: #f4f4f2;
                border-color: #deded9;
            }
            QPushButton#agentSendBtn {
                background: #18181b;
                color: #ffffff;
                border: 1px solid #18181b;
                border-radius: 8px;
                font-weight: bold;
            }
            QLineEdit, QComboBox {
                background: #ffffff;
                color: #18181b;
                border: 1px solid #cfcfca;
                border-radius: 8px;
                padding: 8px 10px;
            }
            QLineEdit:focus, QComboBox:focus { border-color: #18181b; }
            QComboBox QAbstractItemView {
                background: #ffffff;
                color: #18181b;
                border: 1px solid #cfcfca;
                selection-background-color: #efefec;
                selection-color: #18181b;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 4px 0;
            }
            QScrollBar::handle:vertical {
                background: #c7c7c3;
                border-radius: 4px;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 0px;
            }
            QScrollBar::handle:horizontal {
                background: transparent;
                height: 0px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0; }
        """)
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        header = QFrame()
        header.setObjectName("agentHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("LabSafe Agent")
        title.setStyleSheet("font-size: 20px; font-weight: 800; color: #18181b;")
        subtitle = QLabel("对话、实验室状态、联网搜索和模型切换")
        subtitle.setStyleSheet("font-size: 12px; color: #71717a;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)

        self.agent_risk_label = QLabel("--")
        self.agent_risk_label.setAlignment(Qt.AlignCenter)
        self.agent_risk_label.setMinimumWidth(82)
        self.agent_risk_label.setStyleSheet("padding: 6px 10px; border-radius: 13px; background: #f0fdf4; color: #15803d; font-weight: 800; border: 1px solid #bbf7d0;")
        header_layout.addWidget(self.agent_risk_label)

        self.agent_model_label = QLabel("--")
        self.agent_model_label.setStyleSheet("padding: 6px 10px; border-radius: 13px; background: #fafafa; color: #52525b; border: 1px solid #deded9;")
        header_layout.addWidget(self.agent_model_label)

        self.agent_service_label = QLabel("--")
        self.agent_service_label.setStyleSheet("color: #71717a;")
        header_layout.addWidget(self.agent_service_label)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(76)
        refresh_btn.clicked.connect(self.refresh_agent_status)
        header_layout.addWidget(refresh_btn)
        layout.addWidget(header)

        self.agent_reason_label = QLabel("--")
        self.agent_reason_label.setWordWrap(True)
        self.agent_reason_label.setStyleSheet("color: #52525b; padding: 0 4px;")
        layout.addWidget(self.agent_reason_label)

        self.agent_thread_scroll = QScrollArea()
        self.agent_thread_scroll.setWidgetResizable(True)
        self.agent_thread_scroll.setFrameShape(QFrame.NoFrame)
        self.agent_thread_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        thread_body = QWidget()
        thread_body.setStyleSheet("background: transparent;")
        self.agent_chat_layout = QVBoxLayout(thread_body)
        self.agent_chat_layout.setContentsMargins(4, 4, 4, 4)
        self.agent_chat_layout.setSpacing(14)
        self.agent_chat_layout.addStretch()
        self.agent_thread_scroll.setWidget(thread_body)
        layout.addWidget(self.agent_thread_scroll, 1)

        actions_frame = QFrame()
        actions_frame.setObjectName("agentActionsFrame")
        self.agent_actions_layout = QHBoxLayout(actions_frame)
        self.agent_actions_layout.setContentsMargins(12, 8, 12, 8)
        self.agent_actions_layout.setSpacing(8)
        empty_action_label = QLabel("暂无待确认动作")
        empty_action_label.setStyleSheet("color: #71717a;")
        self.agent_actions_layout.addWidget(empty_action_label)
        self.agent_actions_layout.addStretch()
        layout.addWidget(actions_frame)

        composer = QFrame()
        composer.setObjectName("agentComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(12, 10, 12, 10)
        composer_layout.setSpacing(8)

        self.agent_input = QLineEdit()
        self.agent_input.setPlaceholderText("直接提问，或询问当前实验室状态")
        self.agent_input.setMinimumHeight(42)
        self.agent_input.returnPressed.connect(self.send_agent_message)
        composer_layout.addWidget(self.agent_input)

        upload_row = QHBoxLayout()
        upload_row.setSpacing(8)
        self.agent_upload_btn = QPushButton("上传附件")
        self.agent_upload_btn.setFixedWidth(92)
        self.agent_upload_btn.clicked.connect(self.upload_agent_attachment)
        upload_row.addWidget(self.agent_upload_btn)
        self.agent_upload_label = QLabel("未选择附件")
        self.agent_upload_label.setStyleSheet("color: #71717a; font-size: 12px;")
        self.agent_upload_label.setWordWrap(True)
        upload_row.addWidget(self.agent_upload_label, 1)
        composer_layout.addLayout(upload_row)

        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        self.agent_deep_think_btn = QPushButton("深度思考")
        self.agent_deep_think_btn.setCheckable(True)
        self.agent_deep_think_btn.setFixedWidth(92)
        tool_row.addWidget(self.agent_deep_think_btn)

        self.agent_web_search_btn = QPushButton("联网搜索")
        self.agent_web_search_btn.setCheckable(True)
        self.agent_web_search_btn.setFixedWidth(92)
        tool_row.addWidget(self.agent_web_search_btn)

        self.agent_provider_combo = QComboBox()
        self.agent_provider_combo.setMinimumWidth(130)
        self.agent_provider_combo.currentIndexChanged.connect(self._on_agent_provider_changed)
        tool_row.addWidget(self.agent_provider_combo)

        self.agent_model_input = QLineEdit()
        self.agent_model_input.setPlaceholderText("模型名")
        self.agent_model_input.setMaximumWidth(190)
        tool_row.addWidget(self.agent_model_input)

        self.agent_apply_model_btn = QPushButton("应用")
        self.agent_apply_model_btn.clicked.connect(self.select_agent_model)
        self.agent_apply_model_btn.setFixedWidth(62)
        tool_row.addWidget(self.agent_apply_model_btn)

        self.agent_test_model_btn = QPushButton("测试")
        self.agent_test_model_btn.clicked.connect(self.test_agent_model)
        self.agent_test_model_btn.setFixedWidth(62)
        tool_row.addWidget(self.agent_test_model_btn)

        self.agent_model_hint = QLabel("--")
        self.agent_model_hint.setStyleSheet("color: #71717a; font-size: 12px;")
        self.agent_model_hint.setWordWrap(True)
        tool_row.addWidget(self.agent_model_hint, 1)

        self.agent_send_btn = QPushButton("发送")
        self.agent_send_btn.setObjectName("agentSendBtn")
        self.agent_send_btn.clicked.connect(self.send_agent_message)
        self.agent_send_btn.setFixedSize(68, 38)
        tool_row.addWidget(self.agent_send_btn)
        composer_layout.addLayout(tool_row)
        layout.addWidget(composer)

        self.agent_thinking_timer = QTimer(self)
        self.agent_thinking_timer.timeout.connect(self._update_agent_thinking)
        self.agent_thinking_label = None
        self.agent_thinking_base = ""
        self.agent_thinking_step = 0

        self._add_agent_chat_item("Agent", "你好，我是 LabSafe Agent。你可以直接提问。", "#18181b")

        return page
    
    def create_settings_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)
        
        title = QLabel("系统设置")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #18181b;")
        layout.addWidget(title)
        
        server_group = QGroupBox("服务器配置")
        server_layout = QFormLayout()
        
        server_addr = QLineEdit(f"{SERVER_IP}:{SERVER_PORT}")
        server_layout.addRow("服务器地址:", server_addr)
        
        server_group.setLayout(server_layout)
        layout.addWidget(server_group)
        
        func_group = QGroupBox("功能配置")
        func_layout = QVBoxLayout()
        
        # 目标检测开关
        self.detection_btn = QCheckBox("目标检测")
        self.detection_btn.setChecked(True)
        self.detection_btn.setStyleSheet("color: #18181b; font-size: 14px;")
        self.detection_btn.stateChanged.connect(self.toggle_detection)
        func_layout.addWidget(self.detection_btn)
        
        for text, checked in [("启用语音播报", True), ("自动连接服务器", True), ("启用消息通知", True)]:
            cb = QCheckBox(text)
            cb.setChecked(checked)
            cb.setStyleSheet("color: #18181b;")
            func_layout.addWidget(cb)
        
        func_group.setLayout(func_layout)
        layout.addWidget(func_group)
        
        layout.addStretch()
        
        return page
    
    def switch_page(self, index):
        self.content_stack.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    def refresh_agent_status(self):
        if self._agent_status_busy:
            return
        self._agent_status_busy = True
        threading.Thread(target=self._fetch_agent_status, daemon=True).start()

    def _fetch_agent_status(self):
        try:
            status_resp = requests.get('http://127.0.0.1:5000/api/agent/status', timeout=3)
            data = status_resp.json()
            try:
                models_resp = requests.get('http://127.0.0.1:5000/api/agent/models', timeout=3)
                data['models'] = models_resp.json()
            except Exception as model_error:
                data.setdefault('models', {'success': False, 'error': str(model_error)})
        except Exception as e:
            data = {
                'success': False,
                'enabled': False,
                'risk_level': 'unknown',
                'reason': f'Agent 暂不可用: {e}',
                'models': {'success': False, 'error': str(e)}
            }
        finally:
            self._agent_status_busy = False
        self.agent_status_changed.emit(data)

    def _apply_agent_status(self, data):
        if not isinstance(data, dict):
            return
        risk = data.get('risk_level', 'unknown')
        color = {
            'normal': '#15803d',
            'notice': '#0369a1',
            'warning': '#b45309',
            'danger': '#dc2626',
        }.get(risk, '#52525b')
        bg = {
            'normal': '#f0fdf4',
            'notice': '#eff6ff',
            'warning': '#fffbeb',
            'danger': '#fef2f2',
        }.get(risk, '#fafafa')
        border = {
            'normal': '#bbf7d0',
            'notice': '#bfdbfe',
            'warning': '#fde68a',
            'danger': '#fecaca',
        }.get(risk, '#deded9')
        if hasattr(self, 'agent_risk_label'):
            self.agent_risk_label.setText(risk)
            self.agent_risk_label.setStyleSheet(f"padding: 6px 10px; border-radius: 13px; background: {bg}; color: {color}; font-weight: 800; border: 1px solid {border};")
        if hasattr(self, 'device_risk_label'):
            self.device_risk_label.setText(f"● {risk.upper()}")
            self.device_risk_label.setStyleSheet(f"color: {color}; font-weight: 800; padding: 4px 10px; border: 1px solid {border}; border-radius: 12px; background: {bg};")
        models = data.get('models') or {}
        active_provider = models.get('active_provider') or (data.get('model') or {}).get('provider') or 'rules_only'
        active_model = models.get('active_model') or (data.get('model') or {}).get('model') or ''
        if hasattr(self, 'agent_model_label'):
            self.agent_model_label.setText(f"{active_provider} / {active_model}" if active_model else active_provider)
            self.agent_model_label.setStyleSheet("padding: 6px 10px; border-radius: 13px; background: #fafafa; color: #52525b; border: 1px solid #deded9;")
        if hasattr(self, 'agent_service_label'):
            self.agent_service_label.setText(data.get('agent_service', 'standalone') if data.get('success', True) else '不可用')
        if hasattr(self, 'device_service_label'):
            self.device_service_label.setText(f"service active · camera live · agent {data.get('agent_service', 'checking')}")
        if hasattr(self, 'agent_reason_label'):
            self.agent_reason_label.setText(data.get('reason', '--'))
        self._apply_agent_models(models)
        self._render_agent_actions(data.get('pending_actions') or data.get('proposed_actions') or [])

    def _render_agent_actions(self, actions):
        if not hasattr(self, 'agent_actions_layout'):
            return
        while self.agent_actions_layout.count():
            item = self.agent_actions_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        if not actions:
            label = QLabel("暂无待确认动作")
            label.setWordWrap(True)
            label.setStyleSheet("color: #71717a;")
            self.agent_actions_layout.addWidget(label)
            self.agent_actions_layout.addStretch()
            return
        for action in actions:
            box = QFrame()
            box.setStyleSheet("""
                QFrame {
                    background: #fef2f2;
                    border: 1px solid #fecaca;
                    border-radius: 8px;
                }
            """)
            box_layout = QVBoxLayout(box)
            title = QLabel(action.get('title') or action.get('action_type') or '待确认动作')
            title.setStyleSheet("color: #dc2626; font-weight: bold;")
            box_layout.addWidget(title)
            reason = QLabel(action.get('reason', ''))
            reason.setWordWrap(True)
            reason.setStyleSheet("color: #52525b; font-size: 12px;")
            box_layout.addWidget(reason)
            token = action.get('confirmation_token') or action.get('token')
            btn = QPushButton("确认执行")
            btn.setStyleSheet("QPushButton { background: #dc2626; color: #ffffff; border-color: #dc2626; font-weight: bold; } QPushButton:hover { background: #b91c1c; }")
            btn.clicked.connect(lambda checked=False, t=token: self.confirm_agent_action(t))
            box_layout.addWidget(btn)
            self.agent_actions_layout.addWidget(box)
        self.agent_actions_layout.addStretch()

    def upload_agent_attachment(self):
        if self._agent_upload_busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择附件",
            "",
            "Agent 附件 (*.jpg *.jpeg *.png *.webp *.txt *.md *.json *.csv *.pdf *.docx)"
        )
        if not paths:
            return
        self._agent_upload_busy = True
        if hasattr(self, 'agent_upload_btn'):
            self.agent_upload_btn.setEnabled(False)
            self.agent_upload_btn.setText("上传中")
        threading.Thread(target=self._post_agent_uploads, args=(paths,), daemon=True).start()

    def _post_agent_uploads(self, paths):
        uploaded = []
        errors = []
        try:
            for path in paths:
                try:
                    with open(path, 'rb') as f:
                        resp = requests.post(
                            'http://127.0.0.1:5000/api/agent/uploads',
                            files={'file': (os.path.basename(path), f)},
                            timeout=35
                        )
                    data = resp.json()
                    if data.get('success') and data.get('file'):
                        uploaded.append(data['file'])
                    else:
                        errors.append(data.get('error') or os.path.basename(path))
                except Exception as e:
                    errors.append(f"{os.path.basename(path)}: {e}")
        finally:
            self._agent_upload_busy = False
        self.agent_upload_result_received.emit({'uploaded': uploaded, 'errors': errors})

    def _apply_agent_upload_result(self, data):
        if hasattr(self, 'agent_upload_btn'):
            self.agent_upload_btn.setEnabled(True)
            self.agent_upload_btn.setText("上传附件")
        for item in data.get('uploaded') or []:
            self.agent_pending_uploads.append(item)
        if data.get('errors'):
            self.show_toast("附件上传失败: " + "; ".join(data.get('errors')[:2]))
        self._refresh_agent_upload_label()

    def _refresh_agent_upload_label(self):
        if not hasattr(self, 'agent_upload_label'):
            return
        if not self.agent_pending_uploads:
            self.agent_upload_label.setText("未选择附件")
            return
        names = [item.get('name') or item.get('file_id') for item in self.agent_pending_uploads]
        self.agent_upload_label.setText("待发送: " + "、".join(names[:3]) + ("..." if len(names) > 3 else ""))

    def send_agent_message(self):
        if self._agent_chat_busy:
            return
        content = self.agent_input.text().strip() if hasattr(self, 'agent_input') else ''
        if not content and self.agent_pending_uploads:
            content = "请分析我上传的附件"
        if not content:
            return
        self.agent_input.clear()
        upload_snapshot = list(self.agent_pending_uploads)
        self.agent_pending_uploads = []
        self._refresh_agent_upload_label()
        self._add_agent_chat_item("我", content, "#18181b", upload_snapshot)
        self._agent_chat_busy = True
        deep_thinking = self.agent_deep_think_btn.isChecked() if hasattr(self, 'agent_deep_think_btn') else False
        web_search = self.agent_web_search_btn.isChecked() if hasattr(self, 'agent_web_search_btn') else False
        thinking_text = "联网搜索并思考中" if web_search else "思考中"
        self._start_agent_thinking(thinking_text)
        if hasattr(self, 'agent_send_btn'):
            self.agent_send_btn.setEnabled(False)
            self.agent_send_btn.setText("发送中")
        attachment_ids = [item.get('file_id') for item in upload_snapshot if item.get('file_id')]
        threading.Thread(target=self._post_agent_chat, args=(content, deep_thinking, web_search, attachment_ids), daemon=True).start()

    def _post_agent_chat(self, content, deep_thinking=False, web_search=False, attachment_ids=None):
        try:
            resp = requests.post(
                'http://127.0.0.1:5000/api/agent/chat',
                json={
                    'message': content,
                    'sender': 'qt',
                    'session_id': self.agent_session_id,
                    'deep_thinking': bool(deep_thinking),
                    'web_search': bool(web_search),
                    'attachment_ids': attachment_ids or [],
                },
                timeout=75 if (deep_thinking or web_search) else 45
            )
            data = resp.json()
        except Exception as e:
            data = {'success': False, 'reply': f'Agent 暂不可用: {e}', 'risk_level': 'unknown'}
        finally:
            self._agent_chat_busy = False
        self.agent_chat_response_received.emit(data)

    def _apply_agent_chat_response(self, data):
        if hasattr(self, 'agent_send_btn'):
            self.agent_send_btn.setEnabled(True)
            self.agent_send_btn.setText("发送")
        reply = data.get('reply') if isinstance(data, dict) else ''
        self._stop_agent_thinking()
        used_thinking_label = hasattr(self, 'agent_thinking_label') and self.agent_thinking_label is not None
        if used_thinking_label:
            self.agent_thinking_label.setText(reply or "Agent 暂无回复")
            self.agent_thinking_label = None
        else:
            self._add_agent_chat_item("Agent", reply or "Agent 暂无回复", "#18181b", data.get('attachments') if isinstance(data, dict) else [])
        if used_thinking_label and isinstance(data, dict) and data.get('attachments'):
            # When the reply replaced a thinking label, add visual attachments as a compact follow-up.
            self._add_agent_chat_item("Agent", "附件", "#71717a", data.get('attachments') or [])
        if isinstance(data, dict):
            self._apply_agent_status(data)

    def _add_agent_chat_item(self, sender, content, color, attachments=None):
        if hasattr(self, 'agent_chat_layout'):
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(10)
            is_user = sender == "我"
            if is_user:
                row_layout.addStretch(1)

            avatar = QLabel("你" if sender == "我" else ("系" if sender == "系统" else "A"))
            avatar.setFixedSize(34, 34)
            avatar.setAlignment(Qt.AlignCenter)
            avatar.setStyleSheet("""
                QLabel {
                    background: #ffffff;
                    color: #18181b;
                    border: 1px solid #deded9;
                    border-radius: 10px;
                    font-weight: 800;
                }
            """)

            body = QFrame()
            body.setStyleSheet("QFrame { background: transparent; border: none; }")
            body.setMaximumWidth(620 if is_user else 760)
            body_layout = QVBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 0, 0)
            body_layout.setSpacing(4)

            role = QLabel(f"{sender} · {timestamp}")
            role.setStyleSheet("color: #52525b; font-weight: 800;")
            body_layout.addWidget(role)

            text = QLabel(str(content or ""))
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if is_user:
                text.setStyleSheet(f"""
                    QLabel {{
                        color: {color or '#18181b'};
                        background: #f4f4f2;
                        border: 1px solid #deded9;
                        border-radius: 8px;
                        padding: 8px 10px;
                        line-height: 1.45;
                    }}
                """)
            else:
                text.setStyleSheet(f"color: {color or '#18181b'}; line-height: 1.45;")
            body_layout.addWidget(text)
            self._add_agent_attachment_widgets(body_layout, attachments or [])
            if is_user:
                row_layout.addWidget(body, 0, Qt.AlignTop)
                row_layout.addWidget(avatar, 0, Qt.AlignTop)
            else:
                row_layout.addWidget(avatar, 0, Qt.AlignTop)
                row_layout.addWidget(body, 1)

            insert_at = max(0, self.agent_chat_layout.count() - 1)
            self.agent_chat_layout.insertWidget(insert_at, row)
            if hasattr(self, 'agent_thread_scroll'):
                QTimer.singleShot(0, lambda: self.agent_thread_scroll.verticalScrollBar().setValue(self.agent_thread_scroll.verticalScrollBar().maximum()))
            return text

        if not hasattr(self, 'agent_chat_list'):
            return
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{timestamp}] {sender}: {content}")
        item.setForeground(QColor(color))
        self.agent_chat_list.addItem(item)
        self.agent_chat_list.scrollToBottom()
        return None

    def _add_agent_attachment_widgets(self, layout, attachments):
        for attachment in attachments or []:
            att = self._normalize_agent_attachment(attachment)
            if not att.get('url'):
                continue
            title = att.get('title') or att.get('name') or '附件'
            full_url = att['url'] if att['url'].startswith('http') else f"http://127.0.0.1:5000{att['url']}"
            if att.get('type') == 'image':
                try:
                    resp = requests.get(full_url, timeout=2)
                    pix = QPixmap()
                    if resp.ok and pix.loadFromData(resp.content):
                        label = QLabel()
                        label.setPixmap(pix.scaledToWidth(360, Qt.SmoothTransformation))
                        label.setStyleSheet("border: 1px solid #deded9; border-radius: 8px; padding: 4px; background: #ffffff;")
                        layout.addWidget(label)
                except Exception:
                    pass
            link = QLabel(f'<a href="{full_url}">{title}</a>')
            link.setOpenExternalLinks(True)
            link.setStyleSheet("color: #0369a1; font-size: 12px;")
            layout.addWidget(link)

    @staticmethod
    def _normalize_agent_attachment(attachment):
        if not isinstance(attachment, dict):
            return {}
        if attachment.get('url'):
            return attachment
        url = attachment.get('preview_url')
        if not url:
            return attachment
        return {
            'type': 'image' if attachment.get('type') == 'image' else 'file',
            'title': attachment.get('name') or attachment.get('title') or '附件',
            'url': url,
            'thumbnail_url': attachment.get('thumbnail_url'),
            'mime': attachment.get('mime'),
        }

    def _start_agent_thinking(self, text):
        self.agent_thinking_base = text
        self.agent_thinking_step = 0
        self.agent_thinking_label = self._add_agent_chat_item("Agent", text + "...", "#52525b")
        if hasattr(self, 'agent_thinking_timer'):
            self.agent_thinking_timer.start(360)

    def _update_agent_thinking(self):
        if not getattr(self, 'agent_thinking_label', None):
            return
        self.agent_thinking_step = (self.agent_thinking_step + 1) % 4
        dots = "." * (self.agent_thinking_step or 3)
        self.agent_thinking_label.setText(f"{self.agent_thinking_base}{dots}")

    def _stop_agent_thinking(self):
        if hasattr(self, 'agent_thinking_timer') and self.agent_thinking_timer.isActive():
            self.agent_thinking_timer.stop()

    def select_agent_model(self):
        if self._agent_model_busy:
            return
        provider = self.agent_provider_combo.currentData()
        model = self.agent_model_input.text().strip()
        self._agent_model_busy = True
        threading.Thread(target=self._post_agent_model_select, args=(provider, model), daemon=True).start()

    def _post_agent_model_select(self, provider, model):
        try:
            resp = requests.post(
                'http://127.0.0.1:5000/api/agent/models/select',
                json={'provider': provider, 'model': model},
                timeout=5
            )
            data = resp.json()
            data['_message'] = '模型选择已保存，下一次对话生效' if data.get('success', True) else data.get('message', '切换失败')
        except Exception as e:
            data = {'success': False, 'error': str(e), '_message': f'切换失败: {e}'}
        finally:
            self._agent_model_busy = False
        self.agent_models_changed.emit(data)

    def test_agent_model(self):
        if self._agent_model_busy:
            return
        provider = self.agent_provider_combo.currentData()
        model = self.agent_model_input.text().strip()
        self._agent_model_busy = True
        if hasattr(self, 'agent_test_model_btn'):
            self.agent_test_model_btn.setEnabled(False)
            self.agent_test_model_btn.setText("测试中")
        threading.Thread(target=self._post_agent_model_test, args=(provider, model), daemon=True).start()

    def _post_agent_model_test(self, provider, model):
        try:
            resp = requests.post(
                'http://127.0.0.1:5000/api/agent/models/test',
                json={'provider': provider, 'model': model},
                timeout=20
            )
            data = resp.json()
            data['_message'] = data.get('message') or ('模型连接正常' if data.get('success') else '模型连接失败')
        except Exception as e:
            data = {'success': False, 'error': str(e), '_message': f'测试失败: {e}'}
        finally:
            self._agent_model_busy = False
        self.agent_models_changed.emit(data)

    def _apply_agent_models_result(self, data):
        self._apply_agent_models(data)

    def _on_agent_provider_changed(self):
        if not hasattr(self, 'agent_provider_combo') or not hasattr(self, 'agent_model_input'):
            return
        model = self.agent_provider_combo.currentData(Qt.UserRole + 1) or ''
        provider = self.agent_provider_combo.currentData()
        self.agent_model_input.setText(model)
        self.agent_model_input.setEnabled(provider != 'rules_only')

    def _apply_agent_models(self, data):
        if not isinstance(data, dict):
            return
        if hasattr(self, 'agent_test_model_btn'):
            self.agent_test_model_btn.setEnabled(True)
            self.agent_test_model_btn.setText("测试")
        message = data.get('_message')
        if message and hasattr(self, 'agent_model_hint'):
            self.agent_model_hint.setText(message)
            self.show_toast(message)
        providers = data.get('providers') or []
        if not providers:
            if data.get('error') and hasattr(self, 'agent_model_hint'):
                self.agent_model_hint.setText(data.get('error'))
            return
        current = data.get('active_provider') or self.agent_provider_combo.currentData()
        self.agent_provider_combo.blockSignals(True)
        self.agent_provider_combo.clear()
        for provider in providers:
            self.agent_provider_combo.addItem(provider.get('label', provider.get('id', '')), provider.get('id', ''))
            idx = self.agent_provider_combo.count() - 1
            self.agent_provider_combo.setItemData(idx, provider.get('model') or provider.get('default_model') or '', Qt.UserRole + 1)
        index = self.agent_provider_combo.findData(current)
        if index >= 0:
            self.agent_provider_combo.setCurrentIndex(index)
        selected_model = self.agent_provider_combo.currentData(Qt.UserRole + 1) or data.get('active_model') or ''
        self.agent_model_input.setText(selected_model)
        self.agent_model_input.setEnabled(self.agent_provider_combo.currentData() != 'rules_only')
        self.agent_provider_combo.blockSignals(False)

    def confirm_agent_action(self, token):
        if self._agent_confirm_busy or not token:
            return
        reply = QMessageBox.question(
            self,
            "二次确认高风险动作",
            "该动作由 Agent 提出，确认后将请求后端执行。是否继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            self.show_toast("已取消高风险动作")
            return
        self._agent_confirm_busy = True
        threading.Thread(target=self._post_agent_confirm, args=(token,), daemon=True).start()

    def _post_agent_confirm(self, token):
        try:
            resp = requests.post(
                'http://127.0.0.1:5000/api/agent/action/confirm',
                json={'token': token},
                timeout=10
            )
            data = resp.json()
        except Exception as e:
            data = {'success': False, 'message': f'确认失败: {e}'}
        finally:
            self._agent_confirm_busy = False
        self.agent_action_result_received.emit(data)

    def _apply_agent_action_result(self, data):
        message = data.get('message') if isinstance(data, dict) else ''
        if not message:
            message = '动作已执行' if data.get('success') else '动作执行失败'
        self._add_agent_chat_item("系统", message, "#b45309")
        self.show_toast(message)
        self.refresh_agent_status()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.title_bar.max_btn.click()
        super().keyPressEvent(event)
    
    def connect_server(self):
        self.ws_thread = WebSocketThread()
        self.ws_thread.message_received.connect(self.on_message)
        self.ws_thread.connected.connect(self.on_connected)
        self.ws_thread.disconnected.connect(self.on_disconnected)
        self.ws_thread.start()
    
    def on_connected(self):
        self.status_label.setText("● 已连接")
        self.status_label.setStyleSheet("color: #15803d; font-size: 12px; padding: 12px 15px; border-top: 1px solid #deded9;")
        self.add_message("系统", "已连接到服务器", "system")
    
    def on_disconnected(self):
        self.status_label.setText("● 未连接")
        self.status_label.setStyleSheet("color: #dc2626; font-size: 12px; padding: 12px 15px; border-top: 1px solid #deded9;")
        self.add_message("系统", "连接断开", "warning")
    
    def on_message(self, data: dict):
        msg_type = data.get("type", "normal")
        content = data.get("content", "")
        title = data.get("title", "")
        sender = data.get("sender", "未知")
        level = data.get("level", "normal")
        
        display_text = f"{sender}: {title} {content}" if title else f"{sender}: {content}"
        self.add_message(sender, display_text, level)
        
        if msg_type == "emergency" or msg_type == "alert":
            self.flash_window()
    
    def add_message(self, sender: str, content: str, level: str = "normal"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        item_text = f"[{timestamp}] {sender}: {content}"
        
        item = QListWidgetItem(item_text)
        
        if level == "danger" or level == "emergency":
            item.setForeground(QColor("#dc2626"))
        elif level == "warning":
            item.setForeground(QColor("#b45309"))
        elif level == "system":
            item.setForeground(QColor("#0369a1"))
        else:
            item.setForeground(QColor("#18181b"))
        
        self.msg_list.addItem(item)
        self.msg_list.scrollToBottom()
        
        self.all_msg_list.addItem(item_text)
        self.all_msg_list.scrollToBottom()
        
        while self.msg_list.count() > 100:
            self.msg_list.takeItem(0)
        while self.all_msg_list.count() > 200:
            self.all_msg_list.takeItem(0)
    
    def send_normal_message(self):
        content = self.msg_input.text().strip()
        if not content:
            return
        
        msg = {
            "type": "normal",
            "level": "normal",
            "title": "",
            "content": content,
            "sender": "lab-device-1"
        }
        
        self.ws_thread.send_message(msg)
        self.add_message("我", content, "normal")
        self.msg_input.clear()
    
    def send_msg(self, input_widget):
        content = input_widget.text().strip()
        if not content:
            return
        
        msg = {
            "type": "normal",
            "level": "normal",
            "title": "",
            "content": content,
            "sender": "lab-device-1"
        }
        
        self.ws_thread.send_message(msg)
        self.add_message("我", content, "normal")
        input_widget.clear()
    
    def send_emergency(self):
        reply = QMessageBox.question(
            self,
            "确认紧急求助",
            "将发送紧急求助消息并通知后端。请确认现场确实需要协助。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        msg = {
            "type": "emergency",
            "level": "danger",
            "title": "紧急求助",
            "content": "需要紧急援助！",
            "sender": "lab-device-1"
        }
        
        self.ws_thread.send_message(msg)
        self.add_message("我", "🆘 紧急求助已发送", "danger")
        
        # 后台发送通知
        threading.Thread(target=self.send_notification, args=({
            'lab_name': '实验室1',
            'type': 'SOS紧急求助',
            'message': '需要紧急援助！'
        },), daemon=True).start()
        
        # 显示提示标签
        self.show_toast("🆘 紧急求助已发送")
    
    def send_fire_alert(self):
        reply = QMessageBox.question(
            self,
            "确认应急警报",
            "将发送应急警报消息。请确认现场确实需要立即处置。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        msg = {
            "type": "alert",
            "level": "danger",
            "title": "火灾报警",
            "content": "检测到火灾！请立即撤离！",
            "sender": "lab-device-1"
        }
        
        self.ws_thread.send_message(msg)
        self.add_message("我", "应急警报已发送", "danger")
        
        # 后台发送通知
        threading.Thread(target=self.send_notification, args=({
            'lab_name': '实验室1',
            'type': '火灾报警',
            'message': '检测到火灾！请立即撤离！'
        },), daemon=True).start()
        
        # 显示提示标签
        self.show_toast("应急警报已发送")

    def refresh_emergency_call_status(self):
        """后台刷新 4G 应急电话状态，避免阻塞 Qt 主线程。"""
        if self._call_status_busy:
            return
        self._call_status_busy = True
        threading.Thread(target=self._fetch_emergency_call_status, daemon=True).start()

    def _fetch_emergency_call_status(self):
        try:
            resp = requests.get('http://localhost:5000/api/emergency-call/status', timeout=3)
            data = resp.json()
        except Exception as e:
            data = {
                'success': False,
                'state': 'error',
                'message': f'无法读取通话状态: {e}',
                'ready': False
            }
        finally:
            self._call_status_busy = False
        self.emergency_call_status_changed.emit(data)

    def toggle_emergency_call(self):
        """根据当前状态拨打管理员或挂断。"""
        if self._call_action_busy:
            return
        self._call_action_busy = True
        action = 'hangup' if self._emergency_call_active else 'start'
        if action == 'start':
            reply = QMessageBox.question(
                self,
                "确认拨打管理员",
                "将调用后端应急电话接口，号码由后端配置决定，前端不会传入电话号码。是否继续？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                self._call_action_busy = False
                return
        if hasattr(self, 'call_btn'):
            self.call_btn.setEnabled(False)
            self.call_btn.setText("处理中...")
        threading.Thread(target=self._post_emergency_call_action, args=(action,), daemon=True).start()

    def _post_emergency_call_action(self, action):
        endpoint = 'hangup' if action == 'hangup' else 'start'
        payload = {'reason': 'Qt界面一键应急拨号'} if endpoint == 'start' else {}
        try:
            resp = requests.post(
                f'http://localhost:5000/api/emergency-call/{endpoint}',
                json=payload,
                timeout=8
            )
            result = resp.json()
            status = result.get('status') or result
            if isinstance(status, dict):
                status['_action_success'] = bool(result.get('success'))
                status['_action_message'] = result.get('message', '')
            else:
                status = {
                    'success': False,
                    'state': 'error',
                    'message': result.get('message', '通话控制失败'),
                    '_action_success': False,
                    '_action_message': result.get('message', '通话控制失败')
                }
        except Exception as e:
            status = {
                'success': False,
                'state': 'error',
                'message': f'通话控制失败: {e}',
                '_action_success': False,
                '_action_message': f'通话控制失败: {e}'
            }
        finally:
            self._call_action_busy = False
        self.emergency_call_status_changed.emit(status)
        time.sleep(0.8)
        self.refresh_emergency_call_status()

    def _apply_emergency_call_status(self, data):
        """在主线程更新应急通话 UI。"""
        if not isinstance(data, dict):
            return
        state = data.get('state', 'unknown')
        message = data.get('message') or ''
        state_text = {
            'disabled': '已禁用',
            'port_missing': '未就绪',
            'sim_missing': 'SIM缺失',
            'sim_pin': 'SIM需PIN',
            'sim_puk': 'SIM需PUK',
            'idle': '待机',
            'dialing': '拨号中',
            'ringing': '来电中',
            'in_call': '通话中',
            'error': '失败',
            'unknown': '未知'
        }.get(state, state)
        signal_info = data.get('signal') or {}
        dbm = signal_info.get('dbm')
        signal_text = f" / {dbm} dBm" if dbm is not None else ""
        display_text = state_text if not message or message == state_text else f"{state_text} - {message}"
        if signal_text and state in ('idle', 'dialing', 'ringing', 'in_call'):
            display_text += signal_text

        call_active = bool(data.get('call_active') or state in ('dialing', 'ringing', 'in_call'))
        self._emergency_call_active = call_active
        self._emergency_call_state = state

        if hasattr(self, 'call_status_label'):
            color = '#15803d' if state == 'idle' else '#dc2626' if state in ('in_call', 'dialing', 'ringing') else '#b45309'
            if state in ('sim_missing', 'error', 'port_missing', 'sim_pin', 'sim_puk'):
                color = '#b45309'
            self.call_status_label.setText(display_text)
            self.call_status_label.setStyleSheet(f"font-size: 15px; color: {color}; font-weight: 800;")

        if hasattr(self, 'call_btn'):
            enabled = bool(data.get('enabled', True)) and not self._call_action_busy
            self.call_btn.setEnabled(enabled)
            if call_active:
                self.call_btn.setText("挂断")
                self.call_btn.setStyleSheet("""
                    QPushButton {
                        background: #fef2f2;
                        color: #dc2626;
                        border: 1px solid #fecaca;
                        font-size: 15px;
                        font-weight: bold;
                    }
                    QPushButton:hover { background: #fee2e2; }
                    QPushButton:disabled { background: #f4f4f2; color: #a1a1aa; border-color: #deded9; }
                """)
            else:
                self.call_btn.setText("拨打管理员")
                self.call_btn.setStyleSheet("""
                    QPushButton {
                        background: #18181b;
                        color: #ffffff;
                        border: 1px solid #18181b;
                        font-size: 15px;
                        font-weight: bold;
                    }
                    QPushButton:hover { background: #27272a; }
                    QPushButton:disabled { background: #f4f4f2; color: #a1a1aa; border-color: #deded9; }
                """)

        action_message = data.get('_action_message')
        if action_message:
            prefix = "✅" if data.get('_action_success') else "⚠️"
            self.show_toast(f"{prefix} {action_message}")
    
    def update_camera(self):
        """启动低延迟摄像头管线：采集/显示优先，网络推送后台异步执行。"""
        self._mjpeg_running = False
        if self._camera_thread and self._camera_thread.is_alive():
            return

        self._camera_stop.clear()
        self._camera_thread = threading.Thread(target=self._camera_capture_loop, name='CameraCapture', daemon=True)
        self._camera_thread.start()
        if CAPTURE_BACKEND not in ('v4l2ctl', 'gstreamer', 'gstlaunch'):
            self._push_thread = threading.Thread(target=self._camera_push_loop, name='CameraPush', daemon=True)
            self._push_thread.start()
        if self._detection_thread is None or not self._detection_thread.is_alive():
            self._detection_thread = threading.Thread(
                target=self._detection_fetch_loop,
                name='DetectionJsonFetch',
                daemon=True
            )
            self._detection_thread.start()

    def _open_usb_camera(self):
        """按现有设备顺序打开 USB 摄像头。"""
        if CAPTURE_BACKEND == 'gstreamer':
            for dev in ('/dev/video21', '/dev/video22'):
                pipelines = [
                    (
                        f"v4l2src device={dev} io-mode=2 ! "
                        f"image/jpeg,width={PUSH_WIDTH},height={PUSH_HEIGHT},framerate={CAMERA_FPS}/1 ! "
                        "jpegparse ! jpegdec ! videoconvert ! "
                        "video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false"
                    ),
                    (
                        f"v4l2src device={dev} ! "
                        f"image/jpeg,width={PUSH_WIDTH},height={PUSH_HEIGHT},framerate={CAMERA_FPS}/1 ! "
                        "jpegparse ! jpegdec ! videoconvert ! "
                        "video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false"
                    ),
                ]
                for pipeline in pipelines:
                    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                    if not cap.isOpened():
                        cap.release()
                        continue
                    ok, frame = cap.read()
                    if ok and frame is not None:
                        print(f"Camera opened: {dev} via gstreamer, frame={frame.shape}", flush=True)
                        return cap
                    cap.release()

        for dev in ('/dev/video21', '/dev/video22'):
            for backend_name, backend in (('v4l2', cv2.CAP_V4L2), ('auto', cv2.CAP_ANY)):
                cap = cv2.VideoCapture(dev, backend)
                if not cap.isOpened():
                    cap.release()
                    continue
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, PUSH_WIDTH)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, PUSH_HEIGHT)
                cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                ok, frame = cap.read()
                if ok and frame is not None:
                    print(f"Camera opened: {dev} via {backend_name}, frame={frame.shape}")
                    return cap
                cap.release()
        return None

    def _camera_capture_loop(self):
        if CAPTURE_BACKEND == 'gstlaunch':
            self._gst_launch_mjpeg_capture_loop()
            return
        if CAPTURE_BACKEND == 'gstreamer':
            self._gst_mjpeg_capture_loop()
            return
        if CAPTURE_BACKEND == 'v4l2ctl':
            self._v4l2_mjpeg_capture_loop()
            return

        cap = None
        failures = 0

        while not self._camera_stop.is_set():
            if cap is None or not cap.isOpened():
                cap = self._open_usb_camera()
                if cap is None:
                    time.sleep(1)
                    continue
                failures = 0

            try:
                ret, frame = cap.read()
                if not ret or frame is None:
                    failures += 1
                    if failures >= 3:
                        cap.release()
                        cap = None
                    continue

                failures = 0
                now = time.time()

                # 始终显示本地摄像头帧，检测框用后端 JSON 坐标本地叠加，避免拉取画框 JPEG 造成延迟。
                if now - self._last_display_ts >= self._display_interval:
                    display_frame = frame
                    if getattr(self, 'detection_enabled', True):
                        display_frame = self._draw_detections_on_frame(frame)
                    rgb = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
                    h, w = rgb.shape[:2]
                    image = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()
                    self.camera_ready.emit(image)
                    self._last_display_ts = now

                with self._frame_lock:
                    self._latest_frame = frame.copy()
                    self._latest_push_seq += 1
            except Exception as e:
                print(f"Camera error: {e}")
                if cap is not None:
                    cap.release()
                    cap = None
                time.sleep(0.2)

        if cap is not None:
            cap.release()

    def _gst_launch_mjpeg_capture_loop(self):
        """Capture continuous native MJPEG through gst-launch stdout."""
        for dev in ('/dev/video21', '/dev/video22'):
            if self._camera_stop.is_set():
                return

            while not self._camera_stop.is_set():
                cmd = [
                    'gst-launch-1.0', '-q',
                    'v4l2src', f'device={dev}',
                    '!', f'image/jpeg,width={PUSH_WIDTH},height={PUSH_HEIGHT},framerate={CAMERA_FPS}/1',
                    '!', 'queue', 'leaky=downstream', 'max-size-buffers=1',
                    '!', 'jpegparse',
                    '!', 'filesink', 'location=/dev/stdout',
                ]
                proc = None
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                    self._camera_proc = proc
                    now = time.time()
                    if now - self._last_camera_log_ts >= 10.0:
                        print(f"Camera opened: {dev} via gst-launch MJPG stream {PUSH_WIDTH}x{PUSH_HEIGHT}", flush=True)
                        self._last_camera_log_ts = now
                    self._read_mjpeg_pipe(proc)
                except Exception as e:
                    print(f"gst-launch camera error on {dev}: {e}", flush=True)
                finally:
                    if proc is not None and proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=1.0)
                    self._camera_proc = None

                if not self._camera_stop.is_set():
                    time.sleep(0.2)

    def _read_mjpeg_pipe(self, proc):
        buffer = bytearray()
        last_push_ts = 0.0
        last_data_ts = time.time()
        frame_count = 0
        if proc.stdout is None:
            return 0

        fd = proc.stdout.fileno()
        while not self._camera_stop.is_set() and proc.poll() is None:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                if time.time() - last_data_ts > 4.0:
                    print("gst-launch produced no MJPEG data for 4s, restarting pipeline", flush=True)
                    break
                continue
            chunk = os.read(fd, 32768)
            if not chunk:
                break
            last_data_ts = time.time()
            buffer.extend(chunk)
            if len(buffer) > 8 * 1024 * 1024:
                del buffer[:len(buffer) - 2 * 1024 * 1024]

            while True:
                start = buffer.find(b'\xff\xd8')
                if start < 0:
                    if len(buffer) > 1024 * 1024:
                        del buffer[:-2]
                    break
                end = buffer.find(b'\xff\xd9', start + 2)
                if end < 0:
                    if start > 0:
                        del buffer[:start]
                    break

                jpeg = bytes(buffer[start:end + 2])
                del buffer[:end + 2]
                frame_count += 1
                last_push_ts = self._handle_mjpeg_frame(jpeg, last_push_ts)
        return frame_count

    def _gst_mjpeg_capture_loop(self):
        """Capture native MJPEG frames through GStreamer appsink without restarting v4l2-ctl."""
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except Exception as e:
            print(f"GStreamer Python bindings unavailable, fallback to v4l2-ctl: {e}", flush=True)
            self._v4l2_mjpeg_capture_loop()
            return

        Gst.init(None)
        for dev in ('/dev/video21', '/dev/video22'):
            if self._camera_stop.is_set():
                return

            pipeline_desc = (
                f"v4l2src device={dev} ! "
                f"image/jpeg,width={PUSH_WIDTH},height={PUSH_HEIGHT},framerate={CAMERA_FPS}/1 ! "
                "queue leaky=downstream max-size-buffers=1 ! jpegparse ! "
                "appsink name=sink emit-signals=false sync=false async=false max-buffers=1 drop=true"
            )

            while not self._camera_stop.is_set():
                pipeline = None
                try:
                    pipeline = Gst.parse_launch(pipeline_desc)
                    sink = pipeline.get_by_name("sink")
                    if sink is None:
                        raise RuntimeError("appsink not found")

                    pipeline.set_state(Gst.State.PLAYING)
                    state_ret, state, _ = pipeline.get_state(5 * Gst.SECOND)
                    if state_ret == Gst.StateChangeReturn.FAILURE or state != Gst.State.PLAYING:
                        raise RuntimeError(f"pipeline did not enter PLAYING: {state_ret.value_nick}/{state.value_nick}")

                    now = time.time()
                    if now - self._last_camera_log_ts >= 10.0:
                        print(f"Camera opened: {dev} via gstreamer MJPG appsink {PUSH_WIDTH}x{PUSH_HEIGHT}", flush=True)
                        self._last_camera_log_ts = now

                    bus = pipeline.get_bus()
                    last_push_ts = 0.0
                    while not self._camera_stop.is_set():
                        msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                        if msg is not None:
                            if msg.type == Gst.MessageType.ERROR:
                                err, debug = msg.parse_error()
                                print(f"GStreamer camera error on {dev}: {err}; {debug}", flush=True)
                            break

                        sample = sink.emit("try-pull-sample", Gst.SECOND // 2)
                        if sample is None:
                            continue
                        buf = sample.get_buffer()
                        ok, info = buf.map(Gst.MapFlags.READ)
                        if not ok:
                            continue
                        try:
                            raw = bytes(info.data)
                        finally:
                            buf.unmap(info)
                        start = raw.find(b'\xff\xd8')
                        end = raw.rfind(b'\xff\xd9')
                        if start >= 0 and end > start:
                            jpeg = raw[start:end + 2]
                            last_push_ts = self._handle_mjpeg_frame(jpeg, last_push_ts)
                except Exception as e:
                    print(f"GStreamer camera error on {dev}: {e}", flush=True)
                finally:
                    if pipeline is not None:
                        pipeline.set_state(Gst.State.NULL)

                if not self._camera_stop.is_set():
                    time.sleep(0.2)

    def _v4l2_mjpeg_capture_loop(self):
        """Capture native MJPEG frames through v4l2-ctl and avoid OpenCV read stalls."""
        for dev in ('/dev/video21', '/dev/video22'):
            if self._camera_stop.is_set():
                return
            while not self._camera_stop.is_set():
                stream_path = f"/dev/shm/labsafe_camera_{os.getpid()}_{int(time.time() * 1000)}.mjpg"
                cmd = [
                    'v4l2-ctl',
                    '-d', dev,
                    f'--set-fmt-video=width={PUSH_WIDTH},height={PUSH_HEIGHT},pixelformat=MJPG',
                    f'--set-parm={CAMERA_FPS}',
                    '--stream-mmap',
                    f'--stream-count={max(V4L2_BATCH_FRAMES, 1)}',
                    f'--stream-to={stream_path}',
                ]
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)
                    self._camera_proc = proc
                    now = time.time()
                    if now - self._last_camera_log_ts >= 10.0:
                        print(f"Camera opened: {dev} via v4l2-ctl MJPG batch {PUSH_WIDTH}x{PUSH_HEIGHT}", flush=True)
                        self._last_camera_log_ts = now
                    self._read_mjpeg_file(proc, stream_path)
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=1.0)
                    self._camera_proc = None
                except Exception as e:
                    print(f"v4l2-ctl camera error on {dev}: {e}", flush=True)
                finally:
                    self._camera_proc = None
                    try:
                        os.remove(stream_path)
                    except Exception:
                        pass
                time.sleep(0.01)

    def _read_mjpeg_file(self, proc, stream_path):
        buffer = bytearray()
        last_push_ts = 0.0
        try:
            proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
            return

        if self._camera_stop.is_set() or not os.path.exists(stream_path):
            return
        with open(stream_path, 'rb') as stream:
            buffer.extend(stream.read())

        while True:
            start = buffer.find(b'\xff\xd8')
            if start < 0:
                break
            end = buffer.find(b'\xff\xd9', start + 2)
            if end < 0:
                break

            jpeg = bytes(buffer[start:end + 2])
            del buffer[:end + 2]
            last_push_ts = self._handle_mjpeg_frame(jpeg, last_push_ts)
            if not self._camera_stop.is_set():
                time.sleep(1.0 / max(CAMERA_FPS, 1))

    def _handle_mjpeg_frame(self, jpeg, last_push_ts):
        now = time.time()
        self.frame_shared.emit(jpeg)
        if now - self._last_display_ts >= self._display_interval:
            image = QImage()
            if image.loadFromData(jpeg):
                if getattr(self, 'detection_enabled', True):
                    image = self._draw_detections_on_qimage(image)
                self.camera_ready.emit(image)
                self._last_display_ts = now

        if now - last_push_ts >= self._push_interval:
            self._post_jpeg_frame(jpeg)
            last_push_ts = now
        return last_push_ts

    def _post_jpeg_frame(self, jpeg):
        try:
            if self._push_session is None:
                self._push_session = requests.Session()
            self._push_session.post(
                'http://127.0.0.1:5000/api/camera/push_frame',
                files={'frame': jpeg},
                timeout=0.2
            )
        except Exception:
            pass

    def _camera_push_loop(self):
        while not self._camera_stop.is_set():
            with self._frame_lock:
                seq = self._latest_push_seq
                frame = self._latest_frame.copy() if self._latest_frame is not None else None

            if frame is None or seq == self._pushed_seq:
                time.sleep(0.005)
                continue

            now = time.time()
            if now - self._last_push_ts < self._push_interval:
                time.sleep(0.005)
                continue

            try:
                push_frame = frame
                if PUSH_WIDTH > 0 and PUSH_HEIGHT > 0:
                    h, w = frame.shape[:2]
                    if w != PUSH_WIDTH or h != PUSH_HEIGHT:
                        push_frame = cv2.resize(frame, (PUSH_WIDTH, PUSH_HEIGHT), interpolation=cv2.INTER_AREA)
                ok, jpg = cv2.imencode('.jpg', push_frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if not ok:
                    time.sleep(0.005)
                    continue
                jpeg = jpg.tobytes()
                self.frame_shared.emit(jpeg)
                if self._push_session is None:
                    self._push_session = requests.Session()
                self._push_session.post(
                    'http://127.0.0.1:5000/api/camera/push_frame',
                    files={'frame': jpeg},
                    timeout=0.2
                )
                self._pushed_seq = seq
                self._last_push_ts = now
            except Exception:
                # 后端不可用时不阻塞摄像头采集和 Qt 本地显示。
                time.sleep(0.02)

    def _set_fire_alarm_active(self, active, reason=''):
        reason = (reason or '视觉检测到火灾/烟雾') if active else ''
        self.fire_alarm_changed.emit(bool(active), reason)

    def _apply_fire_alarm_ui(self, active, reason=''):
        active = bool(active)
        reason = (reason or '视觉检测到火灾/烟雾') if active else ''
        changed = active != self._fire_alarm_active or reason != self._fire_alarm_reason
        self._fire_alarm_active = active
        self._fire_alarm_reason = reason if active else ''

        if active:
            now = time.time()
            if now - self._last_alarm_beep_ts >= 0.85:
                try:
                    subprocess.Popen(
                        ['paplay', '--volume=65536', '/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                except Exception:
                    QApplication.beep()
                self._last_alarm_beep_ts = now
            if changed or now - self._last_alarm_message_ts >= 10.0:
                self.add_message("LabSafe", f"火灾/烟雾视觉告警: {reason}", "danger")
                self._last_alarm_message_ts = now

        if not changed:
            return

        if hasattr(self, 'camera_label'):
            self.camera_label.setStyleSheet(
                self._camera_label_alarm_style if active else self._camera_label_normal_style
            )
        if hasattr(self, 'fire_label'):
            if active:
                self.fire_label.setText("危险")
                self.fire_label.setStyleSheet("font-size: 16px; color: #dc2626; font-weight: 800;")
            else:
                self.fire_label.setText("正常")
                self.fire_label.setStyleSheet("font-size: 16px; color: #15803d; font-weight: 800;")
        if hasattr(self, 'sys_label'):
            if active:
                self.sys_label.setText("火灾告警")
                self.sys_label.setStyleSheet("font-size: 16px; color: #dc2626; font-weight: 800;")
            else:
                self.sys_label.setText("运行中")
                self.sys_label.setStyleSheet("font-size: 16px; color: #15803d; font-weight: 800;")

    def _apply_environment_status(self, data):
        fire_state = data.get('fire_state') or {}
        temp = fire_state.get('temperature')
        humidity = fire_state.get('humidity')
        sensor_status = fire_state.get('sensor_status') or ''

        if hasattr(self, 'temp_label') and temp is not None:
            self.temp_label.setText(f"{float(temp):.1f}°C")
        if hasattr(self, 'humidity_label'):
            if humidity is None:
                self.humidity_label.setText("--%")
                self.humidity_label.setStyleSheet("font-size: 16px; color: #b45309; font-weight: 800;")
            else:
                self.humidity_label.setText(f"{float(humidity):.1f}%")
                color = "#0369a1" if sensor_status == "ok" else "#b45309"
                self.humidity_label.setStyleSheet(f"font-size: 16px; color: {color}; font-weight: 800;")

    def _detection_fetch_loop(self):
        """从 Flask 获取最新 RKNN 检测 JSON，Qt 本地叠加画框。"""
        url = 'http://127.0.0.1:5000/api/camera/usb-camera/detections'
        status_url = 'http://127.0.0.1:5000/api/status'
        while not self._camera_stop.is_set():
            if not getattr(self, 'detection_enabled', True):
                time.sleep(0.1)
                continue

            try:
                if self._detection_session is None:
                    self._detection_session = requests.Session()
                resp = self._detection_session.get(url, timeout=0.3)
                if resp.status_code == 200:
                    data = resp.json()
                    detections = data.get('detections') or []
                    image_w = data.get('image_width') or CAMERA_WIDTH
                    image_h = data.get('image_height') or CAMERA_HEIGHT
                    with self._detections_lock:
                        self._latest_detections = detections
                        self._detection_source_size = (float(image_w), float(image_h))
                    fire_alarm = bool(data.get('fire_alarm'))
                    self._set_fire_alarm_active(fire_alarm, data.get('alarm_reason') or '')
                now = time.time()
                if now - self._last_status_fetch_ts >= 1.0:
                    self._last_status_fetch_ts = now
                    status_resp = self._detection_session.get(status_url, timeout=0.3)
                    if status_resp.status_code == 200:
                        self.environment_status_changed.emit(status_resp.json())
                time.sleep(0.08)
            except Exception:
                time.sleep(0.2)

    def _draw_detections_on_frame(self, frame):
        with self._detections_lock:
            detections = list(self._latest_detections)
            source_w, source_h = self._detection_source_size

        if not detections or source_w <= 0 or source_h <= 0:
            return frame

        out = frame.copy()
        h, w = out.shape[:2]
        sx = w / source_w
        sy = h / source_h
        colors = [
            (45, 170, 255),
            (68, 220, 68),
            (255, 190, 60),
            (190, 120, 255),
            (60, 220, 220),
            (40, 40, 240),
            (80, 80, 255),
        ]
        for det in detections:
            bbox = det.get('bbox') or []
            if len(bbox) != 4:
                continue
            class_id = int(det.get('class_id', 0))
            color = colors[class_id % len(colors)]
            x1 = int(max(0, min(w - 1, bbox[0] * sx)))
            y1 = int(max(0, min(h - 1, bbox[1] * sy)))
            x2 = int(max(0, min(w - 1, bbox[2] * sx)))
            y2 = int(max(0, min(h - 1, bbox[3] * sy)))
            if x2 <= x1 or y2 <= y1:
                continue
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
            label = f"{det.get('class_name', 'obj')} {float(det.get('score', 0.0)):.2f}"
            (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            y_text = max(0, y1 - th - baseline - 4)
            cv2.rectangle(out, (x1, y_text), (x1 + tw + 6, y_text + th + baseline + 4), color, -1)
            cv2.putText(out, label, (x1 + 3, y_text + th + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, cv2.LINE_AA)
        return out

    def _draw_detections_on_qimage(self, image):
        with self._detections_lock:
            detections = list(self._latest_detections)
            source_w, source_h = self._detection_source_size

        if not detections or source_w <= 0 or source_h <= 0:
            return image

        out = image.copy()
        w = out.width()
        h = out.height()
        sx = w / source_w
        sy = h / source_h
        colors = [
            QColor(255, 170, 45),
            QColor(68, 220, 68),
            QColor(60, 190, 255),
            QColor(255, 120, 190),
            QColor(220, 220, 60),
            QColor(240, 40, 40),
            QColor(255, 80, 80),
        ]
        painter = QPainter(out)
        font = QFont("Microsoft YaHei", 12)
        font.setBold(True)
        painter.setFont(font)
        for det in detections:
            bbox = det.get('bbox') or []
            if len(bbox) != 4:
                continue
            class_id = int(det.get('class_id', 0))
            color = colors[class_id % len(colors)]
            x1 = int(max(0, min(w - 1, bbox[0] * sx)))
            y1 = int(max(0, min(h - 1, bbox[1] * sy)))
            x2 = int(max(0, min(w - 1, bbox[2] * sx)))
            y2 = int(max(0, min(h - 1, bbox[3] * sy)))
            if x2 <= x1 or y2 <= y1:
                continue
            painter.setPen(QPen(color, 2))
            painter.drawRect(x1, y1, x2 - x1, y2 - y1)
            label = f"{det.get('class_name', 'obj')} {float(det.get('score', 0.0)):.2f}"
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(label) + 8
            th = metrics.height() + 4
            y_text = max(0, y1 - th)
            painter.fillRect(x1, y_text, tw, th, color)
            painter.setPen(QPen(QColor(0, 0, 0), 1))
            painter.drawText(x1 + 4, y_text + th - 5, label)
        painter.end()
        return out
    
    def on_camera_ready(self):
        """摄像头数据就绪"""
        try:
            if self.camera_reply.error() == QNetworkReply.NoError:
                data = self.camera_reply.readAll()
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull():
                    # 缩放以适应标签大小
                    scaled_pixmap = pixmap.scaled(
                        self.camera_label.width(),
                        self.camera_label.height(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    self.camera_label.setPixmap(scaled_pixmap)
        except:
            pass
    
    def show_toast(self, message):
        """显示临时提示标签"""
        # 创建临时提示
        toast = QLabel(message, self)
        toast.setStyleSheet("""
            QLabel {
                background: #18181b;
                color: #ffffff;
                padding: 15px 30px;
                border-radius: 8px;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        toast.setAlignment(Qt.AlignCenter)
        
        # 居中显示
        toast.setFixedSize(300, 50)
        toast.move((self.width() - 300) // 2, (self.height() - 50) // 2)
        toast.show()
        
        # 2秒后自动消失
        QTimer.singleShot(2000, toast.hide)
    
    def send_notification(self, data):
        """后台发送通知"""
        import requests
        try:
            requests.post('http://localhost:5000/api/alert/emergency', json=data, timeout=5)
        except:
            pass
    
    def flash_window(self):
        """闪烁窗口提醒 - 暂时禁用"""
        pass
    
    def closeEvent(self, event):
        if hasattr(self, '_camera_stop'):
            self._camera_stop.set()
        if getattr(self, '_camera_proc', None):
            try:
                self._camera_proc.terminate()
            except:
                pass
        if getattr(self, '_push_session', None):
            try:
                self._push_session.close()
            except:
                pass
        if getattr(self, '_annotated_session', None):
            try:
                self._annotated_session.close()
            except:
                pass
        if getattr(self, '_detection_session', None):
            try:
                self._detection_session.close()
            except:
                pass
        if self.ws_thread:
            self.ws_thread.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    window = LabClient()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
