"""
实时目标检测示例 - 实验室安全系统 (支持多模型)
用法: python demo_realtime.py [camera_id]
"""

import cv2
import sys
import time
import requests
import config
from detection import create_detector, Tracker, draw_tracks, draw_info

try:
    from multi_detector import create_person_fire_detector, MultiDetector
    MULTI_AVAILABLE = True
except ImportError:
    MULTI_AVAILABLE = False


# ==================== 配置 ====================
# 实验室系统API
LABSAFE_API = "http://localhost:5000"


def get_camera_stream(camera_id="living-room"):
    """获取摄像头视频流"""
    url = f"{LABSAFE_API}/api/camera/{camera_id}/snapshot"
    while True:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                import numpy as np
                nparr = np.frombuffer(resp.content, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is not None:
                    yield frame
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(0.1)


def create_multi_tracker():
    """创建多目标追踪器"""
    return {
        'person': Tracker(
            max_age=config.PERSON_TRACKER_CONFIG['max_age'],
            min_hits=config.PERSON_TRACKER_CONFIG['min_hits'],
            iou_threshold=config.PERSON_TRACKER_CONFIG['iou_threshold']
        ),
        'fire': Tracker(
            max_age=config.FIRE_TRACKER_CONFIG['max_age'],
            min_hits=config.FIRE_TRACKER_CONFIG['min_hits'],
            iou_threshold=config.FIRE_TRACKER_CONFIG['iou_threshold']
        )
    }


def draw_multi_tracks(frame, tracks_dict, show_labels=True, show_ids=True):
    """绘制多模型追踪结果"""
    for model_name, tracks in tracks_dict.items():
        for track in tracks:
            bbox = track['bbox']
            x1, y1, x2, y2 = map(int, bbox)
            
            # 颜色: 人物=绿色, 火灾=红色
            if model_name == 'fire':
                color = (0, 0, 255)  # 红色
            else:
                color = (0, 255, 0)  # 绿色
            
            track_id = track.get('track_id', 0)
            
            # 画框
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # 标签
            label_parts = []
            if show_ids:
                label_parts.append(f"ID:{track_id}")
            label_parts.append(track.get('class_name', model_name))
            
            label = " ".join(label_parts)
            
            # 背景
            (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(frame, (x1, y1-h-4), (x1+w, y1), color, -1)
            cv2.putText(frame, label, (x1, y1-2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    
    return frame


def main():
    print("=" * 50)
    print("实验室安全系统 - 实时目标检测")
    print("=" * 50)
    
    if not MULTI_AVAILABLE:
        print("错误: multi_detector.py 未找到")
        return
    
    print("\n[1/4] 加载检测模型...")
    
    try:
        # 尝试加载多模型检测器
        detector = create_person_fire_detector()
        print("    模式: 人物检测 + 火灾检测")
    except Exception as e:
        print(f"    错误: {e}")
        print("    回退到单模型...")
        detector = create_detector(
            model_name=config.MODEL_NAME,
            confidence=config.CONFIDENCE,
            classes=config.CLASSES
        )
    
    print("\n[2/4] 初始化追踪器...")
    trackers = create_multi_tracker()
    print(f"    人物追踪: max_age={config.PERSON_TRACKER_CONFIG['max_age']}")
    print(f"    火灾追踪: max_age={config.FIRE_TRACKER_CONFIG['max_age']}")
    
    print("\n[3/4] 启动实时检测...")
    print("    按 'q' 退出")
    print("    按 's' 保存截图")
    print("=" * 50)
    
    camera_id = sys.argv[1] if len(sys.argv) > 1 else config.CAMERA_ID
    
    frame_count = 0
    fps = 0
    last_time = time.time()
    
    for frame in get_camera_stream(camera_id):
        if frame is None:
            continue
        
        frame_count += 1
        
        # 计算FPS
        if frame_count % 30 == 0:
            fps = 30 / (time.time() - last_time)
            last_time = time.time()
        
        # 检测
        if isinstance(detector, MultiDetector):
            detections = detector.detect(frame)
            
            # 分别追踪
            tracks_dict = {}
            for model_name, dets in detections.items():
                tracks_dict[model_name] = trackers[model_name].update(dets)
            
            # 绘制
            frame = draw_multi_tracks(frame, tracks_dict)
            
            # 信息
            person_count = len(tracks_dict.get('person', []))
            fire_count = len(tracks_dict.get('fire', []))
            
            info = {
                "FPS": f"{fps:.1f}",
                "人物": person_count,
                "火灾": fire_count,
            }
        else:
            # 单模型
            detections = detector.detect(frame)
            tracks = trackers['person'].update(detections)
            frame = draw_tracks(frame, tracks, show_labels=True, show_ids=True)
            
            info = {
                "FPS": f"{fps:.1f}",
                "检测": len(detections),
                "追踪": len(tracks),
            }
        
        # 火灾报警
        if fire_count > 0:
            cv2.putText(frame, "⚠️ 火灾警告!", (frame.shape[1]//2-100, 50),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
        
        frame = draw_info(frame, info)
        
        # 显示
        cv2.imshow("LabSafe - 实时目标检测", frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('s'):
            cv2.imwrite(f"snapshot_{int(time.time())}.jpg", frame)
            print(f"截图已保存")
    
    cv2.destroyAllWindows()
    print("\n退出!")


if __name__ == "__main__":
    main()
