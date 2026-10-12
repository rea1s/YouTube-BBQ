#!/usr/bin/env python3
"""bind_ids.py — 机械绑定编号（把 AI 从"编号记账"里彻底解放）

【背景与地位】
AI 手写 `{ids, src, zh}` 时 ids 是"人工记账"，容易整段漂移、事后只能靠人逐行校正。
本脚本改成**机械绑定**：AI 只写**纯文本**（每行 {src, zh}），ids 由本脚本用
**全局顺序对齐**（元素词流 ←→ 行文本词流）自动算出——文本本来就来自元素流，对齐近乎恒等。

【流水线位置】parse → filler_scan（机械扫语气词）→ **AI 写 lines_text.jsonl（只 {src, zh}）**
→ **bind_ids.py（机械绑编号，产出 lines_p*.jsonl）** → merge_shorts → build_auto（机械打轴）。

【用法】python bind_ids.py <中间文件目录> [--fix] [--show N]
  读：elements.json + dropped.json（剔除语气词）+ lines_text.jsonl（或按序自动合并 lines_text_p*.jsonl；
      两者都没有则用已带 ids 的 lines_p*.jsonl 做校验模式）
  --fix  ：写出 lines_p1.jsonl（{ids, src, zh}；同时把旧分片备份为 prebind_lines_p*.jsonl）
  默认   ：只报告（未覆盖元素、多余词分类、逐行对齐率、总对齐率；问题条目都带**上下文词**
          〔前后各 4 个，目标词用 ▸◂ 标出〕——stream.txt 无编号，AI 按上下文搜定位）
  --show N：打印第 N 行绑定的元素明细（区间元素 + 前后各 8 个），供人工核对

【对齐原理（为什么可靠）】
  · 元素词流 t：全部元素按序、剔除语气词后规范化成 token（含"token→元素编号"映射）；
  · 行文本词流 s：各行 src 按行序拼接后规范化成 token（含"token→行号"映射）；
  · SequenceMatcher(None, t, s, autojunk=False) 做**全局单调**对齐——
    equal/replace 块内的 token 按序配对（replace 视为"听写修正"，合法），
    t 侧多余=未被任何行覆盖的元素（报出）、s 侧多余=AI 多写/改写（分类报出）；
  · 每行 ids = 其 token 映射到的元素编号集合 → 合并连续区间；
  · **元素级归属**：元素在机器里是"原子"——若 AI 的切线落在多词元素内部
    （如元素 " going to" 被切成 going | to 两行），该元素整体归**持有词数多**的一侧
    （平手归前行），并记一行报表。所以 AI 切线**不必看元素边界**，被切开也能机械兜底。
  · autojunk 必须关（默认会把高频词当垃圾跳过，长列表上会毁掉对齐——踩过）。

【多出来的词怎么分类（避免误报）】
  · 保留语气词词面：src 里保留了已删语气词的词（如引语里的 "Uh"）→ 提示（正常）；
  · 口吃/重复词：多出来的词与相邻已匹配词相同（like really— really）→ 提示（良性）；
  · 其余：真正的"多写/改写"→ 注意（需人工确认），但 replace 块内的词数变化属正常修正。

【制作过程要点 / 坑】
  1. src 仍应是元素文本的**忠实拼接**（书写层规整/听写修正可以），本脚本会报出不对应处；
  2. 语气词必须先由 filler_scan.py 扫出（否则语气词会挤进对齐、制造多余的 delete 块）；
  3. 未覆盖元素 > 0 时必须人工看：多半是"AI 删了内容词"或"新形态语气词"（补进 dropped 或补回行里）。
"""
import glob
import json
import os
import re
import sys
from collections import Counter
from difflib import SequenceMatcher

TOKEN_RE = re.compile(r"[A-Za-z0-9']+|[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]")


def tokens(s):
    return TOKEN_RE.findall((s or '').lower())


def to_ranges(ids):
    ids = sorted(set(ids))
    out = []
    for i in ids:
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return out


