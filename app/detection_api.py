"""
目标检测API模块 - 集成到Flask
支持 RKNN NPU 加速
"""

import cv2
import time
import threading
import numpy as np
from collections import defaultdict
import sys
import os

# 添加 detection 目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'detection'))
import config


class DetectionAPI:
    """目标检测API"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance
    
    def _init(self):
        """初始化"""
        self.model = None
        self.rknn_detector = None
        self.tracker = None
        self.enabled = False
        self.detection_classes = getattr(config, 'CLASSES', None)
        self.confidence = float(getattr(config, 'CONFIDENCE', 0.25))
        self.frame_cache = {}  # 缓存每个摄像头的检测结果
        self._model_loading = False
        self._detect_lock = threading.Lock()
        self._last_detections = []
        self._last_latency_ms = None
        self._last_output_shapes = []
        # 性能优化参数
        self.resize_width = getattr(config, 'DETECTION_SIZE', 512)
        self.skip_frames = 1     # 每帧都检测，避免追踪框残留
        self._frame_count = 0    # 帧计数器
        self._last_tracks = []   # 缓存上一帧的追踪结果
        
        # 检测器类型
        self.detector_type = getattr(config, 'DETECTOR_TYPE', 'yolo')
        self.rknn_model_path = getattr(config, 'RKNN_MODEL_PATH', '/home/elf/Desktop/yolo26m_rknn_model/yolo26m-rk3588.rknn')
    
    def load_model(self, model_name=None):
        """加载模型 - 支持 YOLO 和 RKNN"""
        if self._model_loading:
            return False
        
        self._model_loading = True
        try:
            from app.detection import Tracker
            
            # 根据配置选择检测器类型
            if self.detector_type == 'rknn':
                # 使用 RKNN NPU 加速
                print(f"[DetectionAPI] Loading RKNN model (NPU加速): {self.rknn_model_path}")
                from app.detection.rknn_detector import RKNNDetector
                
                self.rknn_detector = RKNNDetector()
                self.rknn_detector.load_model(
                    self.rknn_model_path,
                    confidence=self.confidence,
                    classes=self.detection_classes,
                    input_size=getattr(config, 'DETECTION_SIZE', 512),
                    iou_threshold=getattr(config, 'IOU_THRESHOLD', 0.45)
                )
                print(f"[DetectionAPI] RKNN Model loaded successfully! 🚀")
            else:
                # 使用 YOLO
                model_name = model_name or 'yolov8n.pt'
                print(f"[DetectionAPI] Loading YOLO model: {model_name}")
                from ultralytics import YOLO
                self.model = YOLO(model_name)
                print(f"[DetectionAPI] YOLO Model loaded successfully!")
            
            # 检测结果直接绘制。追踪器保留但不作为画框前置条件，避免火焰/烟雾瞬时目标被延迟显示。
            self.tracker = Tracker(max_age=30, min_hits=1, iou_threshold=0.3)
            self.enabled = True
            
            return True
        except Exception as e:
            print(f"[DetectionAPI] Error loading model: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            self._model_loading = False
    
    def detect_frame(self, frame, cam_id=None):
        """
        检测单帧（带性能优化）
        
        Returns:
            frame: 带检测框的图像
            detections: 检测结果列表
        """
        if not self.enabled:
            return frame, []
        
        self._frame_count += 1
        
        # 隔帧检测：跳过检测时使用上一帧结果
        if self.skip_frames > 1 and self._frame_count % self.skip_frames != 0:
            # 复用上一帧的追踪结果
            frame = self._draw_detections(frame, self._last_detections)
            return frame, []
        
        try:
            start = time.time()
            with self._detect_lock:
                # 使用 RKNN 或 YOLO 检测
                if self.rknn_detector is not None:
                    detections = self.rknn_detector.detect(frame)
                    self._last_output_shapes = getattr(self.rknn_detector, 'output_shapes', [])
                else:
                    detections = self._yolo_detect(frame)
                    self._last_output_shapes = []

            self._last_latency_ms = (time.time() - start) * 1000.0
            self._last_detections = detections

            # 绘制检测框。这里直接画检测结果，不等 tracker confirmed。
            frame = self._draw_detections(frame, detections)
            
            return frame, detections
            
        except Exception as e:
            print(f"[DetectionAPI] Detection error: {e}")
            import traceback
            traceback.print_exc()
            return frame, []
    
    def _yolo_detect(self, frame):
        """YOLO 检测"""
        if self.model is None:
            return []
        
        # 性能优化：降采样
        h, w = frame.shape[:2]
        
        # 使用配置中的分辨率
        det_size = getattr(config, 'DETECTION_SIZE', 320)
        scale = det_size / w if w > det_size else 1.0
        if scale < 1.0:
            small_frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        else:
            small_frame = frame
        
        # 检测
        results = self.model(small_frame, conf=self.confidence, classes=self.detection_classes, imgsz=det_size, verbose=False)
        
        detections = []
        if len(results) > 0:
            result = results[0]
            if result.boxes is not None:
                boxes = result.boxes.cpu().numpy()
                for i in range(len(boxes)):
                    bbox_scaled = boxes.xyxy[i].tolist()
                    if scale < 1.0:
                        bbox_scaled = [b / scale for b in bbox_scaled]
                    detections.append({
                        'bbox': bbox_scaled,
                        'score': float(boxes.conf[i]),
                        'class_id': int(boxes.cls[i]),
                        'class_name': result.names[int(boxes.cls[i])]
                    })
        
        return detections
    
    def _draw_detections(self, frame, detections):
        """绘制检测框"""
        COLORS = [
            (45, 170, 255),   # lab_coat
            (68, 220, 68),    # face_shield
            (255, 190, 60),   # gloves
            (190, 120, 255),  # goggles
            (60, 220, 220),   # mask
            (40, 40, 240),    # flame
            (80, 80, 255),    # smoke
        ]
        
        for det in detections:
            bbox = det['bbox']
            x1, y1, x2, y2 = map(int, bbox)
            class_id = int(det.get('class_id', 0))
            class_name = det.get('class_name', 'unknown')
            score = float(det.get('score', 0.0))
            color = COLORS[class_id % len(COLORS)]
            
            # 画框
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            
            # 标签
            label = f"{class_name} {score:.2f}"
            (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
            y_text = max(0, y1 - th - baseline - 4)
            cv2.rectangle(frame, (x1, y_text), (x1 + tw + 6, y_text + th + baseline + 4), color, -1)
            cv2.putText(
                frame,
                label,
                (x1 + 3, y_text + th + 1),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 0, 0),
                2,
                cv2.LINE_AA,
            )
        
        return frame
    
    def get_status(self):
        """获取状态"""
        return {
            'available': True,
            'enabled': self.enabled,
            'model_loaded': self.model is not None or self.rknn_detector is not None,
            'detector_type': self.detector_type,
            'rknn_model_path': self.rknn_model_path if self.detector_type == 'rknn' else None,
            'detection_classes': self.detection_classes,
            'class_names': getattr(self.rknn_detector, 'names', {}) if self.rknn_detector is not None else {},
            'confidence': self.confidence,
            'last_latency_ms': self._last_latency_ms,
            'last_output_shapes': self._last_output_shapes,
            'last_detections': self._last_detections[-20:]
        }
    
    def set_config(self, detection_classes=None, confidence=None):
        """设置配置"""
        if detection_classes is not None:
            self.detection_classes = detection_classes
        if confidence is not None:
            self.confidence = confidence
            if self.rknn_detector is not None:
                self.rknn_detector.confidence = confidence


# 单例
detection_api = DetectionAPI()
