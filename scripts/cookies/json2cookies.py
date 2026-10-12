#!/usr/bin/env python3
"""json2cookies.py — cookie JSON → yt-dlp 可读的 Netscape cookies.txt

【目的】把两种来源的 cookie JSON 转成 Netscape 格式：
  1. CDP 提取（get_cookies.js 输出，Cookie-Editor 格式，字段 expirationDate/session）；
  2. 浏览器扩展导出（Cookie-Editor / EditThisCookie，同为 Cookie-Editor 格式）；
  3. playwright 原始格式（字段 expires）——一并兼容。

【用法】python json2cookies.py <输入.json> <输出.txt>

【制作过程要点 / 坑】
  1. 值里的换行/制表符会破坏 Netscape 行结构，必须先剔除；
  2. 会话 cookie（expiry 0）照写（yt-dlp 会作为 session cookie 发送）；
  3. 验收标准：输出文件里应能看到 __Secure-3PSID / LOGIN_INFO 之类登录态 cookie（有则更稳）。
"""
import json
import sys


def convert(inp, outp):
    data = json.load(open(inp, encoding='utf-8'))
    if isinstance(data, dict):  # 兼容部分导出把数组包在 cookies 字段里
        data = data.get('cookies', [])

    lines = ['# Netscape HTTP Cookie File', '# 由 json2cookies.py 转换生成', '']
    n = login = 0
    for c in data:
        domain = (c.get('domain') or '').strip()
        name = (c.get('name') or '').strip()
        if not domain or not name:
            continue
        include_sub = 'TRUE' if domain.startswith('.') else 'FALSE'
        path = c.get('path') or '/'
        secure = 'TRUE' if c.get('secure') else 'FALSE'
        exp = c.get('expirationDate') if c.get('expirationDate') is not None else c.get('expires')
        expiry = 0 if (c.get('session') or not exp or exp < 0) else int(float(exp))
        value = str(c.get('value', '')).replace('\n', '').replace('\r', '').replace('\t', '')
        lines.append('\t'.join([domain, include_sub, path, secure, str(expiry), name, value]))
        n += 1
        if name in ('LOGIN_INFO', 'SID', '__Secure-1PSID', 'SAPISID'):
            login += 1

    open(outp, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
    return n, login


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    cnt, login = convert(sys.argv[1], sys.argv[2])
    print(f'[ok] 转换 {cnt} 条（登录态关键项 {login} 个）→ {sys.argv[2]}')
