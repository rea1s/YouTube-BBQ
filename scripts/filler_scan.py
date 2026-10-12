#!/usr/bin/env python3
"""filler_scan.py — 语气词候选扫描（"丢弃清单不许手写"的机械记账工具）

【目的】"丢弃清单不许手写"的机械记账工具：手写"已删语气词编号"容易整段错位——
把内容词当语气词删掉、真正的语气词留在行里，而"编号集合完整、空洞 ⊆ 清单"这类
旧式校验对它是盲的。
本脚本把语气词编号变成**元素表机械扫出**：AI 只做"增删确认"（保留少数有意保留的），
不再手写任何编号。

【用法】python filler_scan.py <中间文件目录> [--keep 12,34] [--extra 56] [--write-dropped]
  读 elements.json → 扫出候选（只匹配"整元素"形态）→ 写 fillers_auto.json（候选明细）
  --write-dropped：同时写出 dropped.json（= 候选 − keep + extra；已有 notes 会保留）
  --keep  ：故意保留、不算丢弃的候选编号（逗号分隔）
  --extra ：候选之外、仍要算丢弃的编号（逗号分隔）

【制作过程要点 / 坑】
  1. 词表：um/uh/umm/uhh/ah/ahh/er/erm/hmm/mhm/mm/mm——**只认整元素**（词首词中的 uh 不碰）；
  2. build_auto 会做**反向校验**：丢弃清单里的每个编号在元素表里必须真的是语气词，
     否则直接报错并打印该编号的实际词——这类"差一位/错删"会被第一时间抓住；
     （非标准词（如 "you know"）确要丢弃 → 用 `--extra` 登记：会写进 fillers_auto.json 与 notes 留痕，③b 据此放行）；
  3. 顺序：**先跑本脚本再开始分句翻译**（规则：删语气词时编号同步剔除——现在编号由机器给）。
"""
import json
import os
import re
import sys

FILLERS = {'um', 'uh', 'umm', 'uhh', 'uhhh', 'ah', 'ahh', 'er', 'erm', 'hmm', 'mhm', 'mm', 'mmm'}


def norm(s):
    return re.sub(r'[^a-z]', '', (s or '').lower())


def main():
    wd = sys.argv[1]
    keep = set(int(x) for x in sys.argv[sys.argv.index('--keep') + 1].split(',')) if '--keep' in sys.argv else set()
    extra = set(int(x) for x in sys.argv[sys.argv.index('--extra') + 1].split(',')) if '--extra' in sys.argv else set()
    els = json.load(open(os.path.join(wd, 'elements.json'), encoding='utf-8'))['elements']
    cand = [{'i': e['i'], 'w': e['w'].strip()} for e in els if norm(e['w']) in FILLERS]
    json.dump({'candidates': cand, 'kept': sorted(keep), 'extra': sorted(extra)},
              open(os.path.join(wd, 'fillers_auto.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f'候选语气词 {len(cand)} 处 → fillers_auto.json（AI 可增删确认后 --write-dropped）')
    if '--write-dropped' in sys.argv:
        notes = []
        dp = os.path.join(wd, 'dropped.json')
        if os.path.isfile(dp):
            try:
                notes = json.load(open(dp, encoding='utf-8')).get('notes', [])
            except Exception:
                notes = []
        notes = [n for n in notes if not n.startswith('去噪')]
        notes.insert(0, f'去噪：删独立语气词共 {len(cand)} 处候选（编号由 filler_scan.py 从元素表机械扫出）')
        if extra:
            notes.insert(1, f'额外丢弃（--extra 登记，留痕备查）：{sorted(extra)}')
        dropped = sorted({c['i'] for c in cand} - keep | extra)
        json.dump({'dropped': dropped, 'notes': notes},
                  open(dp, 'w', encoding='utf-8', newline='\n'), ensure_ascii=False, indent=1)
        print(f'dropped.json 已写出：{len(dropped)} 个（保留 {len(keep)}、额外 {len(extra)}）')


if __name__ == '__main__':
    main()
