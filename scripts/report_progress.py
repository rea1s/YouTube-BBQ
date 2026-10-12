#!/usr/bin/env python3
"""report_progress.py — 进度"报点器"（常驻一个，按进度反馈；完成/失败主动总结）

【两条规则】
  1. **每 5s（--interval）报一行进度**——持续打印在本任务的输出里（长这样：
     `[报点] XX.part 已下 210.5MB（38%）｜本段 2.31 MB/s｜平均 2.05 MB/s｜预计剩余 6.5 分钟`）；
  2. **完成或失败时主动出一次总结并退出**——退出即任务通知（=中断）。判定：
     · 完成：`--done-file` 出现（推荐直接传成品文件路径，如 `<名称>.mp4`）或监视目标消失；
     · 失败①**停滞**：默认 240s 零增长（`--stall`）；
     · 失败②**持续低速**：默认连续 5 次报点本段 <100 KB/s（`--slow-times` / `--slow-kbps`，
       设 `--slow-times 0` 关闭）——"疑似限速/停滞"，下载可能仍在缓慢继续。
  总结行含：总大小 / 平均速率 / 总耗时 / 状态（✅完成 / ❌疑似失败）。

【用法】python report_progress.py <目录或文件> [--interval 5] [--stall 240]
        [--wait 15] [--slow-times 5] [--slow-kbps 100] [--done-file PATH] [--grow-stop SEC] [--total BYTES]
  - 目标：正在增长的文件（如 `.part`）；给目录则自动取目录里最大的 *.part/*.mp4/*.mkv/*.webm。
  - **目标可以还不存在**：下载器建 `.part` 有延迟，本脚本会先等（默认最多 `--wait` 15s，
    期间按节拍打印"等待目标文件出现…"）；等不到才以"❌ 未出现"退出。
    **本技能下载流程给 `--wait 150`**——探测阶段需数十秒（元数据/选轨/字幕）才建 `.part`，默认 15s 会误报"未出现"。
  - 目录模式**优先盯正在写入的 `.part`**（复下进既有目录时不被旧成品文件带偏）；
    有 `--done-file` 且未出现时，静止的旧文件**不会被当"已完成"**——它在等新 `.part` 出现并改盯。

【在流水线里怎么用】
  挂**一个**报点器跑到底（常驻一个，不是分次重挂）：它的进度行持续可见（用户/任务面板都能看）；
  下载本体另有自己的完成/失败通知（双保险）。AI 收到报点器退出通知后 → 把进度与结果转述用户；
  需要中途目视进度时**直接读它的输出**（非阻塞，不是 sleep）。

【制作过程要点 / 坑】
  1. 机制本质：本环境里"后台任务退出"才是可送达的中断——所以"常驻的进度"走它的输出，
     "完成/失败"走它的退出；等待全部在后台，AI 自己不 sleep；
  2. 三个时钟各自独立：全局（平均速率/耗时）、报告节拍（每 interval 一行）、
     最后增长（停滞判定）；**报告节拍不得复位停滞时钟**；
  3. **目标文件不存在 ≠ 已完成**：必须等待出现（否则会在下载刚启动时误发"完成"中断）；
  4. 低速判定用「本段速率」（与报点行显示一致）连续计数，不是平均值；**且只在见过增长之后才计数**
     （监视开始时目标就已静止时不按低速报）；
  3b. **压制场景**：输出文件**从第一秒就在写**——① 目标请给**输出文件路径**（给目录会盯上目录里最大的文件，
      通常是原片）；② **不要把 --done-file 指到输出文件**（它一开始就存在 → 会立刻误报完成；脚本检测到同路径会
      忽略并提示）；③ 判完成用 **`--grow-stop <秒>`**（停止增长即视为写完）或 burn 任务自身的退出通知；
  4b. **静止的成品文件不算失败**：非 .part/.ytdl 的目标若在监视开始时已存在、且 30s 内无增长 →
     按「✅ 已完成（监视开始时文件已存在且静止）」报告；.part 静止仍按停滞判失败；
  5. 它只看文件大小，不碰下载进程、无副作用；写临时名再改名的下载器会推迟"完成"判定；
  6. 路径含 `[]` 的 Git Bash 参数**不会被 MSYS 自动转换**（本技能项目文件夹名固定带 `[]`）——
     脚本内置 `_win_path` 兜底（`/c/…` → `C:/…`）。
"""
import glob
import os
import re
import sys
import time


