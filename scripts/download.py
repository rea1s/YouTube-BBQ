#!/usr/bin/env python3
"""download.py — YouTube 视频/字幕下载（探测 + 选轨 + 下载）

【目的】
一条命令完成：探测视频信息 → 按三级规则选字幕轨 → 下载视频（可选）+ 字幕（json3）→
建好目录结构、写 decision.json（后续环节据此工作）。

【用法】
  python download.py <url> [--base DIR] [--quality 1080p60] [--no-video]
                     [--vcodec av1|vp9|h264|any]
                     [--proxy URL] [--proxy-mode external|internal]
                     [--cookies FILE] [--track KEY] [--check-only]
  - --base        输出根目录（默认当前目录），视频文件夹建在其下
  - --quality     1080p60(默认) / 1080p / 720p / best
  - --vcodec      视频编码偏好（默认 av1——但**默认下载走 HLS，实得 VP9**；明确 av1 时自动改走 DASH 客户端）：vp9 / h264 / any(不限)
  - --no-video    "只要字幕并翻译"：不下视频
  - --proxy       显式指定代理（如 http://127.0.0.1:7897）
  - --proxy-mode  external(默认，清 *_PROXY 环境并走本机代理) / internal(保留环境注入的隧道)
  - --track       强制指定字幕轨键（隐藏项，通常由用户与 AI 交流后使用）
  - --check-only  只探测选轨，不下载

【输入输出】
  输入：YouTube 链接；
  输出（**扁平结构：全部文件直接放在 <base>/<视频名称>/ 下，不再套子目录**）：
    <基名>.<ext>          视频（--no-video 时无）——基名 = 视频标题（不含 [作者] 前缀，文件夹已带）
    <基名>.json3          字幕原始文件
    probe.json            探测信息（含全部轨道）
    decision.json         选轨决策（track/kind/action/警告；含 basename 供后续步骤用）
  （约定：原片=<名称>.mp4、硬字幕=<名称>.hardsub.mp4、软字幕=<名称>.softsub.mkv、
    字幕=<名称>.ass、报告=<名称>.报告.md / <名称>.机检报告.md）

【制作过程要点 / 踩坑记录】
  1. 依赖 yt-dlp + yt-dlp-ejs + curl-cffi + node；缺 curl-cffi 会出 403 "Only images available"；
     JS 挑战需 --js-runtimes node（缺 yt-dlp-ejs 会报 Signature/n challenge solving failed）；
  2. 代理：默认外部代理（清 `*_PROXY` 环境变量防隧道劫持 + 显式 --proxy）；
     探测顺序 7897→7890→7891→7898→1080→1087；找不到要提醒用户开代理/换内网模式；
  3. 格式提取存在**瞬时 403**（同一视频重试即好）→ 探测和下载均内建重试；
  4. **下载方式默认 HLS 分片**（hlsnative 每片独立请求 + `-N 8` 并发）——HLS 无 AV1 轨 → 实得 **VP9**；
     备用：默认客户端 → web_embedded（DASH 客户端路径，可出 AV1）；`--vcodec av1` 明确指定时自动改走客户端优先；
     音频优先 m4a/AAC；质量默认 1080p60；VP9/AV1 个别播放器、剪辑软件解码支持不足（切 h264 即可）；
  5. 选轨三级规则：①人工简体中文→直接用（**同时探测源语言轨：人工优先、无人工取自动**——
     记 track_src/src_kind、两轨一起下载，"中源双语"两轨原生直出，见设计规则 §4.3）；
     ②人工源语言轨→翻译；③自动 -orig→翻译；
     多 -orig（自动配音）用 info['language'] 消歧；判不了就报 "[需决策]" 退出，由 AI 决策；
  6. 文件夹命名 `[制作者] 标题-YouTube`（非法字符替换；取不到用视频 ID）；
  7. **坑（重要）**：YouTube 偶发瞬时降级（API 页 403 → "Only images are available"）时，
     yt-dlp 会**静默退到唯一可用的 360p 旧格式（format 18）且退出码为 0**——不校验就以为下好了。
     解决：下载后用 ffprobe 校验分辨率，低于目标 → 删除重试（最多 3 次），仍不达标记入 decision.json 的
     warnings（本次下载可重跑补下）；
  8. **坑**：部分视频的媒体流会被 YouTube 以 **403** 拒（元数据/字幕正常、下到 10~40MB 断，
     典型症状 `unable to download video data: HTTP Error 403`）——原因是 YouTube 对默认客户端强制
     SABR / 需 **PO Token**。**下载方式优先级（失败或低清都自动换下一种）**：
     ① **HLS 分片**（`protocol*=m3u8`，hlsnative 每片独立请求 + **`-N 8` 并发**——最稳，**默认**；
     HLS 无 AV1 轨 → 默认实得 VP9）→ ② 默认客户端 → ③ `player_client=web_embedded`（免 token）。
     三种都失败则记入 warnings、不再重试；**下载产物务必全片解码校验**
     （`ffmpeg -v error -i 文件 -f null -`，0 输出才算好）；
     另注意：**限速/长连接被掐断**是代理侧问题（小请求正常、大文件慢且断），与 403 不同。
  9. **重跑/复下**：**不覆盖、不删除已存在文件**——同名视频已存在时，本次下载自动保存为
     `<名称>(2).mp4`、`(3)`…（旧文件保留）；另注：yt-dlp 对**已存在的同名目标会跳过下载**
     （日志 "has already been downloaded"、退出码 0）；下载校验只认 `<名称>.mp4` **精确名**
     （不误抓 `.hardsub.mp4`/`.softsub.mkv`）。
"""
import json
import os
import re
import socket
import subprocess
import sys
import time

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_COOKIES = os.path.join(SKILL_DIR, 'secrets', 'cookies.txt')
PROXY_CANDIDATES = [7897, 7890, 7891, 7898, 1080, 1087]
ZH_SIMPLE = ['zh-Hans', 'zh-CN', 'zh-SG', 'zh']
ZH_TRAD = ['zh-Hant', 'zh-TW', 'zh-HK', 'zh-MO']


