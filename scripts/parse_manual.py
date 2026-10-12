#!/usr/bin/env python3
"""parse_manual.py — 人工字幕 json3 → cue 编号表（人工流水线步骤 1）

【目的】
人工字幕的每一条 cue 带**可靠的 start 和 end**（人工制作），本脚本把它们原样保留成表，
后续环节**零重算**地沿用（人工的切分与时间轴不动，这是人工路径与自动路径最大的区别）。

【用法】
  python parse_manual.py <json3> <输出.jsonl> [--as-zh] [--auto]
  - 默认输出 {"start":ms,"end":ms,"src":"原文"}  （供②：AI 补 "zh" 字段）
  - --as-zh 输出 {"start":ms,"end":ms,"zh":"原文"}（供①：人工中文轨直接用）
  - --auto：输入是**自动字幕轨**（滚动式 json3）时先 **cue 化**（在文本变化点切分、
    结束=下一状态开始）——"中源双语"无人工源语轨时取自动源语轨做原文行（见设计规则 §4.3）

【输入输出】
  输入：人工字幕 json3（整句事件、无词级偏移、无收条/元数据首项）；
  输出：JSONL cue 表（编号从 0 起，按时间序）；同时打印统计（条数/时间范围/无句末标点条数——
  后者供 AI 做"合并整句"时参考）。

【制作过程要点 / 数据特征】
  1. 人工 cue 常非完整句（多数无句末标点；一句常被人工切成多条 cue）→
     AI 翻译前须先"合并整句"，再把译文按原 cue 段数切回；
  2. 时间可靠：start = tStartMs、end = tStartMs + dDurationMs（相邻零重叠）；文本内可含换行（两行显示是常态）；
  3. 开头可能有署名行（"翻译人员：…"）：默认保留（那是译者署名），报告中提示可删；
  4. --auto（自动轨）：滚动式字幕同一状态会重复出现——cue 化后每个显示状态一条；
     ASR 文本可能有听写误差，属正常（记入报告）。
"""
import json
import re
import sys

END_PUNCT = re.compile(r'[.!?。！？…]["”』」]?$')


def main():
    src, out = sys.argv[1], sys.argv[2]
    as_zh = '--as-zh' in sys.argv
    d = json.load(open(src, encoding='utf-8'))
    events = d if isinstance(d, list) else d['events']

    raw = []
    for e in events:
        segs = e.get('segs') or []
        text = ''.join(s.get('utf8', '') for s in segs).strip()
        if not text:
            continue
        raw.append((e.get('tStartMs', 0), e.get('dDurationMs') or 0, text))

    if '--auto' in sys.argv:
        # 自动字幕是滚动式：同一显示状态重复出现 → 只在文本变化处切分，结束 = 下一状态开始
        cues = []
        for i, (t, dur, text) in enumerate(raw):
            if cues and cues[-1]['text'] == text:
                continue
            end = None
            for t2, _d, tx2 in raw[i + 1:]:
                if tx2 != text:
                    end = t2
                    break
            if end is None:
                end = t + (dur or 2000)
            cues.append({'start': t, 'end': max(end, t + 200), 'text': text})
    else:
        cues = [{'start': t, 'end': t + dur, 'text': text} for t, dur, text in raw]

    key = 'zh' if as_zh else 'src'
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        for c in cues:
            rec = {'start': c['start'], 'end': c['end'], key: c['text']}
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    no_end = sum(1 for c in cues if not END_PUNCT.search(c['text'].strip()))
    tail = '（显示状态，不合并）' if '--auto' in sys.argv else '（需 AI 合并整句）'
    print(f'cue 数: {len(cues)} | 时间 {cues[0]["start"]}ms ~ {cues[-1]["end"]}ms | '
          f'句末无标点 {no_end}/{len(cues)}' + tail)
    print(f'产物: {out}（字段 {key}）')


if __name__ == '__main__':
    main()
