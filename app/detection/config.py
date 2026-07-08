# 目标检测模块配置文件
# 修改这里即可更改检测参数，无需修改代码

# ========== 检测器配置 ==========
# 检测器类型: "yolo" (GPU/CPU) 或 "rknn" (NPU 加速)
DETECTOR_TYPE = "rknn"

# RKNN 模型路径 (NPU 加速)
RKNN_MODEL_PATH = "/home/elf/labsafe/models/lab_safety_yolo11n_512_fp16.rknn"

# YOLO 模型选择: yolov8n.pt (最快), yolov8s.pt, yolov8m.pt
MODEL_NAME = "yolov8n.pt"

# 检测分辨率 - 越小越快 (640/416/320/256)
# 推荐: 320 用于实时视频流，640 用于高精度
DETECTION_SIZE = 512

# 置信度阈值 (0-1)
CONFIDENCE = 0.25  # 降低阈值以提高召回率

# 检测类别 (COCO数据集常见类别):
#   0 = person     人
#   1 = bicycle    自行车
#   2 = car        汽车
#   3 = motorcycle 摩托车
#   5 = bus        公交车
#   7 = truck      卡车
#  15 = cat       猫
#   16 = dog       狗
# None = 检测所有类别
CLASSES = None  # 检测全部 LabSafe 7 类

# ========== 追踪器配置 ==========
MAX_AGE = 30        # 目标丢失多少帧后删除
MIN_HITS = 3        # 连续追踪多少帧才确认
IOU_THRESHOLD = 0.3 # IOU匹配阈值

# ========== 可视化配置 ==========
SHOW_LABELS = True   # 显示类别名称
SHOW_IDS = True      # 显示追踪ID
SHOW_SCORES = False  # 显示置信度

# ========== 火灾检测模型 ==========
FIRE_MODEL_PATH = "/home/elf/labsafe/models/fire.pt"
FIRE_CONFIDENCE = 0.5

# ========== 多模型检测模式 ==========
ENABLE_MULTI_MODEL = True

# ========== 摄像头配置 ==========
CAMERA_ID = "living-room"
API_BASE = "http://localhost:5000"

# ========== 追踪器配置 (分别针对不同目标) ==========
PERSON_TRACKER_CONFIG = {
    "max_age": 30,
    "min_hits": 3,
    "iou_threshold": 0.3
}

FIRE_TRACKER_CONFIG = {
    "max_age": 10,
    "min_hits": 1,
    "iou_threshold": 0.3
}
