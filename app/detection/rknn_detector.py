"""
RKNN YOLO11 detector for the LabSafe 7-class safety model.

The deployed model output is [1, 11, 5376]:
4 box channels + 7 class channels, with 512x512 fixed input.
"""

import threading

import cv2
import numpy as np
from detector import BaseDetector


CLASS_NAMES = [
    "lab_coat",
    "face_shield",
    "gloves",
    "goggles",
    "mask",
    "flame",
    "smoke",
]


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _letterbox(image, new_shape=(512, 512), color=(114, 114, 114)):
    src_h, src_w = image.shape[:2]
    dst_h, dst_w = new_shape
    ratio = min(dst_w / src_w, dst_h / src_h)
    resized_w = int(round(src_w * ratio))
    resized_h = int(round(src_h * ratio))
    pad_w = dst_w - resized_w
    pad_h = dst_h - resized_h
    pad_left = int(round(pad_w / 2 - 0.1))
    pad_right = int(round(pad_w / 2 + 0.1))
    pad_top = int(round(pad_h / 2 - 0.1))
    pad_bottom = int(round(pad_h / 2 + 0.1))

    if (src_w, src_h) != (resized_w, resized_h):
        image = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    image = cv2.copyMakeBorder(
        image,
        pad_top,
        pad_bottom,
        pad_left,
        pad_right,
        cv2.BORDER_CONSTANT,
        value=color,
    )
    return image, ratio, (float(pad_left), float(pad_top))


