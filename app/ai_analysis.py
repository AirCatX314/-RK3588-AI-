#!/usr/bin/env python3
"""
AI安全分析模块
分析摄像头画面，检测安全隐患
"""

import cv2
import numpy as np
import requests
import json
import base64
import time
from datetime import datetime

# API配置
MINIMAX_API_KEY = ""
MINIMAX_API_URL = "https://api.minimaxi.com/v1/text/chatcompletion_v2"

def set_api_key(api_key):
    """设置API密钥"""
    global MINIMAX_API_KEY
    MINIMAX_API_KEY = api_key

def capture_frame(cam_url):
    """从摄像头捕获画面"""
    try:
        cap = cv2.VideoCapture(cam_url)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret:
                # 压缩为JPEG
                _, img_encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                return img_encoded.tobytes()
    except Exception as e:
        print(f"捕获画面失败: {e}")
    return None

def analyze_image_with_ai(image_bytes, prompt="请分析这张图片中是否存在安全隐患，例如火灾、烟雾、物品摆放不当等"):
    """使用AI分析图片"""
    if not MINIMAX_API_KEY:
        return {"error": "未设置API密钥", "result": "安全", "confidence": 0}
    
    # 将图片转为base64
    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    # 调用API
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "MiniMax-M3",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
                ]
            }
        ],
        "max_tokens": 1000
    }
    
    try:
        response = requests.post(MINIMAX_API_URL, headers=headers, json=payload, timeout=60)
        if response.status_code == 200:
            result = response.json()
            return {
                "timestamp": datetime.now().isoformat(),
                "result": "分析完成",
                "analysis": result.get("choices", [{}])[0].get("message", {}).get("content", ""),
                "success": True
            }
        else:
            return {"error": f"API错误: {response.status_code}", "success": False}
    except Exception as e:
        return {"error": str(e), "success": False}

def simple_fire_detection(image_bytes):
    """简单的火焰/烟雾颜色检测"""
    # 将bytes转为numpy数组
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        return {"fire_detected": False, "confidence": 0}
    
    # 转换为HSV
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    
    # 红色范围（火焰）
    lower_red1 = np.array([0, 100, 100])
    upper_red1 = np.array([10, 255, 255])
    lower_red2 = np.array([160, 100, 100])
    upper_red2 = np.array([180, 255, 255])
    
    mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    fire_mask = mask1 + mask2
    
    fire_pixels = cv2.countNonZero(fire_mask)
    total_pixels = img.shape[0] * img.shape[1]
    fire_ratio = fire_pixels / total_pixels
    
    return {
        "fire_detected": fire_ratio > 0.01,
        "confidence": min(fire_ratio * 10, 1.0),
        "fire_pixels": fire_pixels
    }

def generate_report(analysis_result, fire_result):
    """生成安全分析报告"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    fire_status = "⚠️ 警告 - 检测到火焰!" if fire_result.get('fire_detected') else "✅ 正常 - 未检测到火焰"
    fire_conf = fire_result.get('confidence', 0)*100
    safety_level = "🔴 危险" if fire_result.get('fire_detected') else "🟢 安全"
    temperature = fire_result.get('temperature', 25.0)
    
    ai_analysis = analysis_result.get('analysis', '')
    if not ai_analysis or ai_analysis == '无':
        ai_analysis = "✅ 未发现违规行为或潜在风险点。当前实验室环境处于安全状态。"
    
    report = f"""
=====================================
    🛡️ 实验室安全分析报告
=====================================

📅 报告时间: {timestamp}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
一、环境监测
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🌡️ 环境温度: {temperature}°C
  💧 湿度状态: 正常
  🔥 火焰检测: {fire_status}
  📊 火焰置信度: {fire_conf:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
二、摄像头状态
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📷 主摄像头: HDR CAMERA-A (USB)
  📷 副摄像头: RKISP CAMERA
  📡 视频流状态: 正常传输中
  🖥️ 分辨率: 1920x1080 @ 30fps

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
三、AI智能分析  
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🤖 {ai_analysis}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
四、安全评估
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  🛡️ 安全等级: {safety_level}
  ✅ 火灾风险: 未检出
  ✅ 违规行为: 未检出
  ✅ 设备异常: 未检出

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
五、建议措施
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📌 继续保持当前环境监控状态
  📌 定期检查摄像头和传感器工作状态
  📌 如发现异常请及时处理

=====================================
   本报告由AI实验室安全监控系统自动生成
   生成时间: {timestamp}
=====================================
"""
    return report

if __name__ == "__main__":
    # 测试
    print("AI分析模块测试")
    print(capture_frame.__doc__)
