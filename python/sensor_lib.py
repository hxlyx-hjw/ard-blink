#!/usr/bin/env python3
"""传感器数据核心库：解析、录制、指标、绘图、标准数据比对。

被 serial_plotter.py（实时 + 结束后完整出图）和 agent.py（DeepSeek 助手）
共同调用，保证两套程序对同一份数据口径一致。
"""

import csv
import glob
import json
import os
import re
import sys
import time

import numpy as np
import matplotlib.pyplot as plt


LINE_RE = re.compile(
    r"mask=(?P<mask>[01]{8})\s+on=(?P<on>\d+)\s+"
    r"pos=(?P<pos>LOST|-?\d+(?:\.\d+)?)"
    r"(?:\s*\|\s*imu\s+"
    r"(?P<ax>-?\d+(?:\.\d+)?)\s+(?P<ay>-?\d+(?:\.\d+)?)\s+"
    r"(?P<az>-?\d+(?:\.\d+)?)\s+(?P<gx>-?\d+(?:\.\d+)?)\s+"
    r"(?P<gy>-?\d+(?:\.\d+)?)\s+(?P<gz>-?\d+(?:\.\d+)?))?"
)

RECORDINGS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "recordings"
)
STANDARD_FILE = os.path.join(RECORDINGS_DIR, "_standard.json")

CSV_HEADER = ["t_ms", "mask", "on", "pos", "ax", "ay", "az", "gx", "gy", "gz"]


def parse_line(line: str):
    """把一行串口输出解析成字典；无法识别返回 None。"""
    m = LINE_RE.search(line)
    if not m:
        return None

    pos_str = m.group("pos")
    pos = None if pos_str == "LOST" else float(pos_str)

    imu = None
    if m.group("ax") is not None:
        imu = (
            float(m.group("ax")),
            float(m.group("ay")),
            float(m.group("az")),
            float(m.group("gx")),
            float(m.group("gy")),
            float(m.group("gz")),
        )

    return {"mask": int(m.group("mask"), 2), "on": int(m.group("on")),
            "pos": pos, "imu": imu}


def ensure_dirs():
    os.makedirs(RECORDINGS_DIR, exist_ok=True)


def new_recording_path(prefix="rec"):
    ensure_dirs()
    return os.path.join(
        RECORDINGS_DIR, f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    )


class Recorder:
    """边收边写 CSV 的流式记录器。"""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "w", newline="", encoding="utf-8")
        self.w = csv.writer(self.f)
        self.w.writerow(CSV_HEADER)
        self.t0 = time.monotonic()
        self.count = 0

    def add(self, sample):
        t_ms = int((time.monotonic() - self.t0) * 1000)
        imu = sample.get("imu") or (None,) * 6
        row = [
            t_ms,
            sample["mask"],
            sample["on"],
            "" if sample["pos"] is None else f"{sample['pos']:.4f}",
        ]
        row += ["" if v is None else f"{v:.4f}" for v in imu]
        self.w.writerow(row)
        self.count += 1

    def close(self):
        self.f.close()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def load_recording(path):
    ts, mask, on, pos = [], [], [], []
    ax, ay, az, gx, gy, gz = [], [], [], [], [], []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts.append(_num(row["t_ms"]) / 1000.0)
            mask.append(int(row["mask"]))
            on.append(int(row["on"]))
            pos.append(_num(row["pos"]))
            ax.append(_num(row["ax"]))
            ay.append(_num(row["ay"]))
            az.append(_num(row["az"]))
            gx.append(_num(row["gx"]))
            gy.append(_num(row["gy"]))
            gz.append(_num(row["gz"]))
    return {
        "t": np.asarray(ts), "mask": np.asarray(mask, dtype=int),
        "on": np.asarray(on), "pos": np.asarray(pos),
        "ax": np.asarray(ax), "ay": np.asarray(ay), "az": np.asarray(az),
        "gx": np.asarray(gx), "gy": np.asarray(gy), "gz": np.asarray(gz),
    }


def _mean(a):
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    return float(a.mean()) if a.size else np.nan


def _std(a):
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    return float(a.std()) if a.size else np.nan


def _clean(m):
    for k, v in m.items():
        if isinstance(v, float) and not np.isfinite(v):
            m[k] = None
    return m


