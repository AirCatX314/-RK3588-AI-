"""
可视化模块 - 绘制检测框和追踪结果
"""

import cv2
import numpy as np


# 颜色调色板 (BGR)
COLORS = [
    (255, 0, 0),    # 蓝
    (0, 255, 0),    # 绿
    (0, 0, 255),    # 红
    (255, 255, 0),  # 青
    (255, 0, 255),  # 洋红
    (0, 255, 255),  # 黄
    (128, 0, 128),  # 紫
    (255, 165, 0),  # 橙
    (0, 128, 128),  # 蓝绿
    (128, 128, 0),  # 橄榄
]


def get_color(track_id):
    """根据track_id获取颜色"""
    return COLORS[track_id % len(COLORS)]


def draw_detections(frame, detections, show_labels=True, show_scores=False):
    """
    绘制检测框（无追踪）
    
    Args:
        frame: 视频帧
        detections: 检测结果列表
        show_labels: 是否显示类别名称
        show_scores: 是否显示置信度
    
    Returns:
        绘制好的帧
    """
    for det in detections:
        bbox = det['bbox']
        x1, y1, x2, y2 = map(int, bbox)
        
        # 画框
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        
        # 标签
        if show_labels:
            label = det['class_name']
            if show_scores:
                label += f" {det['score']:.2f}"
            
            # 背景
            (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1-h-4), (x1+w, y1), (0, 255, 0), -1)
            cv2.putText(frame, label, (x1, y1-2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    
    return frame


def draw_tracks(frame, tracks, show_labels=True, show_ids=True, show_scores=False):
    """
    绘制追踪框
    
    Args:
        frame: 视频帧
        tracks: 追踪结果列表
        show_labels: 是否显示类别名称
        show_ids: 是否显示追踪ID
        show_scores: 是否显示置信度
    
    Returns:
        绘制好的帧
    """
    for track in tracks:
        bbox = track['bbox']
        x1, y1, x2, y2 = map(int, bbox)
        
        track_id = track['track_id']
        color = get_color(track_id)
        
        # 画框
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        
        # 标签内容
        label_parts = []
        if show_ids:
            label_parts.append(f"ID:{track_id}")
        if show_labels:
            label_parts.append(track.get('class_name', 'unknown'))
        if show_scores and 'score' in track:
            label_parts.append(f"{track['score']:.2f}")
        
        if label_parts:
            label = " ".join(label_parts)
            
            # 背景
            (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1-h-4), (x1+w, y1), color, -1)
            cv2.putText(frame, label, (x1, y1-2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    
    return frame


def draw_info(frame, info_dict, position=(10, 30)):
    """
    在帧上绘制信息
    
    Args:
        frame: 视频帧
        info_dict: 信息字典 {'检测数': 5, '追踪数': 3, ...}
        position: 起始位置 (x, y)
    
    Returns:
        绘制好的帧
    """
    x, y = position
    for key, value in info_dict.items():
        text = f"{key}: {value}"
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
        y += 30
    
    return frame


def create_mask(frame, detections, class_names=None, expand=10):
    """
    创建检测区域的掩码（可用于ROI等）
    
    Args:
        frame: 视频帧
        detections: 检测结果
        class_names: 感兴趣的类别列表，None=所有
        expand: 扩展像素
    
    Returns:
        mask掩码
    """
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    
    for det in detections:
        if class_names and det['class_name'] not in class_names:
            continue
        
        bbox = det['bbox']
        x1, y1, x2, y2 = map(int, bbox)
        
        # 扩展
        x1 = max(0, x1 - expand)
        y1 = max(0, y1 - expand)
        x2 = min(frame.shape[1], x2 + expand)
        y2 = min(frame.shape[0], y2 + expand)
        
        mask[y1:y2, x1:x2] = 255
    
    return mask
