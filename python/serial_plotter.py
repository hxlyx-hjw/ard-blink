#!/usr/bin/env python3
"""
Arduino 8 路光电 + IMU660RB 串口实时绘图装置

读取 UNO 串口输出（115200），把每个时刻的数据解析并滚动绘图：
  1) 8 通道光电掩码热图（黑 = 压线）
  2) 相对位置曲线（-1.0 .. +1.0）
  3) 加速度 ax/ay/az（g）
  4) 角速度 gx/gy/gz（dps）

用法：
  python serial_plotter.py --port COM5
  python serial_plotter.py --list          # 列出可用串口
  python serial_plotter.py --demo          # 无硬件时用模拟数据演示
"""

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
from collections import deque

import numpy as np

import serial
from serial.tools import list_ports

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

import sensor_lib as lib


class DataBuffer:
    """定长滚动缓存，所有曲线共用同一时间轴。"""

    def __init__(self, window: int):
        self.window = window
        self.mask_grid = np.zeros((window, 8), dtype=np.uint8)
        self.on = np.full(window, np.nan)
        self.pos = np.full(window, np.nan)
        self.ax = np.full(window, np.nan)
        self.ay = np.full(window, np.nan)
        self.az = np.full(window, np.nan)
        self.gx = np.full(window, np.nan)
        self.gy = np.full(window, np.nan)
        self.gz = np.full(window, np.nan)
        self.last = None

    def append(self, sample):
        row = [(sample["mask"] >> k) & 1 for k in range(8)]
        self.mask_grid = np.roll(self.mask_grid, -1, axis=0)
        self.mask_grid[-1] = row

        self.on = np.roll(self.on, -1)
        self.on[-1] = sample["on"]
        self.pos = np.roll(self.pos, -1)
        self.pos[-1] = sample["pos"] if sample["pos"] is not None else np.nan

        imu = sample["imu"] or (np.nan,) * 6
        for arr, val in zip(
            (self.ax, self.ay, self.az, self.gx, self.gy, self.gz), imu
        ):
            arr[:] = np.roll(arr, -1)
            arr[-1] = val

        self.last = sample


class Plotter:
    """负责 matplotlib 界面与每帧刷新。"""

    def __init__(self, sample_queue, window: int, title: str,
                 accel_range: float = 2.0, gyro_range: float = 250.0):
        self.q = sample_queue
        self.buf = DataBuffer(window)
        self.received = 0
        self.recorder = None
        self.accel_range = accel_range
        self.gyro_range = gyro_range

        self.fig, axes = plt.subplots(
            4,
            1,
            figsize=(11, 9),
            sharex=True,
            gridspec_kw={"height_ratios": [2, 1, 1, 1]},
        )
        if self.fig.canvas.manager is not None:
            self.fig.canvas.manager.set_window_title("Sensor Live Plot")
        self.fig.suptitle(title)
        ax0, ax1, ax2, ax3 = axes

        # 面板 1：8 通道即时条形指示（blit 友好，比热图快得多）
        self.bar_lines = [
            ax0.plot([], [], color="black", lw=8, solid_capstyle="butt")[0]
            for _ in range(8)
        ]
        ax0.set_xlim(0, 1)
        ax0.set_ylabel("channel")
        ax0.set_yticks(range(8))
        ax0.set_ylim(-0.5, 7.5)
        ax0.set_title("line mask (instant)")
        self.status_text = ax0.text(
            0.02, 0.98, "", transform=ax0.transAxes, va="top", ha="left", fontsize=9
        )

        # 面板 2：相对位置
        (self.pos_line,) = ax1.plot([], [], lw=1.5)
        ax1.axhline(0, color="gray", lw=0.8)
        ax1.set_ylabel("position")
        ax1.set_ylim(-1.2, 1.2)

        # 面板 3：加速度
        self.a_lines = [
            ax2.plot([], [], lw=1.0, label=name)[0]
            for name in ("ax", "ay", "az")
        ]
        ax2.set_ylabel("accel (g)")
        ax2.set_ylim(-self.accel_range, self.accel_range)
        ax2.legend(loc="upper right", ncol=3, fontsize=8)

        # 面板 4：角速度
        self.g_lines = [
            ax3.plot([], [], lw=1.0, label=name)[0]
            for name in ("gx", "gy", "gz")
        ]
        ax3.set_ylabel("gyro (dps)")
        ax3.set_ylim(-self.gyro_range, self.gyro_range)
        ax3.set_xlabel("sample (rolling window)")
        ax3.legend(loc="upper right", ncol=3, fontsize=8)

        self.fig.tight_layout()

    def _drain(self):
        while True:
            try:
                sample = self.q.get_nowait()
            except queue.Empty:
                break
            self.buf.append(sample)
            self.received += 1
            if self.recorder is not None:
                self.recorder.add(sample)

    def _update(self, frame):
        self._drain()
        x = np.arange(self.buf.window)

        mask = self.buf.mask_grid[-1]
        for k, line in enumerate(self.bar_lines):
            if mask[k]:
                line.set_data([0.05, 0.95], [k, k])
            else:
                line.set_data([], [])

        self.pos_line.set_data(x, self.buf.pos)

        for line, data in zip(
            self.a_lines, (self.buf.ax, self.buf.ay, self.buf.az)
        ):
            line.set_data(x, data)

        for line, data in zip(
            self.g_lines, (self.buf.gx, self.buf.gy, self.buf.gz)
        ):
            line.set_data(x, data)

        if self.buf.last:
            s = self.buf.last
            pos = "LOST" if s["pos"] is None else f"{s['pos']:+.3f}"
            self.status_text.set_text(
                f"mask={format(s['mask'], '08b')}  on={s['on']}  "
                f"pos={pos}  n={self.received}"
            )

        return (
            self.pos_line,
            *self.a_lines,
            *self.g_lines,
            *self.bar_lines,
            self.status_text,
        )