def compute_metrics(d):
    """从一次记录算出可比的统计指标。"""
    pos = d["pos"]
    fin = np.isfinite(pos)
    n = len(d["t"])
    dur = float(d["t"][-1] - d["t"][0]) if n > 1 else 0.0
    rate = n / dur if dur > 0 else np.nan
    return _clean({
        "samples": int(n),
        "duration_s": round(dur, 2),
        "rate_hz": round(rate, 1) if np.isfinite(rate) else None,
        "pos_mean": round(_mean(pos[fin]), 4),
        "pos_abs_mean": round(_mean(np.abs(pos[fin])), 4),
        "pos_std": round(_std(pos[fin]), 4),
        "off_line_ratio": round(float((~fin).mean()), 4),
        "on_mean": round(_mean(d["on"]), 2),
        "accel_z_mean": round(_mean(d["az"]), 4),
        "accel_z_std": round(_std(d["az"]), 4),
        "gyro_x_std": round(_std(d["gx"]), 4),
        "gyro_y_std": round(_std(d["gy"]), 4),
        "gyro_z_mean": round(_mean(d["gz"]), 4),
        "gyro_z_std": round(_std(d["gz"]), 4),
    })


def plot_recording(paths, out_png=None, show=False, title=None):
    """把完整记录画成四面板静态图，返回保存的 PNG 路径（若指定）。"""
    if isinstance(paths, str):
        paths = [paths]

    fig, axes = plt.subplots(
        4, 1, figsize=(12, 9), sharex=True,
        gridspec_kw={"height_ratios": [2, 1, 1, 1]},
    )
    ax0, ax1, ax2, ax3 = axes

    for path in paths:
        d = load_recording(path)
        if len(d["t"]) == 0:
            continue
        label = os.path.basename(path)

        grid = np.stack([[int(m >> k & 1) for k in range(8)] for m in d["mask"]])
        t0, t1 = float(d["t"][0]), float(d["t"][-1])
        if t1 - t0 < 1e-9:  # 极短/时间轴退化的保护
            t0, t1 = t0 - 0.5, t0 + 0.5
        ax0.imshow(
            grid.T, aspect="auto", cmap="gray_r", origin="lower",
            extent=[t0, t1, -0.5, 7.5], interpolation="nearest",
        )
        ax0.set_ylabel("channel")
        ax0.set_yticks(range(8))
        ax0.set_title("line mask")

        ax1.plot(d["t"], d["pos"], lw=1.0, label=label)
        ax1.axhline(0, color="gray", lw=0.8)
        ax1.set_ylabel("position")
        ax1.set_ylim(-1.2, 1.2)
        ax1.legend(loc="upper right", fontsize=8)

        for arr, lab in ((d["ax"], "ax"), (d["ay"], "ay"), (d["az"], "az")):
            ax2.plot(d["t"], arr, lw=1.0, label=lab)
        ax2.set_ylabel("accel (g)")
        ax2.legend(loc="upper right", ncol=3, fontsize=8)

        for arr, lab in ((d["gx"], "gx"), (d["gy"], "gy"), (d["gz"], "gz")):
            ax3.plot(d["t"], arr, lw=1.0, label=lab)
        ax3.set_ylabel("gyro (dps)")
        ax3.set_xlabel("time (s)")
        ax3.legend(loc="upper right", ncol=3, fontsize=8)

    if title:
        fig.suptitle(title)
    fig.tight_layout()

    if out_png:
        fig.savefig(out_png, dpi=100)
    if show:
        plt.show()
    else:
        plt.close(fig)
    return out_png


def _csv_files():
    ensure_dirs()
    return sorted(
        glob.glob(os.path.join(RECORDINGS_DIR, "*.csv")), key=os.path.getmtime
    )


def get_standard_name():
    if os.path.exists(STANDARD_FILE):
        with open(STANDARD_FILE, encoding="utf-8") as f:
            return json.load(f).get("standard")
    return None


def resolve(name):
    name = (name or "").strip()
    if name.lower() == "latest":
        files = _csv_files()
        if not files:
            raise FileNotFoundError("还没有任何记录")
        return files[-1]
    if not name.endswith(".csv"):
        name += ".csv"
    p = os.path.join(RECORDINGS_DIR, name)
    if not os.path.exists(p):
        raise FileNotFoundError(f"记录不存在：{name}")
    return p


def list_recordings():
    std = get_standard_name()
    out = []
    for p in _csv_files():
        name = os.path.basename(p)
        info = {"name": name, "is_standard": name == std}
        try:
            d = load_recording(p)
            m = compute_metrics(d)
            info["samples"] = m["samples"]
            info["duration_s"] = m["duration_s"]
        except Exception:
            info["error"] = True
        out.append(info)
    return out


def set_standard(name):
    p = resolve(name)
    std = os.path.basename(p)
    ensure_dirs()
    with open(STANDARD_FILE, "w", encoding="utf-8") as f:
        json.dump({"standard": std}, f, ensure_ascii=False)
    return std