def parse_quality(q):
    """画质写法 → {cap, sort, label}；**认不出来返回 None（调用方必须报错，绝不静默回退默认画质）**。
    接受：best / 2160p / 1440p / 1080p60 / 1080p / 720p60 / 720p / 480p / 360p（数字可省 p、可带帧率）。"""
    s = (q or '').strip().lower()
    if s in ('best', 'max'):
        return {'cap': 0, 'fps': 0, 'sort': 'vcodec:h264,acodec:aac', 'label': 'best（不封顶）'}
    alias = {'4k': 2160, '2k': 1440, '8k': 4320, 'hd': 720, 'fhd': 1080}
    if s in alias:
        h = alias[s]
        return {'cap': h, 'fps': 0, 'sort': f'res:{h},vcodec:h264,acodec:aac', 'label': f'{h}p（{s}）'}
    m = re.fullmatch(r'(\d{3,4})\s*p?\s*(\d{2,3})?', s)
    if not m:
        return None
    h = int(m.group(1))
    if h < 144 or h > 4320:
        return None
    fps = int(m.group(2)) if m.group(2) else 0
    sort = f'res:{h}' + (f',fps:{fps}' if fps else '') + ',vcodec:h264,acodec:aac'
    return {'cap': h, 'fps': fps, 'sort': sort, 'label': f'{h}p' + (f'{fps}' if fps else '')}


def avail_map(info):
    """{高度: {帧率,…}}（只统计有画面的格式）"""
    out = {}
    for f in info.get('formats', []):
        h, fr = f.get('height'), f.get('fps')
        if f.get('vcodec') in (None, 'none') or not h:
            continue
        out.setdefault(h, set()).add(round(fr) if fr else 0)
    return out


def quality_available(qn, avail):
    """用户指定（或默认）的档位是否存在：高度必须在可用列表里；带帧率要求时该高度还要有 ≥ 该帧率的档。"""
    if not qn['cap']:
        return True
    if qn['cap'] not in avail:
        return False
    return (not qn['fps']) or any(f >= qn['fps'] for f in avail[qn['cap']])