def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def _win_path(p):
    """Git Bash 的 /c/... 风格路径 → C:/... 兜底转换。
    **带 [] 等字符的参数不会被 MSYS 自动转换**，而本技能的项目文件夹名恰是
    `[制作者] 标题-YouTube` 格式——不兜底就会出现"路径存在但脚本找不到"的假象。"""
    if p and len(p) >= 3 and p[0] == '/' and p[2] == '/' and p[1].isalpha():
        return p[1].upper() + ':' + p[2:]
    return p


def pick(target):
    if os.path.isfile(target):
        return target
    esc = glob.escape(target)
    # 优先正在写入的 .part：**复下进既有目录时不被旧成品文件带偏**（旧 mp4 可能更大）
    parts = glob.glob(os.path.join(esc, '*.part'))
    if parts:
        return max(parts, key=os.path.getsize)
    best, size = None, -1
    for pat in ('*.mp4', '*.mkv', '*.webm'):
        for f in glob.glob(os.path.join(esc, pat)):
            s = os.path.getsize(f)
            if s > size:
                best, size = f, s
    return best


def mb(n):
    return n / 1048576


def main():
    target = _win_path(sys.argv[1])
    interval = float(opt('--interval', 5))
    stall = float(opt('--stall', 240))
    wait = float(opt('--wait', 15))
    slow_times = int(opt('--slow-times', 5))       # 0 = 关闭低速判定
    slow_kbps = float(opt('--slow-kbps', 100))
    done_file = _win_path(opt('--done-file'))
    grow_stop = float(opt('--grow-stop', 0))   # 见过增长后，无增长达该秒数 → 视为完成（适合压制输出等"写完就停"的文件）
    total = int(opt('--total', 0))

    # ---- 护栏：--done-file 不能与被监视文件是同一路径 —— 压制输出从第一秒就在写，它在写入中就"存在"，
    #      "文件出现"不等于"任务完成"，会立刻误报完成。这种场景请用 --grow-stop 或任务通知判完成。----
    if done_file and os.path.realpath(done_file) == os.path.realpath(target):
        print(f'[提示] --done-file 与被监视文件是同一个（{os.path.basename(done_file)}）——它在写入开始时就存在，'
              f'不能当完成信号；本次忽略 done-file，请用 --grow-stop <秒> 或任务自身通知判完成', flush=True)
        done_file = None

    # ---- 等目标文件出现（下载器建 .part 有延迟；不存在 ≠ 已完成）----
    f, t_wait = pick(target), time.time()
    next_wait_line = t_wait + interval
    while not f:
        if done_file and os.path.isfile(done_file):
            print(f'[报点 ✅ 完成] 成品已出现：{os.path.basename(done_file)}', flush=True)
            return
        if time.time() - t_wait >= wait:
            print(f'[报点 ❌ 未出现] 等了 {wait:.0f}s 仍没找到可监视的文件（{target}）——'
                  f'下载可能未启动/已失败，或路径不对', flush=True)
            return
        time.sleep(min(1.0, interval))
        f = pick(target)
        if not f and time.time() >= next_wait_line:
            print(f'[报点] 等待目标文件出现…（已等 {time.time() - t_wait:.0f}s）', flush=True)
            next_wait_line = time.time() + interval

    t0 = time.time()
    s0 = os.path.getsize(f)
    last_growth_t, last_growth_size = t0, s0   # 停滞时钟
    report_t, report_size = t0, s0             # 报告节拍时钟
    slow_run = 0                               # 连续低速计数
    grew_once = False                          # 是否见过任何增长（从未增长 = 静止文件，不按「低速」报）
    size, status = s0, '进行中'

    while True:
        time.sleep(min(2.0, interval))
        now = time.time()
        if done_file and os.path.isfile(done_file):
            status = '✅ 完成'
            size = os.path.getsize(done_file) or last_growth_size
            break
        if not os.path.isfile(f):              # .part 被合并清理
            status = '✅ 完成或已替换'
            size = last_growth_size
            break
        if done_file and os.path.realpath(done_file) == os.path.realpath(f):
            print(f'[提示] --done-file 与被监视文件是同一个（{os.path.basename(f)}）——忽略 done-file，改按增长/停增判定', flush=True)
            done_file = None
        size = os.path.getsize(f)
        if size > last_growth_size:
            last_growth_size, last_growth_t = size, now
            grew_once = True
        else:
            if grow_stop and grew_once and now - last_growth_t >= grow_stop:
                status = f'✅ 已完成（输出停止增长 {int(grow_stop)}s——写入/编码已结束）'
                break
            is_part = bool(re.search(r'\.(part|ytdl|temp|tmp)$', f, re.I))
            # 从未增长过的 .part → 按停滞判失败；**非 .part 的静止文件 → 30s 后按「已完成」报告**
            #（监视开始时就已存在且静止 = 下载早已结束/无需再等，不能误报失败）
            if not grew_once and not is_part and now - last_growth_t >= 30:
                if done_file and not os.path.isfile(done_file):
                    # 有完成标志（--done-file）却还没出现：**别把旧成品当"已完成"**——可能是在等
                    # 新下载开始（复下进既有目录）。出现 .part 就改盯它，否则继续等。
                    nf = pick(target)
                    if nf and os.path.realpath(nf) != os.path.realpath(f):
                        print(f'[报点] 改盯 {os.path.basename(nf)}（新下载写入中）', flush=True)
                        f = nf
                        t0 = time.time()
                        s0 = last_growth_t = last_growth_size = os.path.getsize(f)
                        report_t, report_size = t0, s0
                        slow_run, grew_once = 0, False
                        continue
                else:
                    status = '✅ 已完成（监视开始时文件已存在且静止——任务早已结束或无需再等）'
                    break
            if now - last_growth_t >= stall:
                status = f'❌ 疑似失败（{int(stall)}s 零增长）'
                break
        if now - report_t >= interval:         # 报告点：打一行，继续常驻（不动停滞时钟）
            inst = (size - report_size) / max(1e-6, now - report_t)
            avg = (size - s0) / max(1e-6, now - t0)
            pct = f'（{size * 100 / total:.0f}%）' if total else ''
            eta = ''
            if total and inst > 0 and size < total:
                eta = f'｜预计剩余 {(total - size) / inst / 60:.1f} 分钟'
            if slow_times and grew_once and not grow_stop:   # 低速只在「见过增长」后计数；grow-stop 模式（压制输出）不计低速
                slow_run = slow_run + 1 if inst < slow_kbps * 1024 else 0
                slow_tag = f'｜连续低速 {slow_run}/{slow_times}' if slow_run else ''
            else:
                slow_run, slow_tag = 0, ('' if grew_once else '｜静止（尚未见增长）')
            print(f'[报点] {os.path.basename(f)} 已下 {mb(size):.1f}MB{pct}'
                  f'｜本段 {inst / 1048576:.2f} MB/s｜平均 {avg / 1048576:.2f} MB/s{eta}{slow_tag}', flush=True)
            report_t, report_size = now, size
            if slow_times and slow_run >= slow_times:
                status = f'❌ 疑似限速或停滞（连续 {slow_times} 次本段 <{slow_kbps:.0f} KB/s；下载可能仍在缓慢继续）'
                break

    el = max(1e-6, time.time() - t0)
    rate = (size - s0) / el
    print(f'[报点 {status}] {mb(size):.1f}MB｜平均 {rate / 1048576:.2f} MB/s｜耗时 {el / 60:.1f} 分钟', flush=True)


if __name__ == '__main__':
    main()