def suggestions(m, std=None):
    """基于指标（可选与标准对比）给出确定性建议。"""
    s = []
    if m["off_line_ratio"] is not None and m["off_line_ratio"] > 0.05:
        s.append(f"丢线时间占比 {m['off_line_ratio'] * 100:.1f}% 偏高，检查探头高度、校准和线宽")
    if m["pos_abs_mean"] is not None and m["pos_abs_mean"] > 0.35:
        s.append(f"平均位置偏离中心 {m['pos_abs_mean']:.3f}，检查传感器中线安装与机械对称")
    if m["pos_std"] is not None and m["pos_std"] > 0.35:
        s.append(f"位置抖动 std={m['pos_std']:.3f} 偏大，可尝试降低控制增益或提高机械刚性")
    if std is not None:
        if (m["gyro_z_std"] or 0) > (std["gyro_z_std"] or 0) * 1.5 + 1e-3:
            s.append("Z 轴角速度抖动明显高于标准，转向控制过猛或存在打滑")
        if (m["accel_z_std"] or 0) > (std["accel_z_std"] or 0) * 1.5 + 1e-3:
            s.append("Z 轴加速度波动高于标准，车身振动变大，检查固定与减震")
    if not s:
        s.append("各项指标正常，接近标准水平。")
    return s


def _similarity(m, std):
    keys = ["pos_abs_mean", "pos_std", "off_line_ratio", "accel_z_std", "gyro_z_std"]
    parts = []
    for k in keys:
        a, b = m[k], std[k]
        if a is not None and b is not None:
            parts.append(max(0.0, 1.0 - abs(a - b) / (abs(b) + 1e-4)))
    return round(100.0 * sum(parts) / len(parts), 1) if parts else None


def compare_with_standard(name):
    std_name = get_standard_name()
    if not std_name:
        return {"error": "尚未设置标准数据，请先用 set_standard 指定"}
    p = resolve(name)
    d = load_recording(p)
    dstd = load_recording(resolve(std_name))
    m = compute_metrics(d)
    mstd = compute_metrics(dstd)

    keys = ["pos_abs_mean", "pos_std", "off_line_ratio",
            "accel_z_std", "gyro_x_std", "gyro_y_std", "gyro_z_std"]
    deltas = {}
    for k in keys:
        a, b = m[k], mstd[k]
        deltas[k] = None if (a is None or b is None) else round(a - b, 4)

    return {
        "recording": os.path.basename(p),
        "standard": std_name,
        "metrics": m,
        "standard_metrics": mstd,
        "deltas": deltas,
        "similarity": _similarity(m, mstd),
        "suggestions": suggestions(m, mstd),
    }


def record_headless(port, baud=115200, duration=10.0, prefix="agent"):
    """无图形界面录制一段数据，返回 CSV 路径。"""
    import serial

    ser = serial.Serial(port, baud, timeout=0.0)
    path = new_recording_path(prefix)
    rec = Recorder(path)
    buf = b""
    start = time.monotonic()
    try:
        while time.monotonic() - start < duration:
            n = ser.in_waiting
            if n:
                buf += ser.read(n)
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = parse_line(line.decode("utf-8", errors="ignore"))
                if s:
                    rec.add(s)
            time.sleep(0.001)
    finally:
        rec.close()
        ser.close()
    return path


# ------------------- skill 库（学习模板库） -------------------

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")
SKILLS_INDEX = os.path.join(SKILLS_DIR, "_index.json")

# 参与模板聚合与比对的曲线通道
TEMPLATE_CHANNELS = ["pos", "az", "gx", "gy", "gz"]
TEMPLATE_POINTS = 200


def _skills_dir():
    os.makedirs(SKILLS_DIR, exist_ok=True)
    return SKILLS_DIR


