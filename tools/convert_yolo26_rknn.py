#!/usr/bin/env python3
"""
YOLO26 转 RKNN 转换脚本
支持 YOLO26 模型的正确导出
"""

import os
import sys

# 配置
MODEL_PATH = "/home/elf/Desktop/yolo26m.pt"  # YOLO26 模型路径
OUTPUT_DIR = "/home/elf/labsafe/yolo26m_rknn_model"  # 输出目录
TARGET_PLATFORM = "rk3588"  # 目标平台

def convert():
    from ultralytics import YOLO
    
    # 检查模型文件
    if not os.path.exists(MODEL_PATH):
        print(f"❌ 错误：模型文件未找到: {MODEL_PATH}")
        return False
    
    print(f"📦 加载模型: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    
    # 获取模型信息
    print(f"📋 模型信息:")
    print(f"   类型: {model.task}")
    print(f"   框架: ultralytics")
    
    # 导出为 RKNN 格式
    print(f"\n🔄 开始转换为 RKNN ({TARGET_PLATFORM})...")
    
    try:
        # 使用动态输入 shape 导出（更灵活）
        exported_path = model.export(
            format='rknn',
            imgsz=640,
            dynamic=True,  # 动态输入
            verbose=True
        )
        print(f"✅ 导出成功: {exported_path}")
        
    except Exception as e:
        print(f"❌ 导出失败: {e}")
        
        # 尝试使用静态输入导出
        print("\n🔄 尝试静态输入导出...")
        try:
            exported_path = model.export(
                format='rknn',
                imgsz=640,
                dynamic=False,
                batch=1,
                verbose=True
            )
            print(f"✅ 导出成功: {exported_path}")
        except Exception as e2:
            print(f"❌ 静态导出也失败: {e2}")
            return False
    
    # 移动到目标目录
    base_name = os.path.basename(MODEL_PATH).split('.')[0]
    exported_file = f"{base_name}-{TARGET_PLATFORM}.rknn"
    
    if os.path.exists(exported_file):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        dest_path = os.path.join(OUTPUT_DIR, exported_file)
        
        # 复制文件
        import shutil
        shutil.copy(exported_file, dest_path)
        print(f"\n📁 模型已复制到: {dest_path}")
        
        # 清理临时文件
        if os.path.exists(exported_file):
            os.remove(exported_file)
        
        # 测试加载
        print("\n🧪 测试模型加载...")
        test_rknn_load(dest_path)
        
        return True
    else:
        print(f"❌ 导出文件未找到: {exported_file}")
        return False


def test_rknn_load(model_path):
    """测试 RKNN 模型加载"""
    try:
        from rknnlite.api import RKNNLite
        import numpy as np
        
        rknn = RKNNLite()
        rknn.load_rknn(model_path)
        rknn.init_runtime()
        
        # 测试推理
        input_tensor = np.random.randn(1, 3, 640, 640).astype(np.float32)
        outputs = rknn.inference([input_tensor])
        
        print(f"   ✅ 加载成功!")
        print(f"   📊 输出 shape: {outputs[0].shape}")
        
        # 检查输出格式
        if outputs[0].shape == (1, 300, 6):
            print(f"   📋 格式: YOLO26 端到端格式 (1, 300, 6) ✅")
        elif outputs[0].shape == (1, 84, 8400):
            print(f"   📋 格式: YOLOv8 格式 (1, 84, 8400)")
        else:
            print(f"   ⚠️  未知格式")
        
        rknn.release()
        
    except Exception as e:
        print(f"   ❌ 测试失败: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("YOLO26 -> RKNN 转换工具")
    print("=" * 50)
    
    success = convert()
    
    if success:
        print("\n🎉 转换完成!")
    else:
        print("\n💥 转换失败!")
        sys.exit(1)
