#!/bin/bash
# 实验室安全报告定时发送脚本
# 每天16:35自动执行

cd /home/elf/labsafe

# 生成报告
python3 << 'PYEOF'
import json
import requests
from datetime import datetime
from app.ai_analysis import generate_report

try:
    # 获取状态
    try:
        status = requests.get('http://127.0.0.1:5000/api/status', timeout=3).json()
    except:
        status = {'fire_state': {'temperature': 25.0, 'humidity': 50.0, 'sensor_status': 'ok'}}

    fire_state = status.get('fire_state', {})
    temp = fire_state.get('temperature', 25.0)
    humidity = fire_state.get('humidity', 50.0)
    fire_result = {'fire_detected': False, 'confidence': 0.0, 'temperature': temp}

    report = generate_report({}, fire_result)

    # 发送邮件
    from app.email_notify import send_security_report
    result = send_security_report(report)
    print(f"[{datetime.now()}] 安全报告发送结果: {'成功' if result else '失败'}")
except Exception as e:
    print(f"[{datetime.now()}] 发送失败: {e}")
PYEOF

echo "[$(date)] 定时报告任务执行完成"