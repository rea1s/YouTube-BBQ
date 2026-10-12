#!/usr/bin/env python3
"""build_manual.py — 人工字幕：输出 + 机检（人工流水线收尾）

【目的】
人工 cue 表（AI 已按"原 cue 段数"填好译文）→ 字幕文件（ASS 默认 + 可选 VTT/SRT）+ 机检。
**特点：时间 = 人工 start/end，零重算**（不套自动路径的 M/N/L）；人工切分不动（不合并）。

【用法】
  python build_manual.py <项目目录> [--outdir 目录] [--name 基名]
        [--mode bilingual|zh|src] [--order zh-src|src-zh] [--preset default] [--vtt] [--srt]
        [--src-lines <源语轨.jsonl>]
  （扁平结构：过程文件与产物同目录）
  读 <项目目录>/lines.jsonl（每条 {"start","end","zh"[,"src"]}；①直用时只有 zh；②翻译时有 zh+src）
  **--src-lines = 中源双语（原生两轨直出）**：lines.jsonl 为中文轨 cue（parse_manual --as-zh 产物）、
  src-lines 为源语言轨 cue（parse_manual 默认产物）；两轨**时间轴各自独立**（不配对、不对齐、零重算），
  输出为同一 ASS 的两个事件流（中文 ZH 样式 Layer 0 / 源语 SRC 样式 Layer 1）；机检**逐轨**进行，
  跨轨重叠是常态、不作告警。

【机检（人工版）】
  时间格式/非负、end > start；中文 ≤2 行、原文 ≤2 行；末尾标点已清理；
  **零重合统计式**（人工字幕如有重叠，报告但不强修——尊重人工时间）；
  mode≠src 时每条必须有译文（全覆盖）。
  --src-lines 双轨模式：以上检查**按轨各查一遍**；不做配对/覆盖检查、跨轨重叠不报。

【制作过程要点 / 坑】
  1. ①（人工中文直用）与 ②（人工外文翻译）共用本脚本，只是数据来源不同；
  2. 行尾标点清理必须**执行在先、校验在后**（只写校验会把未清理项一次性全报出来）——
     清理必须执行后再校验，顺序不能反；
  3. 人工字幕允许中文 2 行（超长句特例，与自动路径"中文严格 1 行"不同）；
  4. --src-lines（中源双语）时两轨只做呈现，不做配对——人工双语各自的时间轴就是成品。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sub_style  # noqa: E402


def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def main():
    mid = sys.argv[1]
    workdir = os.path.abspath(mid)          # 传入「中间文件」目录（lines.jsonl 在这里）
    # 交付物（.ass + 机检报告）出到**一级目录**：输入目录名为「中间文件」→ 取父目录；否则原地
    deliver = os.path.dirname(workdir) if os.path.basename(workdir) == '中间文件' else workdir
    outdir = opt('--outdir', deliver)
    report_dir = deliver
    mode = opt('--mode', 'bilingual')
    order = opt('--order', 'zh-src')
    preset = opt('--preset', 'default')
    label = opt('--label')
    name = opt('--name', 'subtitle')

    line_file = opt('--lines', 'lines.jsonl')
    rows = [json.loads(l) for l in open(os.path.join(mid, line_file), encoding='utf-8') if l.strip()]
    rows_src = []
    src_lines = opt('--src-lines')
    if src_lines:
        sp = src_lines if os.path.isabs(src_lines) else os.path.join(mid, src_lines)
        rows_src = [json.loads(l) for l in open(sp, encoding='utf-8') if l.strip()]
    dual = bool(rows_src)                              # 中源双语：两条独立事件流
    label = label or ('人工字幕·原生双语' if dual else '人工字幕')

    # 清理（执行后再校验）
    for r in rows:
        r['zh'] = sub_style.strip_zh(r.get('zh', ''))
        if r.get('src'):
            r['src'] = sub_style.strip_src(r['src'])
    for r in rows_src:
        r['src'] = sub_style.strip_src(r.get('src', ''))

    os.makedirs(outdir, exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)
    ass_path = os.path.join(outdir, f'{name}.ass')
    if dual:
        # 双轨独立：中文轨 + 源语轨各走各的时间轴；按 start 归并只影响文件里事件排列
        out_rows = rows_src if mode == 'src' else sorted(rows + rows_src, key=lambda r: r['start'])
    else:
        out_rows = rows
    sub_style.write_ass(out_rows, ass_path, mode, order, preset)
    if '--vtt' in sys.argv:
        sub_style.write_vtt(out_rows, os.path.join(outdir, f'{name}.vtt'), mode, order)
    if '--srt' in sys.argv:
        sub_style.write_srt(out_rows, os.path.join(outdir, f'{name}.srt'), mode, order)

    errs, warns = [], []
    if dual:
        # 中源双语：两条独立事件流——逐轨检查（跨轨同时显示是常态，不作重叠问题）
        def check_stream(rs, side, tag):
            for i, r in enumerate(rs):
                tx = r.get(side, '')
                if r['end'] <= r['start']:
                    errs.append(f'{tag}#{i} 结束 ≤ 开始')
                if not tx.strip():
                    errs.append(f'{tag}#{i} 空文本')
                elif tx.count('\n') >= 2:
                    errs.append(f'{tag}#{i} 超过 2 行')
                if side == 'zh' and re.search(r'[，。]$', tx):
                    errs.append(f'{tag}#{i} 中文行尾标点未清理')
                if side == 'src' and tx and re.search(r'[,.]$', tx.split('\n')[-1]):
                    errs.append(f'{tag}#{i} 原文行尾标点未清理')
                if i + 1 < len(rs) and r['end'] > rs[i + 1]['start']:
                    warns.append(f'{tag}#{i} 与下条重叠 {r["end"] - rs[i + 1]["start"]}ms（人工时间，报告不修）')
        check_stream(rows, 'zh', '中')
        check_stream(rows_src, 'src', '源')
        long_ = [i for i, r in enumerate(rows)
                 if sub_style.zh_len(r.get('zh', '')) > 16
                 and not re.match(r'^(翻译人员|校对人员)', r.get('zh', ''))]
        if long_:
            warns.append(f'译文行宽 >16（超过上限，建议拆行）：{long_}')
        head = [f'# 机检报告（{label}）', '',
                f'- 中文轨 {len(rows)} 条 / 源语轨 {len(rows_src)} 条（**时间轴各自独立**：不配对、零重算）',
                f'- 时间：中文 {rows[0]["start"]}~{rows[-1]["end"]}ms；源语 {rows_src[0]["start"]}~{rows_src[-1]["end"]}ms',
                f'- 错误 {len(errs)} / 告警 {len(warns)}', '']
    else:
        for i, r in enumerate(rows):
            if r['end'] <= r['start']:
                errs.append(f'#{i} 结束 ≤ 开始')
            if mode in ('bilingual', 'zh'):
                if not r.get('zh', '').strip():
                    errs.append(f'#{i} 缺译文')
                elif r['zh'].count('\n') >= 2:
                    errs.append(f'#{i} 中文超过 2 行')
                if re.search(r'[，。]$', r['zh']):
                    errs.append(f'#{i} 中文行尾标点未清理')
            if r.get('src'):
                if r['src'].count('\n') >= 2:
                    errs.append(f'#{i} 原文超过 2 行')
                if re.search(r'[,.]$', r['src'].split('\n')[-1]):
                    errs.append(f'#{i} 原文行尾标点未清理')
            if i + 1 < len(rows) and r['end'] > rows[i + 1]['start']:
                warns.append(f'#{i} 与下条重叠 {r["end"] - rows[i + 1]["start"]}ms（人工时间，报告不修）')

        long_ = [i for i, r in enumerate(rows)
                 if r.get('zh') and sub_style.zh_len(r['zh']) > 16 and not re.match(r'^(翻译人员|校对人员)', r['zh'])]
        if long_:
            warns.append(f'译文行宽 >16（超过上限，建议拆行）：{long_}')

        head = [f'# 机检报告（{label}）', '', f'- cue 数：{len(rows)}',
                f'- 时间 {rows[0]["start"]}ms ~ {rows[-1]["end"]}ms',
                f'- 错误 {len(errs)} / 告警 {len(warns)}', '']
    text = '\n'.join(head + ['❌ ' + e for e in errs] + ['⚠️ ' + w for w in warns] +
                     (['✅ 全部通过'] if not errs and not warns else []))
    open(os.path.join(report_dir, f'{name}.机检报告.md'), 'w', encoding='utf-8').write(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
