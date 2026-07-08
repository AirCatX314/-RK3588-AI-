"""
目标追踪器 - 卡尔曼滤波 (不依赖scipy)
"""

import numpy as np


class KalmanFilter:
    """卡尔曼滤波器 for 2D目标追踪"""
    
    def __init__(self):
        # 状态: [x, y, w, h, vx, vy, vw, vh] = 8维
        # 测量: [x, y, w, h] = 4维
        self.dt = 1.0  # 时间步长
        
        # 状态转移矩阵 F
        self.F = np.array([
            [1, 0, 0, 0, self.dt, 0,     0,     0],
            [0, 1, 0, 0, 0,     self.dt, 0,     0],
            [0, 0, 1, 0, 0,     0,     self.dt, 0],
            [0, 0, 0, 1, 0,     0,     0,     self.dt],
            [0, 0, 0, 0, 1,     0,     0,     0],
            [0, 0, 0, 0, 0,     1,     0,     0],
            [0, 0, 0, 0, 0,     0,     1,     0],
            [0, 0, 0, 0, 0,     0,     0,     1]
        ], dtype=np.float64)
        
        # 测量矩阵 H
        self.H = np.array([
            [1, 0, 0, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0, 0],
            [0, 0, 0, 1, 0, 0, 0, 0]
        ], dtype=np.float64)
        
        # 过程噪声协方差 Q
        self.Q = np.eye(8, dtype=np.float64) * 1e-3
        
        # 测量噪声协方差 R
        self.R = np.eye(4, dtype=np.float64) * 1e-1
        
        # 后验估计 x, 协方差 P
        self.x = None
        self.P = None
    
    def init(self, bbox):
        """初始化状态 [x, y, w, h, 0, 0, 0, 0]"""
        self.x = np.array([
            bbox[0], bbox[1], bbox[2], bbox[3],  # 位置
            0, 0, 0, 0                           # 速度
        ], dtype=np.float64).reshape(-1, 1)
        self.P = np.eye(8, dtype=np.float64) * 10.0
    
    def predict(self):
        """预测步骤"""
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.x[:4].flatten()  # 返回 [x, y, w, h]
    
    def update(self, z):
        """更新步骤
        
        Args:
            z: 测量值 [x, y, w, h]
        """
        z = np.array(z, dtype=np.float64).reshape(-1, 1)
        
        # 预测
        x_pred = self.F @ self.x
        P_pred = self.F @ self.P @ self.F.T + self.Q
        
        # 卡尔曼增益
        S = self.H @ P_pred @ self.H.T + self.R
        K = P_pred @ self.H.T @ np.linalg.inv(S)
        
        # 更新
        y = z - self.H @ x_pred  # 测量残差
        self.x = x_pred + K @ y
        self.P = (np.eye(8, dtype=np.float64) - K @ self.H) @ P_pred
        
        return self.x[:4].flatten()


class Tracker:
    """目标追踪器 - 管理和更新多个目标"""
    
    def __init__(self, max_age=30, min_hits=3, iou_threshold=0.3):
        """
        Args:
            max_age: 最大未匹配帧数
            min_hits: 最小命中次数才确认目标
            iou_threshold: IOU匹配阈值
        """
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        
        self.tracks = {}      # {track_id: {'kf': KalmanFilter, 'hits': int, 'age': int, 'bbox': list}}
        self.next_id = 1
    
    def update(self, detections):
        """更新追踪器"""
        # 步骤1: 预测所有已有轨迹
        for track_id in list(self.tracks.keys()):
            self.tracks[track_id]['bbox'] = self.tracks[track_id]['kf'].predict()
            self.tracks[track_id]['age'] += 1
        
        # 步骤2: 匹配检测和轨迹
        matched, unmatched_dets, unmatched_tracks = self._match(detections)
        
        # 步骤3: 更新匹配的轨迹
        for det_idx, track_id in matched:
            det = detections[det_idx]
            self.tracks[track_id]['kf'].update(det['bbox'])
            self.tracks[track_id]['bbox'] = det['bbox']
            self.tracks[track_id]['hits'] += 1
            self.tracks[track_id]['age'] = 0
            self.tracks[track_id]['class_name'] = det['class_name']
            self.tracks[track_id]['class_id'] = det['class_id']
        
        # 步骤4: 创建新轨迹
        for det_idx in unmatched_dets:
            det = detections[det_idx]
            kf = KalmanFilter()
            kf.init(det['bbox'])
            
            self.tracks[self.next_id] = {
                'kf': kf,
                'bbox': det['bbox'],
                'hits': 1,
                'age': 0,
                'class_name': det['class_name'],
                'class_id': det['class_id']
            }
            self.next_id += 1
        
        # 步骤5: 删除丢失的轨迹
        for track_id in unmatched_tracks:
            if self.tracks[track_id]['hits'] >= self.min_hits:
                pass
            else:
                del self.tracks[track_id]
        
        return self._get_active_tracks()
    
    def _match(self, detections):
        """匈牙利算法匹配（简化版：贪心匹配）"""
        if len(self.tracks) == 0:
            return [], list(range(len(detections))), []
        
        if len(detections) == 0:
            return [], [], list(self.tracks.keys())
        
        # 计算IOU矩阵
        iou_matrix = np.zeros((len(detections), len(self.tracks)))
        track_ids = list(self.tracks.keys())
        
        for d, det in enumerate(detections):
            for t, track_id in enumerate(track_ids):
                iou_matrix[d, t] = self._iou(det['bbox'], self.tracks[track_id]['bbox'])
        
        # 贪心匹配
        matched = []
        used_dets = set()
        used_tracks = set()
        
        for _ in range(min(len(detections), len(self.tracks))):
            max_iou = self.iou_threshold
            max_pos = None
            
            for d in range(len(detections)):
                if d in used_dets:
                    continue
                for t in range(len(track_ids)):
                    if t in used_tracks:
                        continue
                    if iou_matrix[d, t] > max_iou:
                        max_iou = iou_matrix[d, t]
                        max_pos = (d, t)
            
            if max_pos is None:
                break
            
            d, t = max_pos
            matched.append((d, track_ids[t]))
            used_dets.add(d)
            used_tracks.add(t)
        
        unmatched_dets = [d for d in range(len(detections)) if d not in used_dets]
        unmatched_tracks = [track_ids[t] for t in range(len(track_ids)) if t not in used_tracks]
        
        return matched, unmatched_dets, unmatched_tracks
    
    def _iou(self, bbox1, bbox2):
        """计算IOU"""
        x1 = max(bbox1[0], bbox2[0])
        y1 = max(bbox1[1], bbox2[1])
        x2 = min(bbox1[2], bbox2[2])
        y2 = min(bbox1[3], bbox2[3])
        
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        
        area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
        area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
        union = area1 + area2 - inter
        
        return inter / union if union > 0 else 0
    
    def _get_active_tracks(self):
        """获取活跃轨迹"""
        results = []
        for track_id, track in self.tracks.items():
            if track['hits'] >= self.min_hits:
                results.append({
                    'track_id': track_id,
                    'bbox': track['bbox'],
                    'class_name': track.get('class_name', 'unknown'),
                    'class_id': track.get('class_id', -1),
                    'confirmed': track['hits'] >= self.min_hits
                })
        return results
    
    def reset(self):
        """重置追踪器"""
        self.tracks = {}
        self.next_id = 1
