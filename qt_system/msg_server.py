#!/usr/bin/env python3
"""
实验室安全监控系统 - 消息服务器
支持WebSocket连接，转发报警和消息
"""

import asyncio
import json
import datetime
from websockets import serve, WebSocketServerProtocol
from collections import defaultdict

# 连接管理
clients = defaultdict(set)  # room -> set of websockets
all_clients = set()

# 消息历史
message_history = []

async def handle_client(websocket: WebSocketServerProtocol, path: str):
    """处理客户端连接"""
    client_id = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
    all_clients.add(websocket)
    print(f"客户端连接: {client_id}")
    
    # 发送欢迎消息
    await websocket.send(json.dumps({
        "type": "system",
        "content": "已连接到实验室安全监控系统",
        "timestamp": datetime.datetime.now().isoformat()
    }))
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                await process_message(websocket, data)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({
                    "type": "error",
                    "content": "消息格式错误"
                }))
    except Exception as e:
        print(f"客户端断开: {client_id}, 原因: {e}")
    finally:
        all_clients.remove(websocket)

async def process_message(websocket, data: dict):
    """处理收到的消息"""
    msg_type = data.get("type", "normal")
    level = data.get("level", "normal")
    title = data.get("title", "")
    content = data.get("content", "")
    sender = data.get("sender", "未知设备")
    
    timestamp = datetime.datetime.now().isoformat()
    
    # 构建消息
    message = {
        "type": msg_type,
        "level": level,
        "title": title,
        "content": content,
        "sender": sender,
        "timestamp": timestamp
    }
    
    # 保存到历史
    message_history.append(message)
    if len(message_history) > 1000:
        message_history.pop(0)
    
    # 打印日志
    print(f"[{timestamp}] {sender} - {title}: {content}")
    
    # 根据消息类型处理
    if msg_type == "alert" or msg_type == "emergency":
        # 广播给所有客户端
        await broadcast(message)
    elif msg_type == "status":
        # 状态更新，只转发给管理员
        await broadcast_to_role(message, "admin")
    elif msg_type == "normal":
        # 普通消息，广播给所有人
        await broadcast(message)
    elif msg_type == "ping":
        # 心跳响应
        await websocket.send(json.dumps({"type": "pong"}))
    else:
        # 其他消息广播
        await broadcast(message)

async def broadcast(message: dict):
    """广播消息给所有客户端"""
    if all_clients:
        await asyncio.gather(
            *[client.send(json.dumps(message)) for client in all_clients],
            return_exceptions=True
        )

async def broadcast_to_role(message: dict, role: str):
    """广播给特定角色的客户端"""
    # 目前简单实现，广播给所有
    await broadcast(message)

async def get_history(count: int = 50):
    """获取历史消息"""
    return message_history[-count:]

async def main():
    """启动服务器"""
    print("=" * 50)
    print("🔔 实验室安全监控消息服务器")
    print("=" * 50)
    print("WebSocket端口: 8765")
    print()
    
    # 启动WebSocket服务器 (兼容新版本)
    async with serve(handle_client, "0.0.0.0", 8765, ping_interval=30, ping_timeout=10):
        print("服务器已启动，等待连接...")
        await asyncio.Future()  # 永久运行

if __name__ == "__main__":
    asyncio.run(main())
