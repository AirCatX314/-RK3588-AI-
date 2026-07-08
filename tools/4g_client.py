#!/usr/bin/env python3
"""
4G模块网络透传客户端
运行在开发板上，通过网络远程控制4G模块
"""

import socket
import sys
import time

# 配置 - 改成Windows电脑的IP地址
SERVER_IP = "192.168.1.xxx"  # Windows电脑的IP地址
SERVER_PORT = 9999

def connect():
    """连接到服务器"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((SERVER_IP, SERVER_PORT))
        print(f"[*] 已连接到 {SERVER_IP}:{SERVER_PORT}")
        return s
    except Exception as e:
        print(f"[!] 连接失败: {e}")
        return None

def send_at(s, command):
    """发送AT命令"""
    try:
        # 发送AT命令
        cmd = (command + "\r\n").encode('utf-8')
        s.sendall(cmd)
        print(f"[->] 发送: {command}")
        
        # 接收响应
        time.sleep(0.5)
        s.settimeout(2)
        try:
            response = s.recv(1024)
            print(f"[<-] 响应: {response.decode('utf-8', errors='ignore')}")
        except socket.timeout:
            print("[<-] 无响应")
    except Exception as e:
        print(f"[!] 发送失败: {e}")

def main():
    if len(sys.argv) > 1:
        global SERVER_IP
        SERVER_IP = sys.argv[1]
    
    print("=" * 50)
    print("4G模块远程控制客户端")
    print(f"服务器: {SERVER_IP}:{SERVER_PORT}")
    print("=" * 50)
    print("常用AT命令:")
    print("  AT          - 测试连接")
    print("  AT+CSQ      - 信号质量")
    print("  AT+COPS?    - 运营商")
    print("  ATDxxxxxxxx; - 拨打电话")
    print("  ATH         - 挂断")
    print("  ATA         - 接听")
    print("=" * 50)
    
    s = connect()
    if not s:
        return
    
    try:
        while True:
            cmd = input("\n输入AT命令 (q退出): ").strip()
            if not cmd:
                continue
            if cmd.lower() == 'q':
                break
            
            send_at(s, cmd)
            
    except KeyboardInterrupt:
        print("\n[*] 退出")
    finally:
        s.close()

if __name__ == "__main__":
    main()
