#!/usr/bin/env python3
"""setup_fonts.py — 字体准备（把普惠体做成本技能用的"改族名副本"）

【目的】
生成 assets/fonts/ 下的两个字体（硬需求，缺了字幕会静默回退成别的字体）：
  PuHuiTi95.ttf        ← 阿里巴巴普惠体 3.0 95 ExtraBold（**中文用**；族名改成 PuHuiTi-95）
  AlibabaSansBold.ttf  ← Alibaba Sans **Bold**（**原文/英文用**；族名改成 AlibabaSans-Bold）
为什么要改族名：这两套字体**所有字重都共用族名**（"Alibaba PuHuiTi 3.0" / "Alibaba Sans"），
fontconfig 解析会撞车（名义 105 Heavy 会实际渲染成 55）——改族名后族名唯一、fontsdir 引用 100% 命中。
英文为什么用 Alibaba Sans Bold：它是阿里设计配套的拉丁字体（ASCII/Latin-1
全、西里尔字母也全，无 CJK）；普惠体虽也带 ASCII，但英文/原文统一改用 Sans 的 **Bold** 字重。

【用法】python setup_fonts.py [--force] [--src-dir DIR]
  --force     重新生成（覆盖已有）
  --src-dir   指定放有原版 ttf 的目录（可选；默认先搜本机常见字体目录）

【字体来源（按优先级）】
  1. 本机已装/已下载的原版文件：C:\\Windows\\Fonts、%LOCALAPPDATA%\\Microsoft\\Windows\\Fonts、
     以及 --src-dir；
  2. 官方直链下载（均需 Referer）：
     PuHuiTi     https://fonts.alibabadesign.com/AlibabaPuHuiTi-3.zip（约 266MB，含全字重）
     AlibabaSans https://fonts.alibabadesign.com/AlibabaSans.zip（约 1.6MB，含 6 字重）
     ⚠️ **必须带 Referer: https://fonts.alibabadesign.com/**，否则 403 "denied by Referer ACL"。

【字符集事实】
  - 95 ExtraBold 与 55 Regular 字集**逐字完全一致**（29,296 字形 / 29,197 码位、主区 CJK 20,976）
    → 中文用 95 = 真字重（替代"55 加 Bold 假粗"）+ 无生僻字缺口；
  - 105/115 是小字符集（约 9.6 千码位、主区 CJK 约 8 千）——故不用 105/115；
  - L3 不可用：L3 是"补充字集"（康熙部首 + 6 万扩展平面 CJK，连 ASCII 都没有）。
    **libass 缺字回退不认 fontsdir 里的 L3，会落系统字体**（尺寸/字重都不一致，生僻字异常偏大）；
    合并成单文件不可能（29,296 + 60,338 = 89,634 字形 > TrueType 硬上限 65,535）；且翻译后的
    中文本身不会有生僻字 —— 因此中文直接用 95 单字集（L3 不可用）；
  - Alibaba Sans Bold = 纯拉丁字体（734 字形 / 685 码位，无 CJK）：ASCII、Latin-1、西里尔字母全，
    数字与西文标点齐全（破折号/省略号/弯引号有）——英文（原文行）用。

【制作过程要点 / 坑】
  1. 改族名 = 用 fontTools 改 name 表 id 1/4/16（族名）与 id 6（PostScript 名），**字形不动**；
  2. 各字重共用族名 → 不加处理必选错字重；
  3. 若本机字体是"非官方来源"的小子集，先按字形数校验（本脚本会打印）。
"""
import os
import shutil
import subprocess
import sys
import zipfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(SKILL_DIR, 'assets', 'fonts')
PUHUITI_ZIP = 'https://fonts.alibabadesign.com/AlibabaPuHuiTi-3.zip'
SANS_ZIP = 'https://fonts.alibabadesign.com/AlibabaSans.zip'
ZIP_REFERER = 'https://fonts.alibabadesign.com/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'

