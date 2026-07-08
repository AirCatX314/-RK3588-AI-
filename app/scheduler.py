#!/usr/bin/env python3
"""
定时任务调度器
"""

import threading
import time
import json
import os
from datetime import datetime, timezone, timedelta

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))

# 任务配置
SCHEDULE_FILE = '/home/elf/labsafe/schedule.json'

class TaskScheduler:
    def __init__(self):
        self.tasks = []
        self.running = False
        self.thread = None
        self.load_tasks()
    
    def load_tasks(self):
        """加载任务配置"""
        if os.path.exists(SCHEDULE_FILE):
            try:
                with open(SCHEDULE_FILE, 'r') as f:
                    data = json.load(f)
                    self.tasks = data.get('tasks', [])
                    print(f"已加载 {len(self.tasks)} 个定时任务")
            except Exception as e:
                print(f"加载任务失败: {e}")
    
    def save_tasks(self):
        """保存任务配置"""
        try:
            with open(SCHEDULE_FILE, 'w') as f:
                json.dump({'tasks': self.tasks}, f, indent=2)
        except Exception as e:
            print(f"保存任务失败: {e}")
    
    def add_task(self, name, interval_hours=None, send_time=None, enabled=True):
        """添加定时任务"""
        task = {
            'id': len(self.tasks) + 1,
            'name': name,
            'interval_hours': interval_hours,
            'send_time': send_time,  # 格式: "HH:MM"
            'enabled': enabled,
            'last_run': None,
            'created': datetime.now(BEIJING_TZ).isoformat()
        }
        self.tasks.append(task)
        self.save_tasks()
        return task
    
    def remove_task(self, task_id):
        """删除任务"""
        self.tasks = [t for t in self.tasks if t['id'] != task_id]
        self.save_tasks()
    
    def toggle_task(self, task_id, enabled):
        """开关任务"""
        for task in self.tasks:
            if task['id'] == task_id:
                task['enabled'] = enabled
                self.save_tasks()
                return True
        return False
    
    def start(self):
        """启动调度器"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        print("任务调度器已启动")
    
    def stop(self):
        """停止调度器"""
        self.running = False
    
    def _run(self):
        """运行调度循环"""
        while self.running:
            now = datetime.now(BEIJING_TZ)  # 使用北京时间
            current_time = now.strftime("%H:%M")
            
            for task in self.tasks:
                if not task.get('enabled', False):
                    continue
                
                last_run = task.get('last_run')
                send_time = task.get('send_time')  # 固定时间触发
                interval = task.get('interval_hours', 24)
                
                should_run = False
                
                # 优先使用固定时间触发
                if send_time:
                    # 检查是否到达设定时间且今天还没执行过
                    if current_time == send_time:
                        if last_run:
                            try:
                                last_date = datetime.fromisoformat(last_run).date()
                                if last_date != now.date():
                                    should_run = True
                            except:
                                should_run = True
                        else:
                            should_run = True
                else:
                    # 兼容间隔模式
                    if last_run is None:
                        should_run = True
                    else:
                        try:
                            last_time = datetime.fromisoformat(last_run)
                            hours_diff = (now - last_time).total_seconds() / 3600
                            if hours_diff >= interval:
                                should_run = True
                        except:
                            pass
                
                if should_run:
                    print(f"执行任务: {task['name']}")
                    # 执行任务回调
                    self._execute_task(task)
                    task['last_run'] = now.isoformat()
                    self.save_tasks()
            
            time.sleep(60)  # 每分钟检查一次
    
    def _execute_task(self, task):
        """执行具体任务"""
        task_name = task.get('name', '')
        
        # 根据任务名称调用对应的处理函数
        if task_name == '安全分析报告':
            try:
                # 导入主程序的函数并执行
                import sys
                sys.path.insert(0, '/home/elf/labsafe')
                from app.main import api_ai_analyze_internal, send_security_report
                result = api_ai_analyze_internal()
                if result.get('success'):
                    report = result.get('report', '')
                    send_security_report(report)
                print(f"✅ 定时任务执行完成: {task_name}")
            except Exception as e:
                print(f"任务执行失败: {e}")
        elif task.get('callback'):
            try:
                task['callback'](task)
            except Exception as e:
                print(f"任务执行失败: {e}")

# 全局调度器实例
scheduler = TaskScheduler()

def get_scheduler():
    """获取调度器"""
    return scheduler
