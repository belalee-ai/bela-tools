#!/usr/bin/env python3
"""
把购物车录屏抽成少量「关键帧」图片，供读图识别。

思路：先按固定帧率抽小图，再只保留和上一张保留帧差异足够大的帧
（滚动了大半屏、或者切换了页面），停留不动、慢慢滑的重复画面全部丢掉。

用法：
    python3 extract_frames.py <录屏.mp4> <输出目录> [--fps 2] [--width 720] [--diff 18]
依赖：ffmpeg；有 Pillow 时用像素差去重，没有时退回 ffmpeg 自带的 mpdecimate。
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"命令失败：{' '.join(cmd)}\n{r.stderr[-800:]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out_dir")
    ap.add_argument("--fps", type=float, default=2, help="先按多少帧/秒抽样（默认 2）")
    ap.add_argument("--width", type=int, default=720, help="输出图片宽度（默认 720，够看清价格）")
    ap.add_argument("--diff", type=float, default=18, help="去重阈值，越大留得越少（默认 18）")
    a = ap.parse_args()

    if not shutil.which("ffmpeg"):
        sys.exit("没有找到 ffmpeg：请改发截图，或先安装 ffmpeg")
    os.makedirs(a.out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="frames_")
    run(["ffmpeg", "-v", "error", "-i", a.video, "-vf", f"fps={a.fps},scale={a.width}:-2",
         "-q:v", "3", os.path.join(tmp, "f_%05d.jpg")])
    frames = sorted(glob.glob(os.path.join(tmp, "f_*.jpg")))
    if not frames:
        sys.exit("视频里没抽出画面")

    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        Image = None

    kept = []
    if Image:
        last = None
        for f in frames:
            g = Image.open(f).convert("L").resize((48, 104))
            if last is None or ImageStat.Stat(ImageChops.difference(g, last)).mean[0] > a.diff:
                kept.append(f)
                last = g
        if frames[-1] not in kept:  # 最后一屏通常是合计/结算栏，一定留
            kept.append(frames[-1])
    else:
        d2 = tempfile.mkdtemp(prefix="dec_")
        run(["ffmpeg", "-v", "error", "-i", a.video, "-vf",
             f"fps={a.fps},mpdecimate=hi=6400:lo=3200:frac=0.5,scale={a.width}:-2",
             "-vsync", "vfr", "-q:v", "3", os.path.join(d2, "f_%05d.jpg")])
        kept = sorted(glob.glob(os.path.join(d2, "f_*.jpg")))

    for i, f in enumerate(kept, 1):
        sec = (int(os.path.basename(f)[2:7]) - 1) / a.fps if Image else None
        name = f"frame_{i:03d}" + (f"_{sec:06.1f}s" if sec is not None else "") + ".jpg"
        shutil.copy(f, os.path.join(a.out_dir, name))
    print(f"共抽样 {len(frames)} 帧，保留关键帧 {len(kept)} 张 → {a.out_dir}")


if __name__ == "__main__":
    main()
