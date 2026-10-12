#!/usr/bin/env python3
r"""burn.py — 压制（硬字幕烧录） / 封装（软字幕） 

【目的】
把字幕落到视频上：默认**硬字幕**（烧进画面，任何播放器直接可见）；
可选**软字幕**（封装可开关的字幕轨，视频流零重编码）。

【用法】
  python burn.py --video <视频> --ass <字幕.ass> --out <输出> [--mode hard|soft]
        [--hw auto|nvenc|none] [--codec auto|av1|hevc|h264] [--bitrate BITS]
        [--preset fast] [--crf 20] [--quality-first] [--fontsdir <字体目录>]
        [--ffmpeg-args "<原样追加的 ffmpeg 参数>"]
  - hard（默认）：重编码 + 音频 copy
      · **输出编码优先级：AV1 → HEVC → H.264**；**编码能力判断交给 AI**——按硬件型号知识/现场查询
        决定编码档、用 `--codec` 传入（例：NVIDIA 20/30 系无 AV1 编码器 → `--codec hevc`；
        40/50 系 → `--codec av1`；拿不准就不传）。
      · **脚本不做任何能力探测**（不试编一帧、不查型号表）——**直接跑真实编码**：
        指定了就从该档开始、没指定按预估顺序；失败**逐级下退、只向低效率方向**（不反向升级），
        **最终 CPU 兜底只做 H.264**（libx264 `-preset fast -crf 20`；**不用 CPU 压 AV1/HEVC**）；
        逐级下退在日志中标明。
      · **跨编码码率匹配（关键）**：目标码率 = 源码率 × 1.2（重编码代际余量）× **EFF[输出]/EFF[源]**；
        EFF = "同画质所需码率（相对 h264=1.0）"：h264 1.0、hevc 0.75、vp9 0.8、av1 0.7（通用经验值）。
        例：AV1→HEVC ≈ ×1.29、AV1→H.264 ≈ ×1.71、**VP9→HEVC ≈ ×1.13、VP9→H.264 ≈ ×1.50**（VP9 默认源）、
        H.264→AV1 ≈ ×0.84、同编码 ×1.2。
        （**固定倍率会让"高压缩效率的源"在低效率编码下折损画质**——必须按 出/源 比值换算。）
      · 硬件档参数：`-rc vbr -b:v 目标 -maxrate 1.6× -bufsize 2×`；nvenc 再加
        `-preset p5 -tune hq -multipass qres -rc-lookahead 20 -spatial-aq 1 -aq-strength 8`
        （速度/质量折中：质量优先项在旧一代 NVENC 上耗时成倍增加、画质增益极小）；
        `--bitrate` 可显式覆盖目标码率；`--quality-first` = 纯质量模式（`-cq -b:v 0`，体积可达原片数倍）。
      · 强制：`--hw none`（只用 CPU H.264）/ `--codec av1|hevc|h264`（从该档开始逐级下退）。
      · **用户自带 ffmpeg 参数**：`--ffmpeg-args "-c:v libx265 -crf 22 -preset medium"`——追加在命令最后、
        同名参数以用户的为准（一般用于自定义编码器/参数；自带 `-vf` 会覆盖字幕滤镜，勿用）。
  - soft：-c copy 零重编码；mkv 装 ASS 轨（保留样式）/ mp4 装 mov_text（丢样式）

【交付提示（AV1 / HEVC 兼容性）】
  硬件档按优先级可能出 **AV1 或 H.265/HEVC**：VLC / mpv / PotPlayer 等现代播放器正常；
  **Windows 自带播放器可能需装相应解码扩展**，个别剪辑软件/手机/网页上传可能不支持——
  播放异常就 `--hw none`（CPU 档 H.264）重压。

【制作过程要点 / 坑】
  1. 硬压必须重编码视频流（无法无损）；音频一律 `-c:a copy` 不动；CPU 档速度随内容浮动
     （动画类明显快于实拍；分辨率/帧率越高越慢）——追求速度用硬件档；
  2. Windows 路径进 -vf 滤镜需转义（冒号 `\:`）——稳妥做法：把 ass 复制成固定安全名到临时
     目录、cwd 切过去、滤镜里只写相对文件名（避免中文/空格/逗号炸滤镜）；
  3. 字体用 `:fontsdir=` 指向 assets/fonts（改族名副本），不依赖系统安装；字体不对会**静默回退**，
     所以交付前应抽帧目视（preview_style.py 同机制）；
  4. mp4 容器 + opus/vorbis 音频会导致 `-c:a copy` 兼容问题：检测到就转 AAC（脚本已处理）；
  5. **软字幕（mkv）必须把字体作为附件打包**（`-attach` + mimetype）——ASS 里的族名是改族名副本、
     系统未安装，不附带则播放器回退错字体、样式走样；
  6. 输出后 ffprobe 校验收尾。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FONTSDIR = os.path.join(SKILL_DIR, 'assets', 'fonts')


def opt(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def run(cmd, cwd=None, exit_on_fail=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0 and exit_on_fail:
        print('[错误] ffmpeg 失败：\n' + (r.stderr or '')[-2000:])
        sys.exit(1)
    return r


def probe(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'stream=codec_type,codec_name', '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1', path],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.stdout.strip()


def src_bitrate(path):
    """源视频流码率（bps）；取不到（如 webm/片段无 bit_rate 字段）则按 文件大小/时长 估算。"""
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=bit_rate', '-of', 'default=noprint_wrappers=1:nokey=1', path],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        v = int(r.stdout.strip())
        if v > 0:
            return v
    except ValueError:
        pass
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                        '-of', 'default=noprint_wrappers=1:nokey=1', path],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    try:
        return int(os.path.getsize(path) * 8 / float(r.stdout.strip()) + 0.5)
    except (ValueError, ZeroDivisionError):
        return 0


def src_vcodec(path):
    """源视频流编码名（小写，如 h264 / hevc / vp9 / av1）；取不到返回空串。"""
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', path],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    return (r.stdout or '').strip().lower()


# ---- 编码链与码率匹配 ----
# 输出编码优先级：以硬件支持为前提，优先高压缩效率的格式（av1 > hevc > h264）。
CODEC_PRIORITY = ['av1', 'hevc', 'h264']
# 各硬件厂商的候选编码器（按此顺序**直接试跑**；失败换下一个）
HW_CANDIDATES = {
    'av1':  ['av1_nvenc', 'av1_qsv', 'av1_amf'],
    'hevc': ['hevc_nvenc', 'hevc_qsv', 'hevc_amf'],
    'h264': ['h264_nvenc', 'h264_qsv', 'h264_amf'],
}
# EFF：同画质所需码率（相对 h264 = 1.0）。跨编码换算 = EFF[输出]/EFF[源]；HEADROOM 为重编码代际余量。
EFF = {'h264': 1.0, 'hevc': 0.75, 'vp9': 0.8, 'av1': 0.7, 'vp8': 0.9, 'mpeg4': 1.1}
HEADROOM = 1.2


def rate_factor(src_vc, out_codec):
    """跨编码倍率：源码率 × 该倍率 = 目标码率。同编码即 HEADROOM(1.2)。"""
    return HEADROOM * EFF.get(out_codec, 1.0) / EFF.get(src_vc, 1.0)


def main():
    video, ass, out = opt('--video'), opt('--ass'), opt('--out')
    mode = opt('--mode', 'hard')
    preset = opt('--preset', 'fast')
    crf = opt('--crf', '20')
    hw_req = opt('--hw', 'auto')         # auto | nvenc | none
    cd_req = opt('--codec', 'auto')      # auto | h264 | hevc
    fontsdir = opt('--fontsdir', DEFAULT_FONTSDIR)
    extra_args = (opt('--ffmpeg-args') or '').split()   # 用户自带 ffmpeg 参数（追加在最后；同名以用户的为准）
    if not (video and ass and out):
        print(__doc__)
        sys.exit(2)
    if not os.path.isfile(ass) or not os.path.isfile(video):
        print(f'[错误] 输入不存在：video={video} ass={ass}')
        sys.exit(1)

    out_abs = os.path.abspath(out)
    os.makedirs(os.path.dirname(out_abs) or '.', exist_ok=True)

    if mode == 'hard':
        # 彻底免疫滤镜路径转义：ass 与字体都复制进临时目录，滤境只用相对路径（无冒号、无空格风险）
        tmp = tempfile.mkdtemp(prefix='burn_')
        try:
            shutil.copy(ass, os.path.join(tmp, '_sub.ass'))
            os.makedirs(os.path.join(tmp, 'fonts'), exist_ok=True)
            for fn in os.listdir(fontsdir):
                if fn.lower().endswith(('.ttf', '.otf', '.ttc')):
                    shutil.copy(os.path.join(fontsdir, fn), os.path.join(tmp, 'fonts', fn))
            vf = 'ass=_sub.ass:fontsdir=fonts'
            # 音频兼容：mp4 目标遇 opus/vorbis 则转 AAC
            info = probe(video)
            audio_opts = ['-c:a', 'copy']
            if out_abs.lower().endswith('.mp4') and re.search(r'codec_name=(opus|vorbis)', info):
                audio_opts = ['-c:a', 'aac', '-b:a', '192k']
                print('[提示] 音频为 opus/vorbis，mp4 兼容性差，改转 AAC')
            # ---- 编码档见下方：AI 判断（--codec）→ 脚本直接跑、失败逐级下退 ----
            src_br = src_bitrate(video)
            src_vc = src_vcodec(video) or 'h264'
            qf = '--quality-first' in sys.argv

            def hw_venc(codec, enc, br):
                """硬件档参数集：统一 -rc vbr + 码率三件套；nvenc 另加调优项（qsv/amf 用稳妥子集）。"""
                args = ['-c:v', enc]
                if codec == 'hevc':
                    args += ['-profile:v', 'main', '-tag:v', 'hvc1']
                elif codec == 'h264':
                    args += ['-profile:v', 'high']
                if qf:                                     # 纯质量：体积可能数倍于原片
                    return args + (['-preset', 'p6', '-tune', 'hq'] if enc.endswith('nvenc') else []) \
                        + ['-rc', 'vbr', '-cq', crf, '-b:v', '0']
                args += ['-rc', 'vbr', '-b:v', str(br), '-maxrate', str(int(br * 1.6)),
                         '-bufsize', str(int(br * 2)), '-pix_fmt', 'yuv420p']
                if enc.endswith('nvenc'):
                    # 速度/质量折中（质量优先项在旧一代 NVENC 上耗时成倍增加、画质增益极小）
                    args += ['-preset', 'p5', '-tune', 'hq', '-multipass', 'qres', '-rc-lookahead', '20',
                             '-spatial-aq', '1', '-aq-strength', '8']
                    if codec in ('h264', 'hevc'):
                        args += ['-bf', '3', '-b_ref_mode', 'middle']
                return args

            # ---- 编码档：**能力判断交给 AI**（按硬件型号知识/现场查询决定，用 --codec 传入）----
            # 脚本不做任何探测——**直接跑真实编码**；失败**逐级下退**（av1→hevc→h264→CPU）。
            if hw_req == 'none':
                chain = []
                print(f'[档位] 指定 CPU 档：libx264 -preset {preset} -crf {crf}（CPU 只做 H.264）')
            else:
                chain = (CODEC_PRIORITY[CODEC_PRIORITY.index(cd_req):]
                         if cd_req in CODEC_PRIORITY else CODEC_PRIORITY[:])
                what = (f'指定 {cd_req.upper()}' if cd_req in CODEC_PRIORITY
                        else f'预估链 {" → ".join(x.upper() for x in chain)}')
                print(f'[档位] {what}：按预估**直接跑真实编码**、失败逐级下退（至 CPU H.264）'
                      f'｜编码能力由 AI 按硬件判断（可传 --codec 指定）')

            def hard_cmd(v):
                return (['ffmpeg', '-y', '-loglevel', 'error', '-i', os.path.abspath(video),
                         '-vf', vf] + v + audio_opts + extra_args + [out_abs])
                # extra_args：用户自带的 ffmpeg 参数追加在最后——同名参数以用户的为准

            print('[*] 硬字幕烧录…')
            adopted, failed = None, []
            for c in chain:                       # 逐编码级：直接跑，失败换下一级
                fac = rate_factor(src_vc, c)
                br = int(opt('--bitrate', 0)) or int(src_br * fac)
                why = None
                for enc_ in HW_CANDIDATES[c]:     # 级内逐候选编码器直接试跑（nvenc→qsv→amf）
                    r = run(hard_cmd(hw_venc(c, enc_, br)), cwd=tmp, exit_on_fail=False)
                    if r.returncode == 0:
                        adopted = (c, enc_, fac, br)
                        break
                    if why is None:
                        errs = [l.strip() for l in (r.stderr or '').splitlines() if l.strip()]
                        keys = ('No capable', 'not support', 'failed to open', 'Unknown encoder',
                                'Cannot load', 'InitializeEncoder', 'unsupported', 'not available')
                        why = next((l for l in errs if any(k in l for k in keys)),
                                   errs[0] if errs else '?')
                        if why.startswith('['):
                            why = why.split('] ', 1)[-1]
                        why = why[:110]
                if adopted:
                    break
                failed.append(c.upper())
                nxt_c = chain[chain.index(c) + 1] if c != chain[-1] else None
                print(f'[降级] {c.upper()} 试跑失败：{why} → 改试 '
                      + (f'{nxt_c.upper()}（{HW_CANDIDATES[nxt_c][0]}）' if nxt_c
                         else f'CPU（libx264 -preset {preset} -crf {crf}）'))
            if adopted is None:                   # CPU 兜底（只做 H.264）
                r = run(hard_cmd(['-c:v', 'libx264', '-preset', preset, '-crf', crf]),
                        cwd=tmp, exit_on_fail=False)
                if r.returncode != 0:
                    print('[错误] 全部编码档均失败（含 CPU 兜底）：')
                    print((r.stderr or '')[-2000:])
                    sys.exit(1)
                adopted = ('cpu', None, None, None)
            ac, ae, afac, abr = adopted
            if ac == 'cpu':
                if failed:
                    print(f'[采用] CPU 档 libx264 -preset {preset} -crf {crf}'
                          f'（硬件 {"、".join(failed)} 试跑失败）')
            else:
                note = {'av1': '（AV1：现代播放器正常；Windows 自带播放器/个别剪辑软件可能需支持 AV1）',
                        'hevc': '（HEVC：现代播放器正常；Windows 自带播放器可能需装 HEVC 扩展）'}.get(ac, '')
                degrade = f'（{"、".join(failed)} 试跑失败 → 降级）' if failed else ''
                print(f'[采用] 硬件档 {ac.upper()}（{ae}）' + degrade + note)
                print(f'[提示] 码率匹配：源 {src_br / 1e6:.1f}M（{src_vc}）× {afac:.2f}'
                      f'（= 1.2 余量 × EFF[{ac}]/EFF[{src_vc}]）→ 目标 {abr / 1e6:.1f}M；'
                      f'maxrate {abr * 1.6 / 1e6:.1f}M｜--bitrate 可覆盖，--quality-first 换纯质量')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        container = 'mkv' if out_abs.lower().endswith('.mkv') else 'mp4'
        sub_codec = 'copy' if container == 'mkv' else 'mov_text'
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-i', os.path.abspath(video), '-i', os.path.abspath(ass),
               '-map', '0', '-map', '1', '-c', 'copy', '-c:s', sub_codec,
               '-disposition:s:0', 'default']
        attached = 0
        if container == 'mkv':
            # 附带字体（mkv 附件）：ASS 用的族名是技能专用"改族名副本"，系统没装——不附带会被播放器
            # 回退成别的字体（样式走样）。mpv/VLC/PotPlayer 等支持读取 mkv 附件字体。
            for fn in sorted(os.listdir(fontsdir)):
                if fn.lower().endswith(('.ttf', '.otf')):
                    cmd += ['-attach', os.path.join(fontsdir, fn),
                            f'-metadata:s:t:{attached}', 'mimetype=application/x-truetype-font',
                            f'-metadata:s:t:{attached}', f'filename={fn}']
                    attached += 1
        cmd.append(out_abs)
        print(f'[*] 软字幕封装（{container}/{sub_codec}，视频流零重编码'
              + (f'，附带字体 {attached} 个' if attached else '') + '）…')
        run(cmd)

    print(f'[完成] {out_abs}')
    print(probe(out_abs))
    # 体积/码率对照（硬字幕重编码必须让用户看得见"变大了多少"）
    if mode == 'hard' and os.path.isfile(out_abs):
        s_in, s_out = os.path.getsize(video), os.path.getsize(out_abs)
        print(f'[体积] 成片 {s_out / 1048576:.0f}MB ← 原片 {s_in / 1048576:.0f}MB'
              f'（{s_out / s_in:.2f}×）；源码率 {src_bitrate(video) / 1e6:.1f}M → '
              f'成片 {src_bitrate(out_abs) / 1e6:.1f}M')


if __name__ == '__main__':
    main()