def _xywh_to_xyxy(boxes):
    out = np.empty_like(boxes, dtype=np.float32)
    out[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    out[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    out[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    out[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return out


def _restore_boxes(boxes, ratio, pad, original_shape):
    boxes = boxes.astype(np.float32).copy()
    boxes[:, [0, 2]] -= pad[0]
    boxes[:, [1, 3]] -= pad[1]
    boxes[:, :4] /= ratio
    h, w = original_shape
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, w - 1)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, h - 1)
    return boxes


class RKNNDetector(BaseDetector):
    """RKNN Lite YOLO11 detector running on RK3588 NPU."""

    def __init__(self):
        self.rknn = None
        self.model_path = None
        self.confidence = 0.25
        self.iou_threshold = 0.45
        self.classes = None
        self.input_size = 512
        self.names = {i: name for i, name in enumerate(CLASS_NAMES)}
        self._lock = threading.Lock()
        self.output_shapes = []

    def load_model(self, model_path, **kwargs):
        from rknnlite.api import RKNNLite

        self.model_path = model_path
        self.confidence = float(kwargs.get("confidence", 0.25))
        self.iou_threshold = float(kwargs.get("iou_threshold", 0.45))
        self.input_size = int(kwargs.get("input_size", 512))
        classes = kwargs.get("classes", None)
        self.classes = set(classes) if classes is not None else None

        self.rknn = RKNNLite()
        ret = self.rknn.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f"Failed to load RKNN model: {model_path}, ret={ret}")

        core_mask = getattr(RKNNLite, "NPU_CORE_0_1_2", None)
        if core_mask is None:
            ret = self.rknn.init_runtime()
        else:
            try:
                ret = self.rknn.init_runtime(core_mask=core_mask)
            except TypeError:
                ret = self.rknn.init_runtime()
        if ret != 0:
            raise RuntimeError(f"Failed to initialize RKNN runtime, ret={ret}")

        print(f"[RKNNDetector] loaded {model_path}")
        print(f"[RKNNDetector] classes={CLASS_NAMES}, input={self.input_size}, conf={self.confidence}")

    def detect(self, frame):
        if self.rknn is None:
            raise RuntimeError("RKNN model is not loaded")

        inp, ratio, pad = self._preprocess(frame)
        with self._lock:
            outputs = self.rknn.inference(inputs=[inp])
        if outputs is None:
            return []

        outputs = [np.asarray(out) for out in outputs]
        self.output_shapes = [list(out.shape) for out in outputs]
        return self._postprocess(frame, outputs, ratio, pad)

    def _preprocess(self, frame):
        img, ratio, pad = _letterbox(frame, (self.input_size, self.input_size))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.uint8)
        img = np.expand_dims(img, axis=0)
        return img, ratio, pad

    def _postprocess(self, frame, outputs, ratio, pad):
        num_classes = len(CLASS_NAMES)
        arr = outputs[0]
        if arr.ndim == 3 and arr.shape[0] == 1:
            arr = arr[0]

        expected = 4 + num_classes
        if arr.ndim != 2:
            raise RuntimeError(f"Unsupported RKNN output shape: {[list(o.shape) for o in outputs]}")
        if arr.shape[0] == expected:
            pred = arr.T
        elif arr.shape[1] == expected:
            pred = arr
        else:
            raise RuntimeError(f"Unsupported RKNN output shape: {[list(o.shape) for o in outputs]}")

        boxes = pred[:, :4].astype(np.float32)
        class_scores = pred[:, 4 : 4 + num_classes].astype(np.float32)
        if class_scores.size and (np.nanmin(class_scores) < -1e-3 or np.nanmax(class_scores) > 1.0 + 1e-3):
            class_scores = _sigmoid(class_scores)

        class_ids = np.argmax(class_scores, axis=1).astype(np.int32)
        scores = class_scores[np.arange(class_scores.shape[0]), class_ids]
        keep = scores >= self.confidence
        if self.classes is not None:
            keep &= np.array([int(c) in self.classes for c in class_ids], dtype=bool)
        if not np.any(keep):
            return []

        boxes = boxes[keep]
        scores = scores[keep].astype(np.float32)
        class_ids = class_ids[keep]

        if boxes.size and np.nanmax(np.abs(boxes)) <= 2.0:
            boxes *= float(self.input_size)

        boxes = _xywh_to_xyxy(boxes)
        boxes = _restore_boxes(boxes, ratio, pad, frame.shape[:2])

        valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
        boxes = boxes[valid]
        scores = scores[valid]
        class_ids = class_ids[valid]
        if boxes.shape[0] == 0:
            return []

        keep_idxs = self._nms(boxes, scores, class_ids)
        detections = []
        for idx in keep_idxs:
            cls_id = int(class_ids[idx])
            detections.append(
                {
                    "bbox": [float(v) for v in boxes[idx].tolist()],
                    "score": float(scores[idx]),
                    "class_id": cls_id,
                    "class_name": self.names.get(cls_id, f"class_{cls_id}"),
                }
            )
        return detections

    def _nms(self, boxes, scores, classes):
        keep = []
        for cls in np.unique(classes):
            idxs = np.where(classes == cls)[0]
            idxs = idxs[np.argsort(scores[idxs])[::-1]]
            while len(idxs) > 0:
                current = int(idxs[0])
                keep.append(current)
                if len(idxs) == 1:
                    break
                rest = idxs[1:]
                xx1 = np.maximum(boxes[current, 0], boxes[rest, 0])
                yy1 = np.maximum(boxes[current, 1], boxes[rest, 1])
                xx2 = np.minimum(boxes[current, 2], boxes[rest, 2])
                yy2 = np.minimum(boxes[current, 3], boxes[rest, 3])
                inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
                area_current = max(0.0, boxes[current, 2] - boxes[current, 0]) * max(
                    0.0, boxes[current, 3] - boxes[current, 1]
                )
                area_rest = np.maximum(0.0, boxes[rest, 2] - boxes[rest, 0]) * np.maximum(
                    0.0, boxes[rest, 3] - boxes[rest, 1]
                )
                iou = inter / (area_current + area_rest - inter + 1e-9)
                idxs = rest[iou <= self.iou_threshold]
        keep.sort(key=lambda i: float(scores[i]), reverse=True)
        return keep[:100]

    def release(self):
        if self.rknn is not None:
            self.rknn.release()
            self.rknn = None
            print("[RKNNDetector] released")


from detector import DetectorFactory

DetectorFactory._detectors["rknn"] = RKNNDetector
