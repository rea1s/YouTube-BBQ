#!/usr/bin/env python3
"""preview_style.py — 样式预览帧（拿待处理视频渲染一张带字幕的样子）

【目的】
用户在挑"字幕样式"时要看到"在我的视频上长什么样"：取一条真实字幕（或合成样例），
按指定样式渲染成一帧 PNG。没有字幕文件时用合成样例（一行中文 + 两行原文——
正好展示"双固定水平 + 原文第二行往下"的版面语义）。

【用法】
  python preview_style.py --video <视频> --out <预览.png>
        [--lines <lines.jsonl>] [--at 秒] [--preset default] [--fontsdir DIR]
        [--mode bilingual|zh|src] [--order zh-src|src-zh]
  - 有 --lines：取 --at（默认=前 3 分钟内时长最长的一条）的样例渲染；
  - 无 --lines：合成样例，--at 默认 3 秒（避开片头黑帧）。

【制作过程要点 / 坑】
  1. 与 burn.py 同一套"临时目录 + 固定安全名 + cwd"的路径技巧（防滤镜转义炸膛）；
  2. 渲染完应目视核对字体是否真的命中（字体不对会**静默回退**，预览就是干这个的）；
  3. 预览用的 ASS 与正式输出共用 sub_style —— 预览什么样，压出来就什么样。
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sub_style  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FONTSDIR = os.path.join(SKILL_DIR, 'assets', 'fonts')

SAMPLE = [{'start': 0, 'end': 600000,
           'zh': '这是一条中文字幕样例，用来预览样式效果',
           'src': 'This is a sample subtitle line\nused to preview the style.'}]


def opt(name, default=None):
    a = sys.argv
    return a[args_idx(a, name) + 1] if name in a else default


def args_idx(a, name):
    return a.index(name)


def main():
    video, out = opt('--video'), opt('--out')
    lines_path, preset = opt('--lines'), opt('--preset', 'default')
    mode, order = opt('--mode', 'bilingual'), opt('--order', 'zh-src')
    fontsdir = opt('--fontsdir', DEFAULT_FONTSDIR)
    at = opt('--at')
    if not (video and out):
        print(__doc__)
        sys.exit(2)

    if lines_path:
        rows = sub_style.load_lines(lines_path)
        if at:
            rows = [r for r in rows if r['start'] <= float(at) * 1000 <= r['end']] or rows
        else:
            cand = [r for r in rows if r['start'] < 180000]
            rows = [max(cand, key=lambda r: r['end'] - r['start'])] if cand else rows
        rows = rows[:1]
        at_ms = int(float(at) * 1000) if at else (rows[0]['start'] + rows[0]['end']) // 2
        rows[0] = dict(rows[0], start=at_ms - 500, end=at_ms + 500)
    else:
        at_ms = int(float(at) * 1000) if at else 3000
        rows = [dict(SAMPLE[0], start=at_ms - 500, end=at_ms + 500)]

    tmp = tempfile.mkdtemp(prefix='prev_')
    try:
        sub_style.write_ass(rows, os.path.join(tmp, '_sub.ass'), mode, order, preset)
        os.makedirs(os.path.join(tmp, 'fonts'), exist_ok=True)
        for fn in os.listdir(fontsdir):
            if fn.lower().endswith(('.ttf', '.otf', '.ttc')):
                shutil.copy(os.path.join(fontsdir, fn), os.path.join(tmp, 'fonts', fn))
        vf = 'ass=_sub.ass:fontsdir=fonts'
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', os.path.abspath(video),
               '-ss', f'{at_ms / 1000:.3f}', '-frames:v', '1', '-vf', vf, os.path.abspath(out)]
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode != 0:
            print('[错误] ffmpeg 失败：\n' + (r.stderr or '')[-1500:])
            sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f'[完成] {os.path.abspath(out)}（预设 {preset}，时刻 {at_ms / 1000:.1f}s）')


if __name__ == '__main__':
    main()
