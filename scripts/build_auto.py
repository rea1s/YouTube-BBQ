#!/usr/bin/env python3
"""build_auto.py — 自动字幕：打轴 + 输出 + 机检（流水线步骤 5 + 终检①）

【目的】
把 AI 写好的字幕行（lines_p*.jsonl，含 ids/src/zh）变成可用的字幕文件：
  打轴（查表，不造时间）→ 输出 ASS（默认）+ 可选 VTT/SRT → 全量机检 → 机检报告。

【打轴规则（设计规则 §三 步骤 5）】
  记号：M=本条结束时间，N=下一条开始时间，L=本条末元素开始时间；本条开始 = 首元素时间（查元素表）。
  1) N − L > 1.5s → M = L + 1.5s；
  2) 否则 M = N − subgap（**默认 0ms：本条显示到下一条出现，紧贴**）；硬约束 M ≥ L
     （subgap > 0 时才可能触发，触及时取 M = N）；
  3) 末条 M = L + 1.5s（超过总时长 T 截到 T）。

【用法】
  python build_auto.py <项目目录> [--outdir 目录] [--name 文件基名]
        [--mode bilingual|zh|src] [--order zh-src|src-zh] [--preset default]
        [--font bundled|system] [--style-from <用户示例.ass>]
        [--gap 1500] [--subgap 0] [--upto N] [--vtt] [--srt]
  （扁平结构：过程文件与产物同目录）
  读：elements.json / dropped.json / lines_p*.jsonl；写：cues.json、
      <名称>.ass（默认；可加 .vtt/.srt）、<名称>.机检报告.md。

【机检项（终检①，全部自动）】
  ① 时间戳重算比对（重解析写出的 ASS，毫秒→百分秒取整后逐条一致）
  ② 零重合 + M ≥ L + 编号顺序
  ③ 编号完整性（全范围覆盖；空洞 ⊆ 丢弃清单；无重复归属）+ ③b 丢弃清单反向校验
     （清单里每个编号必须真的是语气词，或在 filler_scan.py --extra 里登记过——抓"差一位/错删"）
  ④ 格式（zh 1 行 / src ≤2 行 / zh 行宽 ≤16 报错、<4 告警（行宽计法见 sub_style.zh_len）/ 末尾标点已清理）
     ——**超限的修复动作=回去重新分划（拆两条），不得改译文凑行宽**
  ⑤ 原文—编号一致性（<0.6 报错：机械绑定后罕见，通常是某行 src 被大幅改写——重跑 bind_ids --fix；
     0.6~0.75 告警=轻微修正处）

【制作过程要点 / 坑】
  1. 本脚本是交付前的硬闸门：漏句、错圈语气词、超长行、时间戳格式都会被逐条报出；
  2. 相似度低**不是错**：是 AI 做了轻微修正（假开头清理、听写纠正）的正常痕迹；
  3. ASS 2 位百分秒会把毫秒取整到 10ms——比对时按取整值核对（VTT/SRT 为 3 位毫秒，精确）；
  4. **坑**：目录名含 `[]`（默认命名必含中括号）时裸 glob 匹配不到分片 → `glob.escape(mid)`。
"""
import glob
import json
import os
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sub_style  # noqa: E402


def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def norm(s):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff\u3040-\u30ff]', '', s.lower())


