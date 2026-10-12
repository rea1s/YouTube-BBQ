#!/usr/bin/env python3
"""sub_style.py — 字幕样式与输出（设计规则 §五 的实现；供 build_auto / build_manual / preview_style 引用）

【目的】
把"一条字幕记录"序列（每条含 start/end/zh/src，时间单位毫秒）渲染为：
  - ASS（默认输出格式，2 位百分秒时间戳）
  - VTT（可选，3 位毫秒、点号）
  - SRT（可选，3 位毫秒、逗号）

【样式（1080p 基准）】
  - 中文：普惠体 **95 ExtraBold**（族名 PuHuiTi-95，真字重），**82 号**，白字，描边 **2.0**，**底部锚定**（MarginV=135）
  - 原文/英文：**Alibaba Sans Bold**（族名 AlibabaSans-Bold，阿里配套拉丁字体、Bold 字重），**62 号**，#E8E8E8，描边 **1.2**，**首行顶部锚定**（MarginV=936）
  - 版面语义：中文永远同一水平；原文首行永远同一水平、第二行往下排（"双固定水平"）
  - **层约定：SRC 事件一律 Layer 1、ZH 一律 Layer 0**——libass 的"防重叠"会把**同层**的相邻
    槽位钉住（边距改了位置不动）；分层后位置 100% 听边距（中英间距 33→22px 即由此收到 2/3），
    同时消除"边距交叉导致上下顺序颠倒"的历史隐患
  - 上下顺序可切换（order: zh-src 默认 / src-zh）；形态：bilingual / zh / src
  - 字集：中文 95（29,197 码位，无生僻字缺口；L3 已弃用，原因见 setup_fonts.py）；
    英文 Alibaba Sans Bold（纯拉丁字体：ASCII/Latin-1/西里尔全，无 CJK）
字体文件在 assets/fonts/（"只改族名"副本），ffmpeg 用 fontsdir 引用，不依赖系统安装。

【制作过程要点 / 踩坑记录】
  1. 槽位与内容分离：位置(锚定+边距)跟随"槽位"，字体/字号/颜色跟随"内容"——上下切换只换槽位不动字形参数；
  2. 两条样式都"底部锚定"会让中文随原文行数上下浮动 → 必须"中文底部锚定 + 原文顶部锚定"才达成双固定水平；
  3. libass "防重叠"机制：**同层的两个槽位会被互相钉住**（边距调整几乎不起作用）；
     解法 = SRC 事件放 Layer 1——分层后边距完全生效、也不会再出现顺序颠倒；
  4. ASS 时间戳必须恰好 2 位百分秒、VTT/SRT 恰好 3 位毫秒——多/少位会让播放器把结束时间算错；
  5. 普惠体各字重共用族名会撞车 → 字体一律用改族名副本（见 setup_fonts.py）；
  6. **行宽计法（定规）**：标点**占位**（书名号/逗号各 1、破折号——按 2），只豁免行尾 ，。,.；
     英文/数字每 2 个算 1、空格 0.5；**行宽最优 10、硬上限 16**（<4 才触发并句）——见 zh_len()。

【CLI】python sub_style.py <lines.jsonl> <out.ass|.vtt|.srt> [--mode bilingual|zh|src] [--order zh-src|src-zh] [--preset default]
"""
import copy
import json
import re
import sys

# ---------- 预设 ----------
PRESETS = {
    'default': {  # 默认款：中文 95 ExtraBold；原文 = 阿里配套拉丁字体 Alibaba Sans Bold；SRC 走 Layer 1 绕开防重叠
        'zh': {'font': 'PuHuiTi-95', 'size': 82, 'color': '&H00FFFFFF', 'outline_w': 2.0, 'bold': 0},
        'src': {'font': 'AlibabaSans-Bold', 'size': 62, 'color': '&H00E8E8E8', 'outline_w': 1.2, 'bold': 0},
        'slot_upper_mv': 121,   # 上槽位（中文）：底部锚定（底距 121）
        'slot_lower_mv': 950,   # 下槽位（原文）：首行顶部锚定（顶距 950）；中英墨迹间距 22px
        'zh_only_mv': 80,       # 单中文形态专用：中文底距 80（底部锚定）
        'src_only_mv': 80,      # 单原文形态专用：原文行**底部锚定**、底距 80
    },
}
OUTLINE_COLOR = '&H00101010'
PLAY_RES = (1920, 1080)
SYSTEM_FONT_ZH = 'Microsoft YaHei'      # 系统字体档：Windows 中文默认（缺失时播放器回退其默认字体）
SYSTEM_FONT_SRC = 'Arial'


