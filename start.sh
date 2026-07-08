#!/bin/bash
# LabSafe 系统启动脚本

LOCK_FILE="/tmp/labsafe.lock"

# 等待 X server 就绪
wait_for_x() {
    local max_wait=30
    local count=0
    echo "等待 X server 就绪..."
    while [ $count -lt $max_wait ]; do
        if [ -S "/tmp/.X11-unix/X0" ] && xauth -f /run/user/1000/gdm/Xauthority list 2>/dev/null | grep -q ":0"; then
            echo "X server 已就绪"
            return 0
        fi
        sleep 1
        count=$((count + 1))
    done
    echo "警告: 等待 X server 超时，继续尝试..."
    return 1
}

wait_for_x

# 检查锁文件
if [ -f "$LOCK_FILE" ]; then
    OLD_PID=$(cat "$LOCK_FILE")
    # 检查旧进程是否还在
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "LabSafe is already running (PID: $OLD_PID)"
        exit 0
    else
        echo "Stale lock file found, removing..."
        rm -f "$LOCK_FILE"
    fi
fi

# 创建锁文件
echo $$ > "$LOCK_FILE"

# 清理函数
cleanup() {
    rm -f "$LOCK_FILE"
}
trap cleanup EXIT

echo "Starting LabSafe system..."

# 启动消息服务器
cd /home/elf/labsafe/qt_system
python3 msg_server.py > /tmp/msg.log 2>&1 &
MSG_PID=$!
echo "Started msg_server (PID: $MSG_PID)"

sleep 1

# 启动Web服务器
cd /home/elf/labsafe
PYTHONPATH=/home/elf/labsafe python3 -m app.main > /tmp/labsafe.log 2>&1 &
WEB_PID=$!
echo "Started app.main (PID: $WEB_PID)"

sleep 1

# 启动Qt客户端
cd /home/elf/labsafe/qt_system
export QT_QPA_PLATFORM=xcb
python3 lab_client.py > /tmp/qt.log 2>&1 &
QT_PID=$!
echo "Started lab_client (PID: $QT_PID)"

echo "LabSafe system started successfully (PIDs: $MSG_PID $WEB_PID $QT_PID)"

wait