class SerialReader(threading.Thread):
    """后台线程：读串口 -> 按行解析 -> 放入队列。"""

    def __init__(self, ser, sample_queue, stop_event):
        super().__init__(daemon=True)
        self.ser = ser
        self.q = sample_queue
        self.stop = stop_event

    def run(self):
        buf = b""
        while not self.stop.is_set():
            try:
                n = self.ser.in_waiting
                if n:
                    buf += self.ser.read(n)
                else:
                    time.sleep(0.001)
                    continue
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    sample = lib.parse_line(line.decode("utf-8", errors="ignore"))
                    if sample:
                        self.q.put(sample)
            except serial.SerialException:
                time.sleep(0.05)


def _demo_feeder(sample_queue, stop_event):
    """无硬件演示：生成一条左右摆动的压线 + 轻微噪声的 IMU。"""
    t = 0
    while not stop_event.is_set():
        center = 3.5 + 3.0 * np.sin(t / 25.0)
        mask = 0
        for k in range(8):
            if abs(k - center) < 1.0:
                mask |= 1 << k
        on = bin(mask).count("1")
        pos = None if on == 0 else (sum(k for k in range(8) if mask >> k & 1) / on - 3.5) / 3.5
        sample = {
            "mask": mask,
            "on": on,
            "pos": pos,
            "imu": (
                0.02 * np.sin(t / 20.0),
                0.02 * np.cos(t / 23.0),
                1.0 + 0.01 * np.sin(t / 30.0),
                8.0 * np.sin(t / 15.0),
                6.0 * np.cos(t / 17.0),
                2.0 * np.sin(t / 11.0),
            ),
        }
        sample_queue.put(sample)
        t += 1
        time.sleep(0.02)


def _list_ports():
    ports = list(list_ports.comports())
    if not ports:
        print("没有检测到可用串口。")
        return
    for p in ports:
        print(f"{p.device:12s} {p.description}")


def _choose_port(want):
    ports = list(list_ports.comports())
    if want:
        return want
    if not ports:
        print("错误：没有可用串口。用 --list 查看，或用 --port 指定。")
        sys.exit(1)
    if len(ports) == 1:
        return ports[0].device
    print("检测到多个串口，已选择第一个：")
    for p in ports:
        print(f"  {p.device:12s} {p.description}")
    return ports[0].device


def main():
    ap = argparse.ArgumentParser(description="Arduino 传感器串口实时绘图")
    ap.add_argument("--port", default=None, help="串口号，如 COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--window", type=int, default=300, help="滚动窗口样本数")
    ap.add_argument("--interval", type=int, default=15, help="刷新间隔(ms)")
    ap.add_argument("--accel-range", type=float, default=2.0, help="加速度纵轴 ±范围")
    ap.add_argument("--gyro-range", type=float, default=250.0, help="角速度纵轴 ±范围")
    ap.add_argument("--list", action="store_true", help="列出串口后退出")
    ap.add_argument("--demo", action="store_true", help="用模拟数据演示")
    args = ap.parse_args()

    if args.list:
        _list_ports()
        return

    q = queue.Queue()
    stop = threading.Event()
    ser = None

    if args.demo:
        title = "DEMO (simulated data)"
        feeder = threading.Thread(
            target=_demo_feeder, args=(q, stop), daemon=True
        )
        feeder.start()
    else:
        port = _choose_port(args.port)
        print(f"打开串口 {port} @ {args.baud} ...")
        try:
            ser = serial.Serial(port, args.baud, timeout=0.0)
        except serial.SerialException as e:
            print(f"打开串口失败：{e}")
            _list_ports()
            sys.exit(1)
        title = f"{port} @ {args.baud}"
        reader = SerialReader(ser, q, stop)
        reader.start()

    recorder = lib.Recorder(lib.new_recording_path("demo" if args.demo else "rec"))
    plotter = Plotter(
        q,
        args.window,
        title,
        accel_range=args.accel_range,
        gyro_range=args.gyro_range,
    )
    plotter.recorder = recorder

    def _on_close(_event):
        stop.set()
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    plotter.fig.canvas.mpl_connect("close_event", _on_close)
    ani = FuncAnimation(
        plotter.fig, plotter._update, interval=args.interval, blit=True, cache_frame_data=False
    )
    plt.show()
    stop.set()

    recorder.close()
    print(f"已保存记录：{recorder.path}")
    if recorder.count:
        m = lib.compute_metrics(lib.load_recording(recorder.path))
        print("指标：", json.dumps(m, ensure_ascii=False, indent=2))
        png = lib.plot_recording(
            recorder.path,
            out_png=recorder.path + ".png",
            show=True,
            title=f"完整记录 {os.path.basename(recorder.path)}",
        )
        print(f"完整图已保存：{png}")

        # 录完后询问是否录入 skill 库
        ans = input("是否把本次记录存入 skill 库？[y/N] ").strip().lower()
        if ans in ("y", "yes"):
            name = input("技能名称（留空用文件名）：").strip()
            skill = name or os.path.basename(recorder.path)[:-4]
            try:
                lib.save_skill(recorder.path, skill)
                print(f"已存入 skill 库：{skill}")
            except Exception as e:
                print(f"保存失败：{e}")
    else:
        print("本次没有采集到数据，未出图。")


if __name__ == "__main__":
    main()
