#!/usr/bin/env python3
"""远端机器体检 —— 拿这个决定"哪个模型放哪台"。

只用 Python 标准库，**裸机/新装系统都能直接跑**，不需要先 pip install 什么。

    python3 probe_remote.py            # 人类可读报告
    python3 probe_remote.py --json     # 机器可读（贴给我看这个更省事）

报告包含：
  · CPU / 内存 / 磁盘 / 系统
  · GPU 型号 + 显存 + 驱动 + CUDA（多层兜底：nvidia-smi / wmic / rocm-smi / sysfs）
  · Python 版本、pip、已有的关键包
  · 到 PyPI / HF / 魔搭 / GitHub 的连通性与延迟（决定能不能下模型）
  · 一个小的 CPU 基准（和本机 i7-12850HX 的数字对比，判断要不要改用 CPU 档）
  · 结论行：这台能跑哪些 P2 组件（IndexTTS / CosyVoice2 / demucs / Kokoro / ASR / MT）
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# P2 各组件的最低要求（GB）：显存、内存、磁盘。依据 docs/12-可本地部署模型清单.md 的实测体积。
COMPONENTS = [
    # 名字,              显存GB, 内存GB, 磁盘GB, 说明
    ("SenseVoice-ASR",    0.0,   2.0,   0.25, "CPU 就能实时（P1 已跑通）"),
    ("Hy-MT2-1.8B",       0.0,   3.0,   1.40, "CPU 可跑，离线够用"),
    ("demucs-人声分离",    0.0,   3.0,   0.20, "CPU 离线批处理够用"),
    ("Kokoro-82M-TTS",    0.0,   1.0,   0.35, "CPU 实时，Apache 许可"),
    ("CosyVoice2-0.5B",   4.0,   6.0,   4.55, "音色克隆；CPU 上慢于实时"),
    ("IndexTTS-2.5",      6.0,   8.0,   5.15, "效果最好；非 Apache 许可"),
    ("GPT-SoVITS",        5.0,   8.0,   6.45, "少样本克隆"),
]

HOSTS = [
    ("PyPI", "pypi.org", 443),
    ("PyPI 清华镜像", "pypi.tuna.tsinghua.edu.cn", 443),
    ("HuggingFace", "huggingface.co", 443),
    ("HF 镜像", "hf-mirror.com", 443),
    ("魔搭 ModelScope", "modelscope.cn", 443),
    ("GitHub", "github.com", 443),
]


def run(cmd: list[str], timeout: float = 12.0) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           errors="replace")
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def cpu_info() -> dict:
    name = platform.processor() or ""
    cores = os.cpu_count() or 0
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    name = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass
    elif sys.platform == "win32":
        # wmic 在 Win11 上默认已移除，改读注册表（标准库，不依赖外部命令）
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:
            pass
    return {"name": name, "logical_cores": cores}


def mem_gb() -> dict:
    """返回总内存与**可用**内存（GB）。能不能装下模型看的是可用内存。"""
    total = avail = 0.0
    if sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                total = round(st.ullTotalPhys / 1073741824, 1)
                avail = round(st.ullAvailPhys / 1073741824, 1)
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal"):
                    total = round(int(line.split()[1]) / 1048576, 1)
                elif line.startswith("MemAvailable"):
                    avail = round(int(line.split()[1]) / 1048576, 1)
        except Exception:
            pass
    elif sys.platform == "darwin":
        out = run(["sysctl", "-n", "hw.memsize"]).strip()
        if out.isdigit():
            total = round(int(out) / 1073741824, 1)
        avail = total
    return {"total_gb": total, "available_gb": avail}


def disks() -> list[dict]:
    out = []
    roots = [Path("/")] if not sys.platform == "win32" else \
            [Path(f"{d}:\\") for d in "CDEF" if Path(f"{d}:\\").exists()]
    for r in roots:
        try:
            u = shutil.disk_usage(str(r))
            out.append({"path": str(r), "free_gb": round(u.free / 1073741824, 1),
                        "total_gb": round(u.total / 1073741824, 1)})
        except Exception:
            pass
    return out


def gpus() -> list[dict]:
    found: list[dict] = []
    out = run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
               "--format=csv,noheader,nounits"])
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[0]:
            found.append({"vendor": "nvidia", "name": parts[0],
                          "vram_gb": round(float(parts[1]) / 1024, 1),
                          "driver": parts[2]})
    if not found and sys.platform == "win32":
        out = run(["wmic", "path", "win32_VideoController", "get", "name,AdapterRAM"])
        for line in out.splitlines():
            line = line.strip()
            if line and "AdapterRAM" not in line and line[0].isdigit():
                cols = line.split()
                ram = int(cols[0]) if cols and cols[0].isdigit() else 0
                found.append({"vendor": "?", "name": " ".join(cols[1:])[:60],
                              "vram_gb": round(ram / 1073741824, 1), "driver": ""})
    if not found and shutil.which("rocm-smi"):
        found.append({"vendor": "amd", "name": run(["rocm-smi", "--showproductname"])[:80],
                      "vram_gb": 0.0, "driver": ""})
    return found


def packages() -> dict:
    wanted = ["torch", "torchaudio", "torchvision", "onnxruntime", "sherpa_onnx",
              "transformers", "soundfile", "librosa", "numpy", "edge_tts",
              "fastapi", "uvicorn", "httpx"]
    have = {}
    for m in wanted:
        try:
            mod = __import__(m)
            have[m] = getattr(mod, "__version__", "?")
        except Exception:
            pass
    return have


def probe_hosts() -> list[dict]:
    res = []
    for label, host, port in HOSTS:
        t0 = time.perf_counter()
        try:
            socket.create_connection((host, port), timeout=6).close()
            res.append({"label": label, "host": host, "ok": True,
                        "ms": round((time.perf_counter() - t0) * 1000, 1)})
        except Exception as e:
            res.append({"label": label, "host": host, "ok": False, "ms": None,
                        "err": type(e).__name__})
    return res


def cpu_bench() -> dict:
    """纯 Python 小基准：单核吞吐 + 多核加速比。

    只是一个**相对值**，用来在两台机器之间比（比如判断远端是不是比本机还慢）。
    不要当跑分看，也不要用它预测 ASR 的 RTF —— 那取决于模型和线程数。
    """
    import math
    import threading

    ITER = 400_000

    def work(n: int) -> None:
        s = 0.0
        for i in range(n):
            s += math.sqrt(i % 997) * math.sin(i % 89)

    t0 = time.perf_counter()
    work(ITER)
    single = time.perf_counter() - t0

    n_threads = min(os.cpu_count() or 1, 16)
    t0 = time.perf_counter()
    ths = [threading.Thread(target=work, args=(ITER,)) for _ in range(n_threads)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    multi = time.perf_counter() - t0

    return {
        "single_kops": round(ITER / single / 1000, 1),          # 单核：千次迭代/秒
        "threads": n_threads,
        "multi_kops_total": round(ITER * n_threads / multi / 1000, 1),
        "speedup": round(single * n_threads / multi, 2),         # 真实并行加速比
    }


def cuda_state() -> dict:
    """光看显存会骗人：显存够但 torch 是 CPU 版的话，装了也用不上 GPU。

    这台开发机就是活教材 —— RTX 4060 8GB 看着够跑 IndexTTS，实际 torch 是
    `2.12.0+cpu`、`cuda.is_available()==False`，模型只会用 CPU 跑到天荒地老。
    """
    st = {"torch": "", "torch_cuda": False, "usable_gpu": False}
    try:
        import torch

        st["torch"] = getattr(torch, "__version__", "")
        st["torch_cuda"] = bool(torch.cuda.is_available())
    except Exception:
        st["torch"] = "未装"
    return st


def verdict(spec: dict) -> list[str]:
    """逐组件判断能不能跑。

    显存类组件要**同时**满足：显存够 + torch 能用 CUDA。只看显存会给假结论。
    """
    vram = max([g.get("vram_gb", 0) for g in spec["gpus"]] or [0])
    ram = spec["memory"]["available_gb"] or spec["memory"]["total_gb"]
    disk = max([d["free_gb"] for d in spec["disks"]] or [0])
    cuda_ok = spec["cuda"]["torch_cuda"]
    lines = []
    for name, need_v, need_r, need_d, note in COMPONENTS:
        why = []
        if need_r > ram:
            why.append(f"可用内存需 {need_r}GB/有 {ram}GB")
        if need_d > disk:
            why.append(f"磁盘需 {need_d}GB/剩 {disk}GB")
        if need_v > 0:
            if need_v > vram:
                why.append(f"显存需 {need_v}GB/有 {vram}GB")
            elif not cuda_ok:
                why.append(f"显存够({vram}GB)但 torch 用不了 CUDA（当前 {spec['cuda']['torch']}），"
                           f"要换 CUDA 版才真能跑")
        mark = "✅ 能跑" if not why else "❌ 不行"
        if not why and need_v > 0:
            note = note + f"（{vram}GB 显存 + CUDA 可用）"
        lines.append(f"  {mark}  {name:16} {('；'.join(why)) if why else note}")
    return lines


def main() -> int:
    as_json = "--json" in sys.argv
    spec = {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "cpu": cpu_info(),
        "memory": mem_gb(),
        "disks": disks(),
        "gpus": gpus(),
        "python": sys.version.split()[0],
        "pip": (run([sys.executable, "-m", "pip", "--version"]) or "无").split()[1] if \
               shutil.which(sys.executable) else "无",
        "packages": packages(),
        "cuda": cuda_state(),
        "proxy_env": {k: v for k, v in os.environ.items()
                      if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")},
        "network": probe_hosts(),
        "benchmark": cpu_bench(),
    }
    spec["verdict"] = verdict(spec)

    if as_json:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
        return 0

    print("=" * 70)
    print(f"远端机器体检  {spec['hostname']}")
    print("=" * 70)
    print(f"系统        {spec['os']}")
    print(f"CPU         {spec['cpu']['name']}  逻辑核 {spec['cpu']['logical_cores']}")
    print(f"内存        总 {spec['memory']['total_gb']} GB / 可用 {spec['memory']['available_gb']} GB")
    for d in spec["disks"]:
        print(f"磁盘 {d['path']:6} 剩 {d['free_gb']} GB / 共 {d['total_gb']} GB")
    if spec["gpus"]:
        for g in spec["gpus"]:
            print(f"GPU         {g['name']}  显存 {g['vram_gb']} GB  驱动 {g.get('driver','')}")
    else:
        print("GPU         未检测到（nvidia-smi / wmic / rocm-smi 都没有）")
    print(f"Python      {spec['python']}    pip {spec['pip']}")
    c = spec["cuda"]
    print(f"CUDA 可用   {c['torch_cuda']}    torch {c['torch']}"
          f"{'   ← 显存类模型会被迫用 CPU' if spec['gpus'] and not c['torch_cuda'] else ''}")
    if spec["proxy_env"]:
        print(f"代理环境变量 {spec['proxy_env']}（下模型/装包慢或 DNS 失败时先清掉）")
    print(f"已装关键包  {', '.join(f'{k} {v}' for k, v in spec['packages'].items()) or '（无）'}")
    print("\n网络连通性（下模型能不能走通）:")
    for n in spec["network"]:
        print(f"  {'✅' if n['ok'] else '❌'} {n['label']:16} "
              f"{str(n['ms']) + ' ms' if n['ok'] else n.get('err','')}")
    b = spec["benchmark"]
    print(f"\nCPU 基准    单核 {b['single_kops']} 千次迭代/秒   "
          f"{b['threads']} 线程并行加速 {b['speedup']}×（总吞吐 {b['multi_kops_total']}）")
    print("            （相对值，只用来对比两台机器，不能拿来预测 ASR 的 RTF）")
    print("\nP2 组件适配结论:")
    for line in spec["verdict"]:
        print(line)
    print("\n把这一段（或 --json 输出）贴回来即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
