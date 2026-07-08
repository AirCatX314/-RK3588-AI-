#!/usr/bin/env python3
"""
实验室安全监控系统 - 管理员客户端
运行在电脑上，接收报警和消息
"""

import sys
import json
import asyncio
import datetime
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
import websockets
import threading

# 服务器配置
SERVER_IP = "127.0.0.1"  # Server IP. Set this to the board IP when running remotely.
SERVER_PORT = 8765

class WebSocketThread(QThread):
    """WebSocket通信线程"""
    message_received = pyqtSignal(dict)
    connected = pyqtSignal()
    disconnected = pyqtSignal()
    
    def __init__(self, server_ip, server_port):
        super().__init__()
        self.server_ip = server_ip
        self.server_port = server_port
        self.running = True
        self.ws = None
        
    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.connect_server())
        
    async def connect_server(self):
        while self.running:
            try:
                async with websockets.connect(f"ws://{self.server_ip}:{self.server_port}") as ws:
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

class AdminClient(QMainWindow):
    """管理员客户端"""
    
    def __init__(self):
        super().__init__()
        self.ws_thread = None
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle("🔧 实验室安全监控 - 管理员端")
        self.setGeometry(100, 100, 800, 600)
        
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)
        central.setLayout(layout)
        
        # 标题
        title = QLabel("🔧 实验室安全监控 - 管理员端")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #fff; padding: 10px;")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # 连接状态
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("服务器:"))
        self.server_input = QLineEdit(SERVER_IP)
        self.server_input.setFixedWidth(150)
        status_layout.addWidget(self.server_input)
        status_layout.addWidget(QLabel("端口:"))
        self.port_input = QLineEdit(str(SERVER_PORT))
        self.port_input.setFixedWidth(80)
        status_layout.addWidget(self.port_input)
        
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self.toggle_connection)
        status_layout.addWidget(self.connect_btn)
        
        self.status_label = QLabel("● 未连接")
        self.status_label.setStyleSheet("color: #888;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        layout.addLayout(status_layout)
        
        # 消息记录
        msg_group = QGroupBox("📋 消息记录")
        msg_layout = QVBoxLayout()
        
        self.msg_list = QListWidget()
        self.msg_list.setStyleSheet("""
            QListWidget {
                background: rgba(0,0,0,0.3);
                color: #fff;
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px;
                padding: 5px;
                font-size: 14px;
            }
        """)
        msg_layout.addWidget(self.msg_list)
        msg_group.setLayout(msg_layout)
        layout.addWidget(msg_group, 1)
        
        # 发送区域
        send_group = QGroupBox("📤 发送通知")
        send_layout = QHBoxLayout()
        
        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("输入通知内容...")
        send_layout.addWidget(self.msg_input, 1)
        
        send_btn = QPushButton("发送通知")
        send_btn.clicked.connect(self.send_notification)
        send_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                background: #2196F3;
                color: white;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover { background: #1976D2; }
        """)
        send_layout.addWidget(send_btn)
        
        send_group.setLayout(send_layout)
        layout.addWidget(send_group)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        
        test_btn = QPushButton("🧪 测试消息")
        test_btn.clicked.connect(self.send_test)
        test_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                background: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
            }
        """)
        btn_layout.addWidget(test_btn)
        
        clear_btn = QPushButton("🗑️ 清空记录")
        clear_btn.clicked.connect(lambda: self.msg_list.clear())
        clear_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                background: #757575;
                color: white;
                border: none;
                border-radius: 5px;
            }
        """)
        btn_layout.addWidget(clear_btn)
        
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        
        # 样式
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a1a2e, stop:1 #16213e);
            }
            QGroupBox {
                color: #fff;
                font-size: 14px;
                border: 1px solid rgba(255,255,255,0.2);
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
            QLineEdit {
                padding: 8px;
                border-radius: 5px;
                border: 1px solid rgba(255,255,255,0.3);
                background: rgba(255,255,255,0.1);
                color: #fff;
            }
            QLabel { color: #fff; }
        """)
    
    def toggle_connection(self):
        if self.ws_thread and self.ws_thread.isRunning():
            self.ws_thread.stop()
        else:
            server = self.server_input.text()
            port = int(self.port_input.text())
            self.ws_thread = WebSocketThread(server, port)
            self.ws_thread.message_received.connect(self.on_message)
            self.ws_thread.connected.connect(self.on_connected)
            self.ws_thread.disconnected.connect(self.on_disconnected)
            self.ws_thread.start()
    
    def on_connected(self):
        self.status_label.setText("● 已连接")
        self.status_label.setStyleSheet("color: #4caf50;")
        self.connect_btn.setText("断开")
        self.add_message("系统", "已连接到服务器", "system")
    
    def on_disconnected(self):
        self.status_label.setText("● 未连接")
        self.status_label.setStyleSheet("color: #f44336;")
        self.connect_btn.setText("连接")
        self.add_message("系统", "连接断开", "warning")
    
    def on_message(self, data: dict):
        msg_type = data.get("type", "normal")
        content = data.get("content", "")
        title = data.get("title", "")
        sender = data.get("sender", "未知")
        level = data.get("level", "normal")
        
        display = f"{sender}: {title} {content}" if title else f"{sender}: {content}"
        self.add_message(sender, display, level)
        
        # 紧急消息提醒
        if msg_type in ["emergency", "alert"]:
            self.flash_window()
            self.play_alarm()
    
    def add_message(self, sender, content, level="normal"):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{timestamp}] {content}")
        
        colors = {
            "danger": "#f44336",
            "emergency": "#f44336",
            "warning": "#ff9800",
            "system": "#2196F3",
            "normal": "#fff"
        }
        item.setForeground(QColor(colors.get(level, "#fff")))
        
        self.msg_list.addItem(item)
        self.msg_list.scrollToBottom()
    
    def send_notification(self):
        content = self.msg_input.text().strip()
        if not content:
            return
        
        msg = {
            "type": "normal",
            "level": "normal",
            "title": "管理员通知",
            "content": content,
            "sender": "admin"
        }
        
        self.ws_thread.send_message(msg)
        self.add_message("我", f"通知: {content}", "system")
        self.msg_input.clear()
    
    def send_test(self):
        msg = {
            "type": "normal",
            "level": "normal",
            "title": "测试消息",
            "content": "这是一条测试消息",
            "sender": "admin"
        }
        self.ws_thread.send_message(msg)
        self.add_message("我", "测试消息已发送", "system")
    
    def flash_window(self):
        for _ in range(3):
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTop)
            self.show()
            QThread.msleep(200)
            self.hide()
            QThread.msleep(200)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTop)
        self.show()
    
    def play_alarm(self):
        # 简单的系统提示音
        QApplication.beep()
    
    def closeEvent(self, event):
        if self.ws_thread:
            self.ws_thread.stop()
        event.accept()

def main():
    app = QApplication(sys.argv)
    window = AdminClient()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
