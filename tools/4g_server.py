#!/usr/bin/env python3
"""
4G模块网络透传服务器
运行在Windows电脑上，把4G模块的串口暴露到网络上
"""

import socket
import serial
import threading
import sys

# 配置
SERIAL_PORT = "COM3"  # Windows上的串口号 (需要改成实际的)
BAUD_RATE = 115200
LISTEN_PORT = 9999   # 监听端口

def handle_client(client_socket, ser):
    """处理客户端连接"""
    print(f"[*] 客户端已连接")
    try:
        while True:
            # 读取串口数据发送给客户端
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting)
                client_socket.sendall(data)
            
            # 读取客户端数据发送给串口
            client_socket.settimeout(0.1)
            try:
                data = client_socket.recv(1024)
                if data:
                    ser.write(data)
                    print(f"[->] 发送AT命令: {data.decode('utf-8', errors='ignore').strip()}")
            except socket.timeout:
                pass
    except Exception as e:
        print(f"[!] 错误: {e}")
    finally:
        client_socket.close()
        print(f"[*] 客户端已断开")

def main():
    # 尝试打开串口
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        print(f"[*] 串口 {SERIAL_PORT} 已打开")
    except Exception as e:
        print(f"[!] 无法打开串口 {SERIAL_PORT}: {e}")
        print("[*] 可用串口:")
        import serial.tools.list_ports
        for port in serial.tools.list_ports.comports():
            print(f"  - {port.device}: {port.description}")
        return

    # 创建TCP服务器
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server.bind(("0.0.0.0", LISTEN_PORT))
        server.listen(1)
        print(f"[*] 服务器启动，监听端口 {LISTEN_PORT}")
        print(f"[*] 在开发板上运行客户端连接 {LISTEN_PORT}")
        
        while True:
            client_socket, addr = server.accept()
            print(f"[*] 接受连接: {addr}")
            # 每个客户端单独处理
            thread = threading.Thread(target=handle_client, args=(client_socket, ser))
            thread.daemon = True
            thread.start()
            
    except KeyboardInterrupt:
        print("\n[*] 服务器关闭")
    except Exception as e:
        print(f"[!] 服务器错误: {e}")
    finally:
        server.close()
        ser.close()

if __name__ == "__main__":
    main()