def fmt_for(cap, client):
    """按**用户指定的封顶**拼格式选择器——每个下载方式都带 `[height<=cap]`。
    末尾的 `/bv*+ba/b` 只作"该视频没有 ≤cap 的档"时的退路（下载后校验、超上限则告警）。"""
    hc = f'[height<={cap}]' if cap else ''
    core = f'bv*{hc}+ba/b{hc}' if cap else 'bv*+ba/b'
    tail = '/bv*+ba/b' if cap else ''
    if client == 'hls':
        return f'bv*[protocol*=m3u8]{hc}+ba[protocol*=m3u8]/' + core + tail
    return core + tail


def arg(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def build_env(proxy_mode):
    env = dict(os.environ)
    if proxy_mode == 'external':
        for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy', 'NODE_OPTIONS'):
            env.pop(k, None)
    return env


def detect_proxy():
    for p in PROXY_CANDIDATES:
        try:
            with socket.create_connection(('127.0.0.1', p), timeout=0.5):
                return f'http://127.0.0.1:{p}'
        except OSError:
            continue
    return None


def probe_height(path):
    """ffprobe 读视频高度（失败返回 0）——用于校验'有没有被瞬时降级下成低清'。"""
    if not path or not os.path.isfile(path):
        return 0
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                            '-show_entries', 'stream=height', '-of', 'csv=p=0', path],
                           capture_output=True, text=True)
        return int((r.stdout or '').strip().splitlines()[0])
    except Exception:
        return 0


def ytdlp(args, env, proxy, retries=3, quiet=False):
    cmd = [sys.executable, '-m', 'yt_dlp', '--js-runtimes', 'node', '--no-warnings',
           '--retries', '10', '--fragment-retries', '10']
    if proxy:
        cmd += ['--proxy', proxy]
    cmd += args
    last = None
    for i in range(retries):
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode == 0:
            return r.stdout
        last = r
        if not quiet:
            print(f'  [重试 {i + 1}/{retries}] yt-dlp 返回 {r.returncode}：{(r.stderr or "").strip().splitlines()[-1] if r.stderr else ""}')
        time.sleep(5)
    print(f'[错误] yt-dlp 连续失败。stderr 末段：\n{(last.stderr or "")[-1500:]}')
    sys.exit(1)


