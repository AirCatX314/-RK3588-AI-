# 火灾检测模型下载说明

## 下载地址

### 方案1: GitHub (推荐)
```bash
# 方式1: 使用Roboflow预训练模型
curl -L "https://huggingface.co/condyl/Fire-Detection-YOLOv8/resolve/main/best.pt" -o fire.pt

# 方式2: 其他开源项目
# https://github.com/AvlabsLab/Fire-Detection
# https://github.com/AmanazK/Fire-Detection-YOLO
```

### 方案2: HuggingFace
搜索 "fire detection yolo" 下载:
- https://huggingface.co/condyl/Fire-Detection-YOLOv8
- https://huggingface.co/Arman12/Fire-Detection-YOLOV8

### 方案3: 自行训练
如果以上都没有，可以用公开数据集训练:
- https://www.kaggle.com/datasets/atulanandjha/fire-dataset-2

## 放置位置
```
/home/elf/labsafe/models/fire.pt
```

## 验证模型
```bash
python3 -c "from ultralytics import YOLO; m = YOLO('/home/elf/labsafe/models/fire.pt'); print('OK')"
```