TARGETS = {  # dst: (原版文件名, 改后族名, 改后PS名, 官方包URL)
    'PuHuiTi95.ttf': ('AlibabaPuHuiTi-3-95-ExtraBold.ttf', 'PuHuiTi-95', 'PuHuiTi95', PUHUITI_ZIP),
    'AlibabaSansBold.ttf': ('AlibabaSans-Bold.ttf', 'AlibabaSans-Bold', 'AlibabaSansBold', SANS_ZIP),
}
SEARCH_DIRS = [
    r'C:\Windows\Fonts',
    os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Windows\Fonts'),
    os.path.expanduser('~/Downloads'),
    os.path.expanduser('~/Downloads/AlibabaPuHuiTi-3'),  # 官方包解压根目录（下一层是各字重文件夹）
    os.path.expanduser('~/Downloads/AlibabaSans'),
]


def ensure_fonttools():
    try:
        import fontTools  # noqa: F401
    except ImportError:
        print('[*] 安装 fonttools…')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', 'fonttools'], check=True)


def find_local(name, extra_dir=None):
    for d in ([extra_dir] if extra_dir else []) + SEARCH_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
        if os.path.isdir(d):  # 官方包解压结构：<root>/<字重目录>/<同名 ttf>
            p2 = os.path.join(d, os.path.splitext(name)[0], name)
            if os.path.isfile(p2):
                return p2
    return None


def download_zip(tmpdir, url):
    import urllib.request
    out = os.path.join(tmpdir, url.rsplit('/', 1)[-1])
    print(f'[*] 下载官方字体包 {url.rsplit("/", 1)[-1]}…（必须带 Referer，否则 403）')
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': ZIP_REFERER})
    with urllib.request.urlopen(req, timeout=300) as r, open(out, 'wb') as f:
        shutil.copyfileobj(r, f, length=1024 * 1024)
    print(f'    已下载 {os.path.getsize(out) / 1048576:.1f} MB')
    return out


def rename_family(src, dst, family, psname):
    from fontTools.ttLib import TTFont
    t = TTFont(src)
    nm = t['name']
    for rec in list(nm.names):
        if rec.nameID in (1, 4, 16):
            nm.setName(family, rec.nameID, rec.platformID, rec.platEncID, rec.langID)
        elif rec.nameID == 6:
            nm.setName(psname, rec.nameID, rec.platformID, rec.platEncID, rec.langID)
    t.save(dst)


def main():
    args = sys.argv
    force = '--force' in args
    src_dir = args[args.index('--src-dir') + 1] if '--src-dir' in args else None
    os.makedirs(FONT_DIR, exist_ok=True)

    need = [dst for dst in TARGETS if force or not os.path.isfile(os.path.join(FONT_DIR, dst))]
    if not need:
        print(f'[ok] 字体已就绪：{list(TARGETS)}')
        return
    ensure_fonttools()
    import tempfile
    zips = {}
    with tempfile.TemporaryDirectory() as tmp:
        for dst in need:
            src_name, family, psname, zip_url = TARGETS[dst]
            src = find_local(src_name, src_dir)
            if not src:
                if zip_url not in zips:
                    zips[zip_url] = download_zip(tmp, zip_url)
                with zipfile.ZipFile(zips[zip_url]) as z:
                    member = next((n for n in z.namelist() if n.split('/')[-1] == src_name
                                   and not n.startswith('__MACOSX/')), None)
                    if not member:
                        print(f'[错误] zip 里找不到 {src_name}')
                        sys.exit(1)
                    src = os.path.join(tmp, src_name)
                    open(src, 'wb').write(z.read(member))
            dst_path = os.path.join(FONT_DIR, dst)
            rename_family(src, dst_path, family, psname)
            from fontTools.ttLib import TTFont
            cmap = TTFont(dst_path).getBestCmap()
            main_cjk = sum(1 for c in cmap if 0x4E00 <= c <= 0x9FFF)
            ext_cjk = sum(1 for c in cmap if 0x20000 <= c <= 0x2FFFF)
            print(f'[ok] {dst} ← {src} | 族名 {family} | cmap {len(cmap)}（主区 CJK {main_cjk} / 扩展区 {ext_cjk}）')


if __name__ == '__main__':
    main()