def pick_track(info, force=None):
    manual = {k: v for k, v in (info.get('subtitles') or {}).items() if k != 'live_chat'}
    auto = info.get('automatic_captions') or {}
    warn = []
    if force:
        kind = 'manual' if force in manual else 'auto'
        direct = force in ZH_SIMPLE
        return dict(track=force, kind=kind, action='use-directly' if direct else 'translate'), warn

    src_lang = info.get('language') or ''
    if not src_lang:
        for f in info.get('formats') or []:
            if f.get('vcodec') == 'none' and f.get('language'):
                src_lang = f['language']
                break
    origs = [k for k in auto if k.endswith('-orig')]
    if not src_lang and len(origs) == 1:
        src_lang = origs[0][:-len('-orig')]

    def match(keys, lang):
        if not lang:
            return None
        if lang in keys:
            return lang
        base = lang.split('-')[0]
        for k in keys:
            if k.split('-')[0] == base and not k.endswith('-orig'):
                return k
        for k in keys:
            if k.split('-')[0] == base:
                return k
        return None

    # ① 人工简体中文 → 直接用；同时探测源语言轨（"中源双语"两轨原生直出，见设计规则 §4.3）：
    #    人工源语言轨优先；无人工则退**自动源语轨**（原文行本来就取自自动字幕）
    for k in ZH_SIMPLE:
        if k in manual:
            d = dict(track=k, kind='manual', action='use-directly')
            others = {kk: v for kk, v in manual.items()
                      if kk not in ZH_SIMPLE and kk not in ZH_TRAD}
            src_key = match(others, src_lang)
            if src_key:
                d.update(track_src=src_key, src_kind='manual')
            else:
                auto_key = None
                if src_lang:
                    if src_lang + '-orig' in auto:
                        auto_key = src_lang + '-orig'
                    else:
                        cand = ([kk for kk in auto if kk.endswith('-orig')]
                                or [kk for kk, v in auto.items()
                                    if not any('tlang=' in (f.get('url') or '') for f in v)])
                        auto_key = match(cand, src_lang)
                if auto_key:
                    d.update(track_src=auto_key, src_kind='auto')
                    warn.append(f'无人工源语言轨 → 原文行取自动源语轨 {auto_key}（ASR 文本，独立时间轴）')
                else:
                    warn.append('源语言轨人工/自动都没有匹配到——中源双语不可用（将按单中文；'
                                '原文行如需可 --track 指定）')
            return d, warn
    if any(k in ZH_TRAD for k in manual):
        warn.append(f'人工轨仅繁体（{[k for k in manual if k in ZH_TRAD]}），按②处理译成简体')

    # ② 人工字幕（源语言轨）→ 翻译
    if manual:
        k = match(manual, src_lang)
        if k:
            return dict(track=k, kind='manual', action='translate'), warn
        if len(manual) == 1:
            k = next(iter(manual))
            warn.append(f'源语言未知，人工轨唯一（{k}），按它翻译（AI 自检兜底）')
            return dict(track=k, kind='manual', action='translate'), warn
        print(f'[需决策] 人工字幕多轨且源语言未知：{sorted(manual)}（info language={src_lang!r}）')
        print('        请用 --track 指定其一后重跑')
        sys.exit(2)

    # ③ 自动字幕（-orig 优先）→ 翻译
    if origs:
        k = origs[0] if len(origs) == 1 else match(origs, src_lang)
        if not k:
            print(f'[需决策] 多个 -orig 轨：{origs}（info language={src_lang!r}），请用 --track 指定')
            sys.exit(2)
        if len(origs) > 1:
            warn.append(f'多个 -orig（自动配音视频），按源语言 {src_lang!r} 选中 {k}')
        return dict(track=k, kind='auto', action='translate'), warn
    if auto:
        k = match(auto, src_lang)
        if k:
            warn.append(f'自动轨无 -orig 标记，按源语言 {src_lang!r} 选中 {k}（AI 自检兜底）')
            return dict(track=k, kind='auto', action='translate'), warn
        no_tlang = [k for k, v in auto.items()
                    if not any('tlang=' in (f.get('url') or '') for f in v)]
        if len(no_tlang) == 1:
            return dict(track=no_tlang[0], kind='auto', action='translate'), warn
        print(f'[需决策] 无法判定原始自动轨：{sorted(auto)[:20]}…，请用 --track 指定')
        sys.exit(2)
    print('[错误] 该视频人工/自动字幕都没有——记入报告，考虑仅下视频')
    sys.exit(3)


def safe_name(s, fallback):
    s = re.sub(r'[\\/:*?"<>|%\x00-\x1f]', '_', s or '').strip().rstrip('.')
    return (s[:150] or fallback)


