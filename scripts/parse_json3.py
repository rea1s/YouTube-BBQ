#!/usr/bin/env python3
"""parse_json3.py — 自动字幕 json3 → 元素表 + 编号文本流（流水线步骤 1）

【目的】
把 YouTube 自动字幕的 json3（词级事件流）转成一张干净的"元素表"：
  元素绝对时间 = 事件 tStartMs + seg tOffsetMs（缺省 0）
  机械化去噪，只删三类：换行符 \\n（含"收条"事件）、[...] 包裹的提示、>> / >>> 说话人标记
  去噪后**连续编号**（0,1,2,… 无空洞）；元素文本保留原样（含前导空格）
产出：
  elements.json  {"total_ms":T, "count":N, "elements":[{"i":0,"t":2639,"w":"Hello"}...]}
  stream.txt     **纯词流（无编号）**（供 AI 阅读；每行 12 个词——AI 按行分块：≈85 行 = 1000 词；
                 去编号省约一半阅读 token，且 AI 本就不写编号——见 bind_ids.py）
输入兼容 .json3 / .json / .srt（srt 为降级路径：每条 cue 视为一个元素，无词级时间）。

【用法】python parse_json3.py <json3或srt> <输出目录>

【制作过程要点 / 踩坑记录】
  1. "收条"事件（aAppend==1 且 segs 只有一个换行）是显示层分隔符、无独立信息，整体丢弃；
     其 tStartMs 恒 = 下一条目开始 − 10ms；事件的 dDurationMs 不可靠（≈下下个事件起点），不使用；
     首项元数据（无 segs）的 dDurationMs = 全片总时长 T（人工字幕没有这一项）；
  2. **坑**：`">> [laughter]"` 这类"说话人标记 + 标签"粘连的形态——剥掉 >> 后必须复检 [] 标签，
     否则会漏掉一个标签元素（本脚本已修，回归测试见 references/脚本说明.md）；
  3. 元素文本保留原始前导空格：跨事件连接缺空格的问题由 AI 在翻译环节处理。
"""
import json
import os
import re
import sys

RE_BRACKET = re.compile(r'^\[[^\]]*\]$')
RE_SPEAKER = re.compile(r'^>{2,}\s*')


def parse_json3(path):
    d = json.load(open(path, encoding='utf-8'))
    events = d if isinstance(d, list) else d['events']
    elements, total_ms = [], None
    stat = {'marker': 0, 'bracket': 0, 'speaker': 0, 'blank': 0}
    for idx, e in enumerate(events):
        segs = e.get('segs')
        if not segs:
            if idx == 0 and e.get('dDurationMs'):
                total_ms = e['dDurationMs']
            continue
        if e.get('aAppend') == 1 and len(segs) == 1 and segs[0].get('utf8') == '\n':
            stat['marker'] += 1
            continue
        t0 = e.get('tStartMs', 0)
        for s in segs:
            raw = s.get('utf8', '')
            if not raw or not raw.strip():
                stat['blank'] += 1
                continue
            vis = raw.strip()
            if RE_BRACKET.match(vis):
                stat['bracket'] += 1
                continue
            if RE_SPEAKER.match(vis):
                vis = RE_SPEAKER.sub('', vis).strip()
                stat['speaker'] += 1
                if not vis or RE_BRACKET.match(vis):
                    if vis:
                        stat['bracket'] += 1
                    continue
            elements.append((t0 + (s.get('tOffsetMs') or 0), raw if not RE_SPEAKER.match(raw.strip()) else vis))
    return [{"i": i, "t": t, "w": w} for i, (t, w) in enumerate(elements)], total_ms, stat


def parse_srt(path):
    """降级路径：srt 每条 cue 一个元素（时间取 cue 起点）"""
    text = open(path, encoding='utf-8-sig').read().replace('\r\n', '\n')
    blocks = re.split(r'\n\s*\n', text.strip())
    elements, total_ms = [], None
    for b in blocks:
        lines = [l for l in b.split('\n') if l.strip()]
        if len(lines) < 2:
            continue
        m = re.match(r'(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)', lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600000 + g[1] * 60000 + g[2] * 1000 + g[3]
        end = g[4] * 3600000 + g[5] * 60000 + g[6] * 1000 + g[7]
        content = ' '.join(l.strip() for l in lines[2:])
        if content:
            elements.append({"i": len(elements), "t": start, "w": content})
            total_ms = end
    return elements, total_ms, {'marker': 0, 'bracket': 0, 'speaker': 0, 'blank': 0}


def main():
    src, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    if src.endswith('.srt'):
        elements, total_ms, stat = parse_srt(src)
    else:
        elements, total_ms, stat = parse_json3(src)

    json.dump({"total_ms": total_ms, "count": len(elements), "elements": elements},
              open(os.path.join(outdir, 'elements.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    # 纯词流（无编号）：AI 只读文本、不写编号（编号由 bind_ids.py 机械绑定）；
    # 每行固定 12 个词——AI 按行分块/续跑（≈85 行 = 1000 个词）；
    # 去编号省约一半阅读量（[i] 标签约占 stream 体积的一半），且从根上消除"数编号"的诱惑。
    lines, cur = [], []
    for el in elements:
        cur.append(el['w'].strip())
        if len(cur) >= 12:
            lines.append(' '.join(cur))
            cur = []
    if cur:
        lines.append(' '.join(cur))
    open(os.path.join(outdir, 'stream.txt'), 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')

    dur = f'{total_ms / 1000:.1f}s' if total_ms else '?'
    print(f'元素总数: {len(elements)} | 总时长 T: {dur}')
    print(f'去噪统计: 收条 {stat["marker"]} | []标签 {stat["bracket"]} | >>剥符号 {stat["speaker"]} | 空白 {stat["blank"]}')
    print(f'产物: {outdir}/elements.json, {outdir}/stream.txt')


if __name__ == '__main__':
    main()