def _skill_index():
    if os.path.exists(SKILLS_INDEX):
        with open(SKILLS_INDEX, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _write_skill_index(idx):
    _skills_dir()
    with open(SKILLS_INDEX, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def _safe_name(name):
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in str(name)).strip()
    return safe.replace(" ", "_") or "skill"


def save_skill(recording, skill_name, note=""):
    """把某次记录复制进 skill 库，作为学习模板。"""
    import shutil

    src = resolve(recording)
    name = _safe_name(skill_name)
    dst = os.path.join(_skills_dir(), name + ".csv")
    if os.path.exists(dst):
        raise FileExistsError(f"skill 已存在：{name}")
    shutil.copyfile(src, dst)

    idx = _skill_index()
    idx[name] = {
        "file": name + ".csv",
        "note": note or "",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": os.path.basename(src),
    }
    _write_skill_index(idx)
    return name


def list_skills():
    idx = _skill_index()
    out = []
    for name, meta in idx.items():
        p = os.path.join(_skills_dir(), meta.get("file", name + ".csv"))
        info = {
            "name": name,
            "note": meta.get("note", ""),
            "created": meta.get("created", ""),
        }
        if os.path.exists(p):
            try:
                m = compute_metrics(load_recording(p))
                info["samples"] = m["samples"]
                info["duration_s"] = m["duration_s"]
            except Exception:
                info["error"] = True
        else:
            info["missing"] = True
        out.append(info)
    return out


def remove_skill(name):
    idx = _skill_index()
    if name not in idx:
        raise FileNotFoundError(f"skill 不存在：{name}")
    p = os.path.join(_skills_dir(), idx[name].get("file", name + ".csv"))
    if os.path.exists(p):
        os.remove(p)
    del idx[name]
    _write_skill_index(idx)
    return name


def resolve_skill(name):
    idx = _skill_index()
    if name not in idx:
        raise FileNotFoundError(f"skill 不存在：{name}")
    return os.path.join(_skills_dir(), idx[name].get("file", name + ".csv"))


def _skill_csv_files():
    _skills_dir()
    idx = _skill_index()
    files = [
        os.path.join(_skills_dir(), m.get("file", n + ".csv")) for n, m in idx.items()
    ]
    return [f for f in files if os.path.exists(f)]


def build_template(skill_names=None):
    """把 skill 库（或指定 skills）的曲线统一到归一化时间轴，统计均值与标准差模板。"""
    if skill_names:
        paths = [resolve_skill(n) for n in skill_names]
    else:
        paths = _skill_csv_files()
    if not paths:
        raise ValueError("skill 库为空，请先录入技能")

    n = TEMPLATE_POINTS
    xs = np.linspace(0.0, 1.0, n)
    mats = {c: [] for c in TEMPLATE_CHANNELS}
    for p in paths:
        d = load_recording(p)
        if len(d["t"]) < 2:
            continue
        t = (d["t"] - d["t"][0]) / (d["t"][-1] - d["t"][0] + 1e-9)
        for c in TEMPLATE_CHANNELS:
            mats[c].append(np.interp(xs, t, np.asarray(d[c], float)))

    tpl = {"count": len(paths), "n_points": n}
    for c in TEMPLATE_CHANNELS:
        M = np.vstack(mats[c]) if mats[c] else np.full((0, n), np.nan)
        tpl[c + "_mean"] = np.nanmean(M, axis=0)
        tpl[c + "_std"] = np.nanstd(M, axis=0)
    return tpl


def compare_with_template(name):
    """把一次记录与 skill 库聚合模板比对，返回偏差与相似度。"""
    tpl = build_template()
    p = resolve(name)
    d = load_recording(p)
    if len(d["t"]) < 2:
        return {"error": "记录样本过少，无法比对"}

    n = tpl["n_points"]
    xs = np.linspace(0.0, 1.0, n)
    t = (d["t"] - d["t"][0]) / (d["t"][-1] - d["t"][0] + 1e-9)

    devs = {}
    for c in TEMPLATE_CHANNELS:
        arr = np.interp(xs, t, np.asarray(d[c], float))
        diff = arr - tpl[c + "_mean"]
        devs[c] = round(float(np.nanmean(np.abs(diff) / (tpl[c + "_std"] + 1e-4))), 3)

    sim = round(100.0 * float(np.exp(-np.mean(list(devs.values())))), 1)
    worst = max(devs, key=devs.get)
    sugg = []
    if devs[worst] > 1.0:
        sugg.append(
            f"与 skill 库模板差异最大的通道是 {worst}，平均偏离 {devs[worst]} 个标准差"
        )
    sugg += suggestions(compute_metrics(d))

    return {
        "recording": os.path.basename(p),
        "template_skills": tpl["count"],
        "deviations": devs,
        "similarity": sim,
        "suggestions": sugg,
    }


# ------------------- 文件读写与展示（供 agent 调用） -------------------

def _safe_path(path):
    base = os.path.abspath(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.abspath(path) if os.path.isabs(path) else os.path.abspath(os.path.join(base, path))
    if p != base and not p.startswith(base + os.sep):
        raise PermissionError("路径超出允许范围（仅限 python 目录内）")
    return p


def read_file(path, max_chars=4000):
    p = _safe_path(path)
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        txt = f.read()
    return txt[:max_chars] + ("...(截断)" if len(txt) > max_chars else "")


def write_file(path, content):
    p = _safe_path(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


def show_file(path):
    """用系统默认程序打开文件（PNG/CSV/文本等），直接展示给用户。"""
    p = _safe_path(path)
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    if os.name == "nt":
        os.startfile(p)
    else:
        import subprocess

        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, p])
    return p


def list_files(subdir=""):
    base = os.path.abspath(os.path.dirname(os.path.abspath(__file__)))
    root = _safe_path(os.path.join(base, subdir) if subdir else base)
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            out.append(os.path.relpath(os.path.join(dirpath, fn), base))
    return sorted(out)