def main():
    a = sys.argv
    url = a[1]
    base = arg('--base', '.')
    quality = arg('--quality', '1080p60')
    no_video = '--no-video' in a
    proxy_mode = arg('--proxy-mode', 'external')
    force_track = arg('--track')
    force_client = arg('--client')            # 隐藏项：强制 yt-dlp player_client（如 web_embedded）
    check_only = '--check-only' in a
    cookies = arg('--cookies', DEFAULT_COOKIES)
    qn = parse_quality(quality)
    if qn is None:
        print(f'[错误] 不认识的 --quality 值：{quality!r}')
        print('       支持：best / 2160p / 1440p / 1080p60 / 1080p / 720p60 / 720p / 480p / 360p（数字可省 p、可带帧率）')
        print('       注：不认识的值**不会**再静默回退 1080p60——请改用上面的写法')
        sys.exit(1)
    # 视频编码偏好：默认 av1（同画质体积最小）——**但默认下载方式 HLS 不含 AV1 → 实际拿到 VP9**；
    # 明确 --vcodec av1 时自动改走 DASH 客户端（AV1 只在客户端路径有）
    vc_arg = arg('--vcodec')
    vcodec = (vc_arg or 'av1').strip().lower()
    vmap = {'av1': 'av01', 'av01': 'av01', 'vp9': 'vp9', 'vp09': 'vp9', 'h264': 'h264', 'avc1': 'h264', 'any': ''}
    if vcodec not in vmap:
        print(f'[错误] 不认识的 --vcodec 值：{vcodec!r}（支持 av1 / vp9 / h264 / any）')
        sys.exit(1)
    vcodec = vmap[vcodec]
    if vcodec != 'h264':
        toks = []
        for t in qn['sort'].split(','):
            if t == 'vcodec:h264':
                if vcodec:
                    toks.append(f'vcodec:{vcodec}')
            elif t:
                toks.append(t)
        qn['sort'] = ','.join(toks)
        qn['label'] += f' / {vcodec or "不限编码"}'
        print(f'[提示] 视频编码偏好：{vcodec or "不限"}'
              + ('' if vc_arg else '（默认走 HLS 分片 → 实得 VP9；要 AV1 用 --vcodec av1，会自动改走 DASH 客户端）'))
    print(f'[提示] 画质目标：{qn["label"]}——按用户指定**封顶**，不会下更高分辨率')

    proxy = arg('--proxy')
    if proxy_mode == 'external' and not proxy:
        proxy = detect_proxy()
        if not proxy:
            print('[错误] 未探测到本机代理端口（7897/7890/7891/7898/1080/1087 都没开）。')
            print('       请先启动你的代理软件，或用 --proxy 指定端口；习惯隧道环境可用 --proxy-mode internal。')
            sys.exit(1)
        print(f'[提示] 使用外部代理 {proxy}（可用 --proxy 更换；如需内网模式用 --proxy-mode internal）')

    env = build_env(proxy_mode)
    cookie_args = ['--cookies', cookies] if os.path.isfile(cookies) else []
    if not cookie_args:
        print(f'[提示] 未找到 cookie 文件 {cookies}——Cookie 是前置必做项：请先与用户交互获取'
              f'（问哪个浏览器已登录 YouTube → 提示关闭该浏览器 → CDP 提取），或用 --cookies <路径> 指定已有文件')

    # 1) 探测（重试内建）
    print('[*] 探测视频信息…')
    out = ytdlp(['-J', '--skip-download'] + cookie_args + [url], env, proxy)
    info = json.loads(out)

    # 1.5) 画质可用性（**交互项**）：指定/默认档位达不到就停下问用户，不擅自换档
    av = avail_map(info)
    if not quality_available(qn, av):
        alist = '、'.join(f"{h}p" + (f"{max(av[h])}" if max(av[h]) else '')
                          for h in sorted(av, reverse=True))
        print(f'[需决策] 该视频没有 {qn["label"]}；可用画质：{alist}')
        print('         请与用户确认要哪一档，然后用 --quality <档位> 重跑（可直接填上面的任一项，或 best 不封顶）')
        sys.exit(2)

    name = safe_name(f"[{info.get('uploader') or info.get('channel') or '未知作者'}] "
                     f"{info.get('title') or info.get('id')}-YouTube", info['id'])
    # 文件基名用标题（不带 [作者] 前缀——文件夹名已带）；后续 ass/hardsub/报告 都用它
    fname = safe_name(f"{info.get('title') or info.get('id')}", info['id'])
    workdir = os.path.join(base, name)
    os.makedirs(workdir, exist_ok=True)
    # 交付物（源视频/压制版/字幕/报告）放一级；**中间文件全部放二级「中间文件/」**
    mid = os.path.join(workdir, "中间文件")
    os.makedirs(mid, exist_ok=True)

    # 2) 选轨
    decision, warn = pick_track(info, force_track)
    files_json3 = [f for f in (info.get('subtitles', {}).get(decision['track'], []) +
                               info.get('automatic_captions', {}).get(decision['track'], []))
                   if f.get('ext') == 'json3']
    if not files_json3:
        warn.append('选中轨没有 json3 格式，将回退 srt（词级时间戳会缺失）')
    print(f"[*] 选轨：{decision['track']}（{decision['kind']}）→ {decision['action']}"
          + (f"；源语轨 {decision['track_src']}（{decision.get('src_kind', 'manual')}，原生双语用）"
             if decision.get('track_src') else '')
          + ('  [只要字幕模式]' if no_video else ''))

    dec = dict(id=info['id'], title=info.get('title'), uploader=info.get('uploader'), basename=fname,
               duration=info.get('duration'), language=info.get('language'),
               track=decision, quality=quality, no_video=no_video,
               workdir=os.path.abspath(workdir), middir=os.path.abspath(mid), warnings=warn,
               files={})
    json.dump(info, open(os.path.join(mid, 'probe.json'), 'w', encoding='utf-8'), ensure_ascii=False)

    if check_only:
        json.dump(dec, open(os.path.join(mid, 'decision.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print(json.dumps(dec, ensure_ascii=False, indent=1))
        return

    # 3) 字幕下载
    print('[*] 下载字幕…')
    subflags = ['--write-subs']
    if decision['kind'] == 'auto' or decision.get('src_kind') == 'auto':
        subflags.append('--write-auto-subs')
    sub_langs = decision['track'] + (',' + decision['track_src'] if decision.get('track_src') else '')
    ytdlp(['--skip-download'] + subflags + ['--sub-langs', sub_langs,
           '--sub-format', 'json3/srt',
           '-o', os.path.join(mid, fname + '.%(ext)s')] + cookie_args + [url], env, proxy)

    def find_sub(lang):
        for f in sorted(os.listdir(mid)):
            if ((f.startswith(fname) or f.startswith(info['id']))
                    and f.endswith(('.json3', '.srt')) and f'.{lang}.' in f):
                return f
        return None

    found = find_sub(decision['track'])
    if not found:                      # 兜底：命名不符时取第一个
        for f in os.listdir(mid):
            if f.startswith(fname) or f.startswith(info['id']):
                if f.endswith('.json3') or f.endswith('.srt'):
                    found = f
                    break
    dec['files']['subtitle'] = found
    if decision.get('track_src'):
        dec['files']['subtitle_src'] = find_sub(decision['track_src'])

    # 4) 视频下载（可选）
    if not no_video:
        cap = qn['cap']                      # 用户指定分辨率的**上限**（0 = best 不封顶）
        heights = sorted({f.get('height') for f in info.get('formats', [])
                          if f.get('vcodec') not in (None, 'none') and f.get('height')})
        has_le = (not cap) or any(h <= cap for h in heights)
        if cap and not has_le:
            warn.append(f'该视频没有 ≤{cap}p 的清晰度（可用：{heights}）——将按可用最高下载')
            print(f'    [警告] 该视频没有 ≤{cap}p 的档（可用 {heights}），将按可用最高下载')
        vpath, final_h = None, 0

        # 目标文件名：**不覆盖、不删除已存在文件**——同名视频已存在时，本次下载保存为 <名称>(2).mp4、(3)…
        out_fname = fname
        _n = 2
        while any(os.path.isfile(os.path.join(workdir, f'{out_fname}.{ext}'))
                  for ext in ('mp4', 'mkv', 'webm')):
            out_fname = f'{fname}({_n})'
            _n += 1
        if out_fname != fname:
            print(f'[提示] 目录里已有 {fname}.mp4 → 本次下载保存为 {out_fname}.mp4（旧文件保留不动）')

        def target_video():
            """本次下载目标文件 = <out_fname>.<ext>（只认精确名——不误抓 .hardsub.mp4 等同前缀派生文件）"""
            for ext in ('mp4', 'mkv', 'webm'):
                cand = os.path.join(workdir, f'{out_fname}.{ext}')
                if os.path.isfile(cand):
                    return cand
            return None

        # 下载方式优先级（见文件头坑 8）：① HLS 分片（默认；-N 8 并发；HLS 无 AV1 → 走 VP9）
        #   ② 默认客户端 ③ web_embedded。明确 --vcodec av1 时 AV1 只在客户端路径有 → 客户端优先。
        plan = ['hls', None, 'web_embedded']
        if vc_arg and vcodec == 'av01':
            plan = [None, 'web_embedded', 'hls']
        for attempt, client in enumerate(plan, 1):
            if force_client:
                client = force_client
            tag = {'hls': 'HLS 分片（并发 8）'}.get(client) or client or '默认客户端'
            print(f'[*] 下载视频（{quality}）…（第 {attempt}/{len(plan)} 次尝试：{tag}）')
            fmt = fmt_for(cap, client)       # 每个方式都带 [height<=cap]（用户指定封顶）
            extra = ([] if client == 'hls'
                     else (['--extractor-args', f'youtube:player_client={client}'] if client else []))
            sort_i, nfrag = qn['sort'], []
            if client == 'hls':
                nfrag = ['-N', '8']                       # 并发 8 片
                if vcodec == 'av01':                      # HLS 无 AV1 轨 → 默认改走 VP9
                    sort_i = sort_i.replace('vcodec:av01', 'vcodec:vp9')
            _pre = target_video()
            pre_stamp = os.path.getmtime(_pre) if _pre else None
            try:
                ytdlp(['-f', fmt, '-S', sort_i] + nfrag +
                      ['--merge-output-format', 'mp4',
                       '-o', os.path.join(workdir, out_fname + '.%(ext)s')] + extra + cookie_args + [url], env, proxy)
            except SystemExit:
                print(f'    [警告] 第 {attempt} 次下载失败（{tag}），换下一种方式')
                vpath = None
                continue
            vpath = target_video()
            if not vpath:
                print('    [警告] 本次尝试未产生视频文件，换下一种方式')
                continue
            fresh = pre_stamp is None or os.path.getmtime(vpath) != pre_stamp
            final_h = probe_height(vpath)
            if final_h and (not cap or final_h <= cap):        # 达标 = 有画面且**不超上限**
                print(f'    [ok] 视频{"已下载" if fresh else "已存在（yt-dlp 跳过下载）"}：{final_h}p（目标 {qn["label"]}）')
                break
            if final_h and cap and final_h > cap and not has_le:   # 该视频确无 ≤cap 的档 → 接受最高
                print(f'    [提示] 该视频没有 ≤{cap}p 的档，已取 {final_h}p')
                break
            if final_h and cap and final_h > cap:
                print(f'    [警告] 下到 {final_h}p 高于指定上限 {cap}p（选择器未守住）——删除重试')
            else:
                print(f'    [警告] 本次只下到 {final_h or "?"}p（目标 {qn["label"]}）——疑似 YouTube 瞬时降级，删除重试')
            if vpath:
                try:
                    os.remove(vpath)
                except OSError:
                    pass
            vpath = None
            time.sleep(5)
        if vpath:
            dec['files']['video'] = os.path.basename(vpath)
            dec['video_height'] = final_h
        else:
            warn.append('视频未能下载（HLS 分片/默认客户端/web_embedded 三种方式都失败）——建议稍后重试或更换网络/代理')
        if vpath and cap and final_h:
            if final_h < cap:
                warn.append(f'视频画质低于目标：实际 {final_h}p（目标 {cap}p，三种方式均已尝试）')
            elif final_h > cap:
                warn.append(f'视频画质高于指定：实际 {final_h}p（指定上限 {cap}p——该视频无 ≤{cap}p 的档）')

    json.dump(dec, open(os.path.join(mid, 'decision.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'[完成] 一级（交付物）：{workdir}')
    print(f'       二级（中间文件）：{mid}')
    print(f'        选轨 {decision["track"]}（{decision["action"]}）| 字幕 {dec["files"].get("subtitle")}'
          + (f' | 视频 {dec["files"].get("video")}' if dec['files'].get('video') else ''))
    for w in warn:
        print(f'        ⚠️ {w}')


if __name__ == '__main__':
    main()