def main():
    wd = sys.argv[1]
    fix = '--fix' in sys.argv
    els = json.load(open(os.path.join(wd, 'elements.json'), encoding='utf-8'))['elements']
    by_i = {e['i']: e['w'].strip() for e in els}

    def ectx(eid, n=4):
        """元素编号前后各 n 个词（目标用 ▸◂ 标出）——stream.txt 无编号，报表按上下文定位"""
        lo, hi = max(0, eid - n), min(len(els) - 1, eid + n)
        return ' '.join((f'▸{by_i.get(j, "")}◂' if j == eid else by_i.get(j, '')) for j in range(lo, hi + 1))
    dropped = set()
    for fn in ('dropped.json', 'fillers_auto.json'):
        p = os.path.join(wd, fn)
        if os.path.isfile(p):
            d = json.load(open(p, encoding='utf-8'))
            if 'dropped' in d:
                dropped = set(d['dropped'])
                break
    # 元素词流 t（剔除语气词）；同时记住"被删语气词"的词面，用于分类"保留语气词"
    t_tokens, t_elem = [], []
    drop_tokens = {}
    for e in els:
        if e['i'] in dropped:
            for tk in tokens(e['w']):
                drop_tokens.setdefault(tk, []).append(e['i'])
            continue
        for tk in tokens(e['w']):
            t_tokens.append(tk)
            t_elem.append(e['i'])

    # 行文本：优先 lines_text.jsonl；否则按序号合并 lines_text_p*.jsonl（分块写法）；
    # 都没有 → 用已带 ids 的 lines_p*.jsonl（校验模式）
    tp = os.path.join(wd, 'lines_text.jsonl')
    if os.path.isfile(tp):
        text_paths = [tp]
    else:
        text_paths = sorted(glob.glob(os.path.join(glob.escape(wd), 'lines_text_p*.jsonl')),
                            key=lambda p: int(re.search(r'_p(\d+)\.jsonl$', p).group(1))
                            if re.search(r'_p(\d+)\.jsonl$', p) else 10 ** 9)
    from_text = bool(text_paths)
    if from_text:
        rows = [json.loads(l) for p in text_paths for l in open(p, encoding='utf-8') if l.strip()]
        for r in rows:
            r.pop('ids', None)
    else:
        rows = []
        for p in sorted(glob.glob(os.path.join(glob.escape(wd), 'lines_p*.jsonl'))):
            rows += [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
    if not rows:
        print(f'[错误] 在 {wd} 找不到行文本（lines_text.jsonl / lines_text_p*.jsonl / lines_p*.jsonl 都没有）')
        sys.exit(1)

    s_tokens, s_line = [], []
    for idx, r in enumerate(rows):
        for tk in tokens(r.get('src', '')):
            s_tokens.append(tk)
            s_line.append(idx)

    if '--show' in sys.argv:
        n = int(sys.argv[sys.argv.index('--show') + 1])
        r = rows[n - 1]
        ids = r.get('ids') or [[]]
        lo, hi = (ids[0][0] if ids and ids[0] else 0), (ids[-1][1] if ids and ids[0] else 0)
        print(f'#{n} ids={r.get("ids")}\n  zh = {r["zh"]}\n  src= {r["src"]!r}')
        if ids and ids[0]:
            print(f'  区间内元素: {" ".join(by_i.get(i, "") for i in range(lo, hi + 1))!r}')
            print('  邻域元素: ' + ' '.join(f'[{i}]{by_i.get(i, "")}' for i in range(max(0, lo - 8), min(len(by_i), hi + 9))))
        return

    sm = SequenceMatcher(None, t_tokens, s_tokens, autojunk=False)
    own = {}                                     # 元素编号 → Counter{行号: 词数}
    uncovered, extra_raw, kept_filler, repeated = [], [], [], []
    eq = rep = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                own.setdefault(t_elem[i1 + k], Counter())[s_line[j1 + k]] += 1
            eq += i2 - i1
        elif tag == 'replace':
            m = min(i2 - i1, j2 - j1)
            for k in range(m):
                own.setdefault(t_elem[i1 + k], Counter())[s_line[j1 + k]] += 1
            rep += m
            for k in range(i1 + m, i2):
                uncovered.append((t_elem[k], by_i.get(t_elem[k], '')))
            for k in range(j1 + m, j2):
                extra_raw.append((s_line[k] + 1, s_tokens[k], i1))
        elif tag == 'delete':
            for k in range(i1, i2):
                uncovered.append((t_elem[k], by_i.get(t_elem[k], '')))
        else:  # insert
            for k in range(j1, j2):
                extra_raw.append((s_line[k] + 1, s_tokens[k], i1))

    # 多出来的词：分类（保留语气词 / 口吃重复 / 真·多写改写）；ctx=相邻词上下文（报表定位用）
    extra = []
    for ln, tk, i1 in extra_raw:
        prev_t = t_tokens[i1 - 1] if i1 > 0 else None
        next_t = t_tokens[i1] if i1 < len(t_tokens) else None
        ctx = ' '.join(x for x in (prev_t, f'▸{tk}◂', next_t) if x)
        if tk in drop_tokens:
            kept_filler.append((ln, tk, drop_tokens[tk][:3], ctx))
        elif tk == prev_t or tk == next_t:
            repeated.append((ln, tk))
        else:
            extra.append((ln, tk, ctx))

    # 元素级归属：被切开的多词元素整体归词数多的一侧（平手归前行）
    conflicts = []
    for e, c in own.items():
        if len(c) > 1:
            best = max(c.items(), key=lambda kv: (kv[1], -kv[0]))[0]
            conflicts.append((e, by_i.get(e, ''), dict(c), best))
    line_set = {i: set() for i in range(len(rows))}
    for e, c in own.items():
        line_set[max(c.items(), key=lambda kv: (kv[1], -kv[0]))[0]].add(e)

    # 纯符号元素（无词，如 "—" ","）：落在空白处时机械归**前一行**（保证编号覆盖完整；有词的孔洞不动，交给[注意]报告）
    covered = set().union(*line_set.values()) if line_set else set()
    punct_attached = []
    for e in els:
        i = e['i']
        if i in dropped or i in covered or tokens(e['w']):
            continue
        target = None
        for j in range(i - 1, -1, -1):
            hit = [lx for lx, s in line_set.items() if j in s]
            if hit:
                target = max(hit)
                break
        if target is None:
            for j in range(i + 1, len(els)):
                hit = [lx for lx, s in line_set.items() if j in s]
                if hit:
                    target = min(hit)
                    break
        if target is not None:
            line_set[target].add(i)
            punct_attached.append((i, by_i.get(i, ''), target))

    out = []
    for idx, r in enumerate(rows):
        out.append({'ids': to_ranges(line_set[idx]), 'src': r.get('src', ''), 'zh': r.get('zh', '')})

    total = max(1, eq + rep + len(uncovered) + len(extra) + len(kept_filler) + len(repeated))
    print(f'行数 {len(rows)}｜对齐率 {(eq + rep) * 100 / total:.1f}%（equal {eq} / replace {rep}）')
    bad = [k + 1 for k, r in enumerate(out) if not r['ids']]
    if bad:
        print(f'[错误] {len(bad)} 行完全对不上（src 不在元素流里？）：{bad[:10]}')
    if conflicts:
        print(f'[提示] {len(conflicts)} 个多词元素被切线切进内部——已整体归词多的一侧（平手归前行）：')
        for e, w, c, best in conflicts[:8]:
            print(f'   [{e}] {w!r}：行 {c} → 归第 {best + 1} 行')
    if punct_attached:
        print(f'[提示] {len(punct_attached)} 个纯符号元素已机械归入相邻行（不影响文本）：')
        for i, w, t in punct_attached[:8]:
            print(f'   [{i}] {w!r} → 第 {t + 1} 行')
    if uncovered:
        print(f'[注意] {len(uncovered)} 个元素未被任何行覆盖（内容词漏掉？前 15；▸◂ 为目标词）：')
        for i, w in uncovered[:15]:
            print(f'   [{i}] {w} ｜ …{ectx(i)}…')
    if kept_filler:
        print(f'[提示] {len(kept_filler)} 处保留了已删语气词的词面（引语/强调，正常）：')
        for ln, tk, ids, ctx in kept_filler[:8]:
            print(f'   第 {ln} 行: {tk!r}（语气词编号 {ids}；{ctx}）')
    if repeated:
        print(f'[提示] {len(repeated)} 处口吃/重复词（良性）：{repeated[:8]}')
    if extra:
        print(f'[注意] {len(extra)} 个词在元素流里找不到（真·多写/改写？前 15；▸◂ 为目标词）：')
        for ln, tk, ctx in extra[:15]:
            print(f'   第 {ln} 行: {tk!r} ｜ {ctx}')
    # 与现有 ids 的差异（校验模式）
    if not from_text:
        diff = [(k + 1, rows[k].get('ids'), out[k]['ids']) for k in range(len(rows))
                if rows[k].get('ids') and rows[k]['ids'] != out[k]['ids']]
        if diff:
            print(f'[可能漂移] {len(diff)} 行：现 ids → 机械绑定 ids（前 10）')
            for k, a, b in diff[:10]:
                print(f'   #{k}: {a} → {b}')
        else:
            print('现 ids 与机械绑定一致 ✓（无漂移）')
    if fix:
        p1 = os.path.join(wd, 'lines_p1.jsonl')
        for p in sorted(glob.glob(os.path.join(glob.escape(wd), 'lines_p*.jsonl'))):
            with open(os.path.join(wd, 'prebind_' + os.path.basename(p)), 'w', encoding='utf-8', newline='\n') as f:
                f.write(open(p, encoding='utf-8').read())
        with open(p1, 'w', encoding='utf-8', newline='\n') as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        print(f'已写出 {p1}（{len(out)} 行，ids 为机械绑定）')


if __name__ == '__main__':
    main()