def main():
    mid = sys.argv[1]
    workdir = os.path.abspath(mid)          # 传入「中间文件」目录（过程文件都在这里）
    # 交付物（.ass/.vtt/.srt + 机检报告）出到**一级目录**：输入目录名为「中间文件」→ 取父目录；否则原地（兼容旧布局）
    deliver = os.path.dirname(workdir) if os.path.basename(workdir) == '中间文件' else workdir
    outdir = opt('--outdir', deliver)
    report_dir = deliver
    mode = opt('--mode', 'bilingual')
    order = opt('--order', 'zh-src')
    preset = opt('--preset', 'default')
    font_mode = opt('--font', 'bundled')     # bundled（默认，随技能字体）/ system（系统字体；供"不压制"档）
    style_from = opt('--style-from')         # 采纳用户示例 .ass 的样式（字体/字号/颜色/描边）
    gap, subgap = int(opt('--gap', '1500')), int(opt('--subgap', '0'))   # subgap 默认 0：紧贴下一条（--subgap 可调大）
    upto = int(opt('--upto', '0')) or None
    name = opt('--name')
    if not name:
        dec = os.path.join(mid, 'decision.json')
        if os.path.isfile(dec):
            d = json.load(open(dec, encoding='utf-8'))
            name = d.get('basename') or d['id']          # 约定：标题基名（与 mp4/报告同名）；无则回落视频 ID
        else:
            name = 'subtitle'

    ej = json.load(open(os.path.join(mid, 'elements.json'), encoding='utf-8'))
    elements, total_ms = ej['elements'], ej['total_ms']
    lines = []
    # 目录含 [] 等 glob 元字符时必须 escape（默认命名 "[作者] 标题-YouTube" 必含中括号）
    for p in sorted(glob.glob(os.path.join(glob.escape(mid), 'lines_p*.jsonl'))):
        lines += [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
    if not lines:
        print(f'[错误] 在 {mid} 未找到 lines_p*.jsonl（没有字幕行可打轴）')
        sys.exit(1)
    dropped_path = os.path.join(mid, 'dropped.json')
    no_dropped = not os.path.isfile(dropped_path)
    dropped = set() if no_dropped else set(json.load(open(dropped_path, encoding='utf-8'))['dropped'])
    tmap = {e['i']: e['t'] for e in elements}
    wmap = {e['i']: e['w'] for e in elements}

    # ---------- 打轴 ----------
    cues = []
    for k, ln in enumerate(lines):
        S, L = tmap[ln['ids'][0][0]], tmap[ln['ids'][-1][1]]
        if k + 1 < len(lines):
            N = tmap[lines[k + 1]['ids'][0][0]]
            if N - L > gap:
                M = L + gap
            else:
                M = N - subgap
                if M < L:
                    M = N
        else:
            M = min(L + gap, total_ms) if total_ms else L + gap
        cues.append({'start': S, 'end': M, 'zh': sub_style.strip_zh(ln.get('zh', '')),
                     'src': sub_style.strip_src(ln.get('src', '')) if ln.get('src') else '',
                     'ids': ln['ids']})
    if mode == 'src':
        for c in cues:
            c['zh'] = ''
    json.dump(cues, open(os.path.join(mid, 'cues.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)

    # ---------- 输出 ----------
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(report_dir, exist_ok=True)
    ass_path = os.path.join(outdir, f'{name}.ass')
    if style_from:
        preset_obj = sub_style.load_style_from_ass(style_from)
        print(f'[样式] 采纳示例 ass：{style_from}（取字体/字号/颜色/描边；布局仍用双固定水平）')
    else:
        preset_obj = sub_style.preset_for(preset, font_mode)
        if font_mode == 'system':
            print('[样式] 系统字体档（外挂播放免装字体；字体缺失时播放器回退其系统默认）')
    sub_style.write_ass(cues, ass_path, mode, order, preset_obj)
    if '--vtt' in sys.argv:
        sub_style.write_vtt(cues, os.path.join(outdir, f'{name}.vtt'), mode, order)
    if '--srt' in sys.argv:
        sub_style.write_srt(cues, os.path.join(outdir, f'{name}.srt'), mode, order)

    # ---------- 机检 ----------
    R, errors, warns = [], [], []

    def err(m):
        errors.append(m)
        R.append('❌ ' + m)

    def warn(m):
        warns.append(m)
        R.append('⚠️ ' + m)

    def ok(m):
        R.append('✅ ' + m)

    if no_dropped:
        warn('未找到 dropped.json，丢弃清单按空处理（若确有删除的语气词，编号检查会报缺失）')

    # ① 事件数 + 时间戳重算比对（重解析 ASS）
    n_ev = sum((1 if (mode in ('bilingual', 'zh') and c['zh']) else 0) +
               (1 if (mode in ('bilingual', 'src') and c['src']) else 0) for c in cues)
    ass_ts = []
    for line in open(ass_path, encoding='utf-8'):
        # 层号任意（SRC=1 分层绕开 libass 防重叠；见 sub_style）
        m = re.match(r'^Dialogue: \d+,(\d+):(\d{2}):(\d{2})\.(\d{2}),(\d+):(\d{2}):(\d{2})\.(\d{2}),', line)
        if m:
            g = [int(x) for x in m.groups()]
            ass_ts.append((g[0] * 3600000 + g[1] * 60000 + g[2] * 1000 + g[3] * 10,
                           g[4] * 3600000 + g[5] * 60000 + g[6] * 1000 + g[7] * 10))
    exp_ts = []
    for c in cues:
        pair = (round(c['start'] / 10) * 10, round(c['end'] / 10) * 10)
        if mode in ('bilingual', 'zh') and c['zh']:
            exp_ts.append(pair)
        if mode in ('bilingual', 'src') and c['src']:
            exp_ts.append(pair)
    if len(ass_ts) != n_ev:
        err(f'ASS 事件数不符：写出 {len(ass_ts)}，应为 {n_ev}')
    elif ass_ts != exp_ts:
        err('时间戳重算比对不一致（写出与计算不符）')
    else:
        ok(f'时间戳重算比对：{n_ev} 条事件全部一致')

    # ② 零重合 + M ≥ L + 顺序
    bad = []
    for k, c in enumerate(cues):
        if c['end'] < c['start'] or c['end'] < tmap[c['ids'][-1][1]]:
            bad.append(k)
        if k + 1 < len(cues) and c['end'] > cues[k + 1]['start']:
            bad.append(k)
        if k + 1 < len(cues) and c['ids'][-1][1] >= cues[k + 1]['ids'][0][0]:
            bad.append(k)
    err(f'时间轴/顺序异常：{bad[:5]}') if bad else ok('零重合 + M≥L + 编号顺序：全部通过')

    # ③ 编号完整性
    cov = {}
    for k, c in enumerate(cues):
        for lo, hi in c['ids']:
            for i in range(lo, hi + 1):
                cov.setdefault(i, []).append(k)
    upper = upto or max(tmap)
    dup = [i for i, v in cov.items() if len(v) > 1]
    missing = sorted(set(range(0, upper + 1)) - set(cov) - dropped)
    conflict = sorted(set(cov) & dropped)
    if dup or missing:
        err(f'编号问题：重复 {dup[:5]} | 丢失 {missing[:10]}')
    else:
        ok(f'编号完整性：0~{upper} 全覆盖；空洞 {len(dropped)} 个 ⊆ 丢弃清单')
    if conflict:
        warn(f'编号既被覆盖又在丢弃清单：{conflict[:5]}')
    # ③b 丢弃清单反向校验：清单里的编号必须是元素表里真实的语气词
    # （丢弃清单必须反向校验：每个编号要真的是语气词，或在 filler_scan --extra 里登记过——
    #   防"差一位/误删内容词"；非语气词丢弃走 --extra 登记留痕）
    FILLW = {'um', 'uh', 'umm', 'uhh', 'uhhh', 'ah', 'ahh', 'er', 'erm', 'hmm', 'mhm', 'mm', 'mmm'}
    _nz = lambda s: re.sub(r'[^a-z]', '', (s or '').lower())
    _fa = os.path.join(mid, 'fillers_auto.json')      # 额外丢弃只有在 filler_scan --extra 里登记过才放行（留痕）
    extra_ok = set(json.load(open(_fa, encoding='utf-8')).get('extra') or []) if os.path.isfile(_fa) else set()
    bad_drop = [(i, wmap.get(i, '').strip()) for i in sorted(dropped)
                if _nz(wmap.get(i, '')) not in FILLW and i not in extra_ok]
    if bad_drop:
        err(f'丢弃清单里有"非语气词"编号且未登记（疑似差一位/错删）：{bad_drop[:8]}——'
            f'语气词用 `filler_scan.py --write-dropped` 机械扫出；确要额外丢弃，用 `--extra <编号>` 登记后再写清单')

    # ④ 格式
    fe, short = [], []
    for k, c in enumerate(cues):
        if mode in ('bilingual', 'zh'):
            if not c['zh'].strip():
                fe.append(f'#{k + 1} 缺译文')
                continue
            zl = sub_style.zh_len(c['zh'])
            if zl > 16:
                fe.append(f'#{k + 1} 中文行宽 {zl:g} 超上限 16（修复=重新分划拆两条；勿改译文凑行宽）')
            elif zl < 4:
                short.append(k + 1)
            if c['zh'].count('\n') >= 1:
                fe.append(f'#{k + 1} 中文多于 1 行')
            if re.search(r'[，。]$', c['zh'].strip()):
                fe.append(f'#{k + 1} 中文行尾标点未清理')
        if c['src']:
            if c['src'].count('\n') > 1:
                fe.append(f'#{k + 1} 原文超过 2 行')
            if re.search(r'[,.]$', c['src'].split('\n')[-1].strip()):
                fe.append(f'#{k + 1} 原文行尾标点未清理')
    err('格式：' + '；'.join(fe[:8])) if fe else ok('格式规则：全部通过')
    if short:
        warn(f'{len(short)} 条行宽低于 4（可考虑向后并）：{short}')

    # ⑤ 原文—编号一致性（<0.6 视为错位=错误；0.6~0.75 轻微修正=告警）
    low, bad5 = [], []
    for k, c in enumerate(cues):
        if not c['src']:
            continue
        raw = ''.join(wmap[i] for lo, hi in c['ids'] for i in range(lo, hi + 1))
        r = SequenceMatcher(None, norm(raw), norm(c['src'])).ratio()
        if r < 0.6:
            bad5.append((k + 1, round(r, 2)))
        elif r < 0.75:
            low.append((k + 1, round(r, 2)))
    if bad5:
        err(f'原文—编号疑似错位（相似度 <0.6；机械绑定后罕见，通常是某行 src 被大幅改写）：{bad5}——'
            f'重跑 `python scripts/bind_ids.py "<中间目录>" --fix` 重新机械绑定；用 `--show N` 看单行元素明细')
    elif low:
        warn(f'原文—编号相似度偏低（0.6~0.75，通常=轻微修正处）：{low}')
    else:
        ok('原文—编号一致性：通过')

    head = ['# 机检报告', '', f'- 字幕条数：{len(cues)}', f'- 覆盖 0~{upper}（元素 {len(elements)}）',
            f'- 丢弃编号：{len(dropped)}', f'- 错误 {len(errors)} / 告警 {len(warns)}', '']
    text = '\n'.join(head + R)
    open(os.path.join(report_dir, f'{name}.机检报告.md'), 'w', encoding='utf-8').write(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