def preset_for(name, font_mode='bundled'):
    """取预设副本：`bundled`=随技能字体（默认）；`system`=系统通用字体——供"不压制"档，
    外挂播放无需安装字体（缺失时播放器回退系统默认）。"""
    p = copy.deepcopy(PRESETS[name])
    if font_mode == 'system':
        p['zh']['font'] = SYSTEM_FONT_ZH
        p['src']['font'] = SYSTEM_FONT_SRC
    return p


def _preset(p):
    return PRESETS[p] if isinstance(p, str) else p


def load_style_from_ass(path):
    """采纳用户示例 .ass 的样式：取 [V4+ Styles] 中的两条 Style（优先名字含 ZH/SRC，否则前两条），
    采纳其 **字体/字号/主色/描边色/描边宽/Bold**；**布局仍用我们的双固定水平**（锚定+边距不变）。"""
    styles = []
    for line in open(path, encoding='utf-8-sig'):
        if line.startswith('Style:'):
            styles.append([x.strip() for x in line[len('Style:'):].split(',')])
    if not styles:
        raise ValueError(f'{path} 里没有 Style 行（不是 ASS/V4+ 样式？）')
    def pick(key, idx):
        for s in styles:
            if key in s[0].upper():
                return s
        return styles[idx] if len(styles) > idx else styles[0]
    zh, src = pick('ZH', 0), pick('SRC', 1)
    def conv(s):
        return {'font': s[1], 'size': int(float(s[2])), 'color': s[3],
                'outline_color': (s[5] if len(s) > 5 and s[5] else OUTLINE_COLOR),
                'outline_w': float(s[16]) if len(s) > 16 and s[16] else 1.0,
                'bold': 1 if (len(s) > 7 and s[7] not in ('', '0')) else 0}
    out = preset_for('default')
    out['zh'].update(conv(zh))
    out['src'].update(conv(src))
    return out


