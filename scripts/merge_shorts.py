#!/usr/bin/env python3
"""merge_shorts.py — 短字幕合并（自动流水线，切分重排后的机械收尾之一）

【目的】
AI 在切分重排时可能留下"不足 4 字"的短字幕（招呼、应答等）。本脚本把满足条件的
相邻短字幕合并，减少"一闪而过"：
  条件：本条中文行宽 < 4 **且**合并后 ≤ 16 且 两条间隔（下条首元素 − 本条末元素）≤ 1.5s
  **这是"保下限"操作、应极少触发**：判断单位是"整句/句义单元"（逗号分句也算一句）——整句 ≥4 字
  一律不并；脚本只能看到已切好的行，**"让位于句义单元"由 AI 在切分环节负责**（脚本只兜 <4 的底）
  不级联：每条最多向后合并一次
约定义来源：设计规则 §三 步骤 3"短句合并"。

【用法】python merge_shorts.py <中间文件目录>
  读取：elements.json 与 lines_p*.jsonl（AI 写出的分片）
  产出：合并结果统一写回 lines_p1.jsonl；原始分片就地备份为 raw_<原名>（**同目录**，前缀 raw_ 不会
        被 lines_p*.jsonl 的 glob 匹配到）

【制作过程要点 / 坑】
  1. 只在**短**字幕上向后合并；合并处用「。」衔接（原文侧用 ". "），合并后的原文只做"压平成一行"，
     **不做机械折行**（折行由 AI 做——脚本折行有词内断行风险）；
  2. 备份文件名不能匹配 `lines_p*.jsonl`——用同目录 `raw_` 前缀；
  3. 合并条件里的间隔用 elements.json 的时间（= 下条首元素时间 − 本条末元素时间）；
  4. **坑**：目录名含 `[]`（默认命名必含中括号）时裸 glob 匹配不到分片——会**静默写出空结果**
     覆盖 lines_p1.jsonl！目录部分必须 `glob.escape(wd)`。
"""
import glob
import json
import os
import re
import shutil
import sys

GAP = 1500
ZH_MIN, ZH_MAX = 4, 16          # <4 才并（保下限）；合并后行宽 ≤16


def zh_len(s):
    """行宽计法——与 sub_style.zh_len 同规则：标点占位（只豁免行尾 ，。,.）；英文/数字每 2 个算 1；空格 0.5。"""
    s = re.sub(r'[，。,.]+$', '', (s or '').strip())
    return sum(0.5 if (c == ' ' or (c.isascii() and c.isalnum())) else 1.0 for c in s)


def flatten(t):
    return re.sub(r'\s+', ' ', t).strip()


def main():
    workdir = sys.argv[1] if len(sys.argv) > 1 else '.'
    paths = sorted(glob.glob(os.path.join(glob.escape(workdir), 'lines_p*.jsonl')))
    if not paths:
        print(f'[错误] 在 {workdir} 未找到 lines_p*.jsonl——**不写任何文件**'
              f'（防止用空结果覆盖分片）；请核对路径/文件名')
        sys.exit(1)
    lines = []
    for p in paths:
        lines += [json.loads(l) for l in open(p, encoding='utf-8') if l.strip()]
    elem = {e['i']: e['t'] for e in json.load(
        open(os.path.join(workdir, 'elements.json'), encoding='utf-8'))['elements']}

    out, log = [], []
    i = 0
    while i < len(lines):
        cur = lines[i]
        if zh_len(cur['zh']) < ZH_MIN and i + 1 < len(lines):
            nxt = lines[i + 1]
            L = elem[cur['ids'][-1][1]]
            S = elem[nxt['ids'][0][0]]
            if zh_len(cur['zh'] + nxt['zh']) <= ZH_MAX and (S - L) <= GAP:
                # 本条已以标点收尾（含「，」等句中逗号）时直接相接，避免拼出「，。」
                sep = '' if re.search(r'[，。！？…、,]$', cur['zh']) else '。'
                zh = cur['zh'] + sep + nxt['zh']
                src = None
                if cur.get('src') and nxt.get('src'):
                    src = re.sub(r'[,\.!?]+$', '', flatten(cur['src'])) + '. ' + flatten(nxt['src'])
                rec = {'ids': cur['ids'] + nxt['ids'], 'zh': zh}
                if src:
                    rec['src'] = src
                out.append(rec)
                log.append(f"合并: 「{cur['zh']}」 + 「{nxt['zh']}」 → 「{zh}」")
                i += 2
                continue
        out.append(cur)
        i += 1

    for p in paths:
        base = os.path.basename(p)
        shutil.copy(p, os.path.join(workdir, 'raw_' + base))
        if base != 'lines_p1.jsonl':
            os.remove(p)
    with open(os.path.join(workdir, 'lines_p1.jsonl'), 'w', encoding='utf-8', newline='\n') as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    print(f'合并 {len(log)} 处，字幕 {len(lines)} → {len(out)} 条（原始分片已备份为 raw_lines_p*.jsonl）')
    for m in log:
        print(' ', m)


if __name__ == '__main__':
    main()
