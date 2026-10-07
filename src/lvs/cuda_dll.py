"""CUDA 运行库预加载。

ctranslate2 对 cuBLAS 是运行时惰性加载（cublas_stub.cc 里 LoadLibrary），
而 cuBLAS 在 nvidia pip 包的 site-packages/nvidia/cublas/bin/，不在 Windows
标准 DLL 搜索路径，导致长音频 GPU 特征提取时报 "cublas64_12.dll is not found"。

解决方案：在 import ctranslate2 之前，用 ctypes 把 nvidia 包的 DLL 预加载进
进程。Windows 的 LoadLibrary 对已加载的同名 DLL 会直接返回已有句柄，所以
ctranslate2 之后的 LoadLibrary("cublas64_12.dll") 会命中，无需复制文件。

预加载的 DLL 常驻进程内存直到进程退出（由操作系统回收），这是期望行为：
转录本身就需要 cuBLAS，常驻反而让后续转录更快（省去重复 LoadLibrary）。
"""
from __future__ import annotations


def preload_cuda_dlls() -> None:
    """把 nvidia pip 包（cublas/cudnn 等）的 DLL 预加载进进程。

    必须在 import ctranslate2 之前调用。幂等：已加载的 DLL 会直接命中。
    """
    try:
        import ctypes
        import glob
        import os
        import sysconfig

        site = sysconfig.get_paths()["purelib"]
        for dll in sorted(glob.glob(os.path.join(site, "nvidia", "*", "bin", "*.dll"))):
            try:
                ctypes.CDLL(dll)
            except OSError:
                # 个别 DLL 可能缺依赖或已加载，忽略
                pass
    except Exception:  # noqa: BLE001
        pass
