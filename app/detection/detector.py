"""
目标检测器 - 支持多种模型
"""

import numpy as np
from abc import ABC, abstractmethod


class BaseDetector(ABC):
    """检测器基类"""
    
    @abstractmethod
    def detect(self, frame):
        """检测 frame，返回检测结果列表"""
        pass
    
    @abstractmethod
    def load_model(self, model_path, **kwargs):
        """加载模型"""
        pass


class YOLODetector(BaseDetector):
    """YOLO 检测器"""
    
    def __init__(self):
        self.model = None
        self.confidence = 0.5  # 置信度阈值
        self.classes = None     # 检测类别，None=所有类
    
    def load_model(self, model_name='yolov8n.pt', **kwargs):
        """
        加载YOLO模型
        
        Args:
            model_name: 模型名称 (yolov8n.pt, yolov8s.pt, yolov8m.pt等)
            kwargs: 额外参数 (confidence, classes)
        """
        from ultralytics import YOLO
        
        self.model = YOLO(model_name)
        self.confidence = kwargs.get('confidence', 0.5)
        self.classes = kwargs.get('classes', None)
        
        print(f"[Detector] Loaded YOLO model: {model_name}")
    
    def detect(self, frame):
        """
        检测目标
        
        Returns:
            list: [{'bbox': [x1,y1,x2,y2], 'score': float, 'class_id': int, 'class_name': str}, ...]
        """
        if self.model is None:
            raise RuntimeError("Model not loaded! Call load_model() first.")
        
        results = self.model(frame, conf=self.confidence, classes=self.classes, verbose=False)
        
        detections = []
        if len(results) > 0:
            result = results[0]
            if result.boxes is not None:
                boxes = result.boxes.cpu().numpy()
                for i in range(len(boxes)):
                    detections.append({
                        'bbox': boxes.xyxy[i].tolist(),      # [x1,y1,x2,y2]
                        'score': float(boxes.conf[i]),        # 置信度
                        'class_id': int(boxes.cls[i]),        # 类别ID
                        'class_name': result.names[int(boxes.cls[i])]  # 类别名称
                    })
        
        return detections


class DetectorFactory:
    """检测器工厂"""
    
    _detectors = {
        'yolo': YOLODetector,
    }
    
    @classmethod
    def create(cls, detector_type='yolo', **kwargs):
        """创建检测器"""
        if detector_type not in cls._detectors:
            raise ValueError(f"Unknown detector: {detector_type}. Available: {list(cls._detectors.keys())}")
        
        detector = cls._detectors[detector_type]()
        model_path = kwargs.pop('model_path', kwargs.pop('model_name', 'yolov8n.pt'))
        detector.load_model(model_path, **kwargs)
        
        return detector
    
    @classmethod
    def register(cls, name, detector_class):
        """注册新的检测器"""
        cls._detectors[name] = detector_class


# ==================== 便捷函数 ====================

def create_detector(model_name='yolov8n.pt', confidence=0.5, classes=None):
    """
    快速创建YOLO检测器
    
    Args:
        model_name: 模型名称
        confidence: 置信度阈值
        classes: 检测类别列表，如 [0] 只检测人, [0,2] 检测人和车
    
    Returns:
        YOLODetector实例
    """
    detector = YOLODetector()
    detector.load_model(model_name, confidence=confidence, classes=classes)
    return detector