# ---------- 时间戳 ----------
def _split(ms):
    h, ms = divmod(int(ms), 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return h, m, s, ms


def ts_ass(ms):
    h, m, s, ms = _split(ms)
    cs = round(ms / 10)
    if cs == 100:
        s, cs = s + 1, 0
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'


def ts_vtt(ms):
    h, m, s, ms = _split(ms)
    return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'


def ts_srt(ms):
    h, m, s, ms = _split(ms)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


# ---------- 文本清理（呈现层，幂等）----------
def strip_zh(t):
    return re.sub(r'[，。]+$', '', t)


def strip_src(t):
    ls = t.split('\n')
    ls[-1] = re.sub(r'[,\.]+$', '', ls[-1])
    return '\n'.join(ls)


def zh_len(s):
    """行宽计法（设计规则 §三 步骤4，定规）：汉字/标点**各占 1**（书名号、逗号各 1；破折号——按 2 计）；
    **只有行尾那一个 ，。,. 不占位**（反正会清理）；译文里的英文/数字**每 2 个算 1**（0.5/字符）；
    空格算 0.5。返回浮点宽度（行宽最优 10、硬上限 16）。"""
    s = re.sub(r'[，。,.]+$', '', (s or '').strip())
    return sum(0.5 if (c == ' ' or (c.isascii() and c.isalnum())) else 1.0 for c in s)


def load_lines(path):
    return [json.loads(l) for l in open(path, encoding='utf-8') if l.strip()]


# ---------- ASS ----------
def ass_header(preset='default', order='zh-src', mode='bilingual'):
    p = _preset(preset)
    upper_mv, lower_mv = p['slot_upper_mv'], p['slot_lower_mv']

    def style_line(name, content, slot):
        c = p[content]
        align, mv = (2, upper_mv) if slot == 'upper' else (8, lower_mv)
        if content == 'zh' and mode == 'zh':                 # 单中文：中文底距单独设定（仍底部锚定）
            mv = p.get('zh_only_mv', mv)
        if content == 'src' and mode == 'src':               # 单原文：原文行改底部锚定、底距单独设定
            align, mv = 2, p.get('src_only_mv', mv)
        return (f"Style: {name},{c['font']},{c['size']},{c['color']},&H000000FF,{c.get('outline_color', OUTLINE_COLOR)},&H80000000,"
                f"{c.get('bold', 0)},0,0,0,100,100,0,0,1,{c['outline_w']},1,{align},60,60,{mv},1")

    slots = ('upper', 'lower') if order == 'zh-src' else ('lower', 'upper')
    zh_slot, src_slot = slots
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES[0]}
PlayResY: {PLAY_RES[1]}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_line('ZH', 'zh', zh_slot)}
{style_line('SRC', 'src', src_slot)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write_ass(rows, path, mode='bilingual', order='zh-src', preset='default'):
    out = [ass_header(preset, order, mode)]
    for r in rows:
        a, b = ts_ass(r['start']), ts_ass(r['end'])
        if mode in ('bilingual', 'zh') and r.get('zh'):
            out.append(f"Dialogue: 0,{a},{b},ZH,,0,0,0,,{strip_zh(r['zh']).replace(chr(10), r'\N')}")
        if mode in ('bilingual', 'src') and r.get('src'):
            s = strip_src(r['src']).replace('\n', r'\N')
            # SRC 固定 Layer 1：绕开 libass 防重叠（同层相邻槽位会被钉住，边距失效）
            out.append(f"Dialogue: 1,{a},{b},SRC,,0,0,0,,{s}")
    open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(out) + '\n')


# ---------- VTT / SRT ----------
def _text_block(r, mode, order):
    lines = []
    if mode in ('bilingual', 'zh') and r.get('zh'):
        lines.append(strip_zh(r['zh']))
    if mode in ('bilingual', 'src') and r.get('src'):
        lines.append(strip_src(r['src']))
    if mode == 'bilingual' and order == 'src-zh':
        lines.reverse()
    return '\n'.join(lines)


def write_vtt(rows, path, mode='bilingual', order='zh-src'):
    out = ['WEBVTT', '']
    for r in rows:
        out.append(f"{ts_vtt(r['start'])} --> {ts_vtt(r['end'])}")
        out.append(_text_block(r, mode, order))
        out.append('')
    open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(out))


def write_srt(rows, path, mode='bilingual', order='zh-src'):
    out = []
    for i, r in enumerate(rows, 1):
        out.append(str(i))
        out.append(f"{ts_srt(r['start'])} --> {ts_srt(r['end'])}")
        out.append(_text_block(r, mode, order))
        out.append('')
    open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(out))


# ---------- CLI ----------
def main():
    args = sys.argv
    if len(args) < 3:
        print(__doc__)
        sys.exit(2)

    def opt(name, default):
        return args[args.index(name) + 1] if name in args else default

    src, out = args[1], args[2]
    mode = opt('--mode', 'bilingual')
    order = opt('--order', 'zh-src')
    preset = opt('--preset', 'default')
    if '--mono' in args:
        mode = 'zh'
    if '--src-only' in args:
        mode = 'src'

    rows = load_lines(src)
    if out.endswith('.ass'):
        write_ass(rows, out, mode, order, preset)
    elif out.endswith('.vtt'):
        write_vtt(rows, out, mode, order)
    elif out.endswith('.srt'):
        write_srt(rows, out, mode, order)
    else:
        print('输出扩展名需为 .ass / .vtt / .srt')
        sys.exit(2)
    print(f'{out}: {len(rows)} 条 | 形态 {mode} | 顺序 {order} | 预设 {preset}')


if __name__ == '__main__':
    main()
