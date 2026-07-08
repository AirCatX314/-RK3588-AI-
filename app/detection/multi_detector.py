"""
多模型目标检测器 - 支持人物检测 + 火灾检测
支持 RKNN NPU 加速
"""

from detector import YOLODetector, DetectorFactory
from rknn_detector import RKNNDetector
import numpy as np
import config


class MultiDetector:
    """多模型检测器"""
    
    def __init__(self):
        self.detectors = {}  # {name: detector}
    
    def add_detector(self, name, model_name, confidence=0.5, classes=None, detector_type=None):
        """添加检测器"""
        # 根据配置选择检测器类型
        if detector_type is None:
            detector_type = getattr(config, 'DETECTOR_TYPE', 'yolo')
        
        if detector_type == 'rknn' and 'rknn' in model_name.lower():
            detector = RKNNDetector()
            detector.load_model(model_name, confidence=confidence, classes=classes)
        else:
            detector = YOLODetector()
            detector.load_model(model_name, confidence=confidence, classes=classes)
        
        self.detectors[name] = detector
        
        if isinstance(detector, RKNNDetector):
            print(f"[MultiDetector] Added: {name} ({model_name}) - NPU加速 🚀")
        else:
            print(f"[MultiDetector] Added: {name} ({model_name})")
        
        return self
    
    def detect(self, frame):
        """
        检测所有模型
        
        Returns:
            {
                'person': [{'bbox': [...], 'score': ..., 'class_name': ...}, ...],
                'fire': [{'bbox': [...], 'score': ..., 'class_name': ...}, ...]
            }
        """
        results = {}
        for name, detector in self.detectors.items():
            results[name] = detector.detect(frame)
        return results
    
    def get_all_detections(self, frame):
        """获取所有检测结果（扁平化）"""
        results = []
        multi_results = self.detect(frame)
        
        for model_name, detections in multi_results.items():
            for det in detections:
                det['model'] = model_name
                results.append(det)
        
        return results


def create_person_fire_detector():
    """
    创建人物+火灾检测器
    
    火灾模型说明:
    - 可以从 https://github.com/AvlabsLab/Fire-Detection 下载
    - 或使用其他开源火灾检测YOLO模型
    
    Returns:
        MultiDetector实例
    """
    multi = MultiDetector()
    
    # 检测器类型
    detector_type = getattr(config, 'DETECTOR_TYPE', 'yolo')
    
    if detector_type == 'rknn':
        # 使用 RKNN NPU 加速
        rknn_model = getattr(config, 'RKNN_MODEL_PATH', '/home/elf/Desktop/yolo26m_rknn_model/yolo26m-rk3588.rknn')
        
        multi.add_detector(
            'person',
            model_name=rknn_model,
            confidence=config.CONFIDENCE,
            classes=config.CLASSES,
            detector_type='rknn'
        )
        print("[MultiDetector] 使用 RKNN NPU 加速 🚀")
    else:
        # 使用 YOLO 模型
        multi.add_detector(
            'person',
            model_name=config.MODEL_NAME,
            confidence=config.CONFIDENCE,
            classes=config.CLASSES
        )
    
    # 火灾检测 - 需要下载火灾模型
    # 暂时使用yolov8n.pt作为占位，实际使用需要下载fire模型
    # 模型下载: https://github.com/AvlabsLab/Fire-Detection/releases
    # 放置到 /home/elf/labsafe/models/fire.pt
    import os
    fire_model_path = '/home/elf/labsafe/models/fire.pt'
    
    if os.path.exists(fire_model_path):
        multi.add_detector(
            'fire',
            model_name=fire_model_path,
            confidence=0.5,
            classes=None  # 火灾模型通常只输出fire类
        )
    else:
        print(f"[Warning] 火灾模型未找到: {fire_model_path}")
        print("[Hint] 请下载火灾检测模型并放置到该位置")
    
    return multi


# 注册到工厂
DetectorFactory.register('multi', MultiDetector)
DetectorFactory.register('person_fire', create_person_fire_detector)


if __name__ == "__main__":
    # 测试
    import cv2
    
    print("=" * 50)
    print("测试多模型检测器")
    print("=" * 50)
    
    # 创建检测器
    detector = create_person_fire_detector()
    
    # 读取测试图像
    cap = cv2.VideoCapture(0)  # 或使用视频/图像
    
    ret, frame = cap.read()
    if ret:
        results = detector.detect(frame)
        
        print("\n检测结果:")
        for model_name, detections in results.items():
            print(f"  {model_name}: {len(detections)} 个目标")
            for det in detections:
                print(f"    - {det['class_name']}: {det['score']:.2f}")
    
    cap.release()
