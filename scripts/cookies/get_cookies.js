// get_cookies.js — 用 playwright-core 以 CDP 方式从本机浏览器读取已解密的 cookie
//
// 【目的】Chromium 系新版浏览器（127+）启用 App-Bound Encryption，yt-dlp 的
//   --cookies-from-browser 与直接读库都会失败（Failed to decrypt with DPAPI）。
//   本脚本换思路：无头启动**浏览器本体**（复用其登录 profile），由浏览器自己解密，
//   经 CDP 把 cookie 交给我们 → 转 Cookie-Editor JSON（供 json2cookies.py 转 Netscape）。
//
// 【用法】node get_cookies.js [edge|chrome|<exe路径>] [out.json] [--profile NAME] [--user-data-dir DIR] [--timeout MS]
//   - 默认浏览器 edge（用户指定）；自定义 exe 路径时必须同时给 --user-data-dir；
//   - **前提：目标浏览器必须已关闭**（profile 会被占用），否则报错退出；
//   - 输出：Cookie-Editor 兼容 JSON；打印条数与是否含登录态。
//
// 【制作过程要点 / 坑】
//   1. playwright-core 只查找候选路径（技能内 node_modules / 环境变量 PW_CORE / 已知安装位置），
//      缺失时提示 npm install playwright-core；
//   2. userDataDir 传"User Data 根目录"并用 --profile-directory 选 profile
//      （直接传 ...\User Data\Default 会被当成根目录、读到空 cookie）；
//   3. 无头模式足够（不需要打开页面，直接 ctx.cookies() 取已解密值）；
//   4. 取 youtube.com + google.com 两个域（后者含登录态相关的 google 域 cookie）；
//   5. **启动超时默认 30s**（`--timeout MS` 可调）——超时/失败即视为"自动获取做不到"，
//      转请用户直接提供 cookie 文件（Netscape），不要在启动上死等。
const fs = require('fs');
const path = require('path');

function loadPW() {
  const candidates = [
    process.env.PW_CORE,
    path.join(__dirname, 'node_modules', 'playwright-core'),
    path.join(__dirname, '..', '..', 'node_modules', 'playwright-core'),
    path.join(process.env.APPDATA || '', 'npm', 'node_modules', 'playwright-core'),
  ].filter(Boolean);
  for (const c of candidates) {
    try { if (fs.existsSync(path.join(c, 'package.json'))) return require(c); } catch (e) { }
  }
  return null;
}

const BROWSERS = {
  edge: {
    exe: ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
          'C:/Program Files/Microsoft/Edge/Application/msedge.exe'],
    userData: path.join(process.env.LOCALAPPDATA || '', 'Microsoft', 'Edge', 'User Data'),
  },
  chrome: {
    exe: ['C:/Program Files/Google/Chrome/Application/chrome.exe',
          path.join(process.env.LOCALAPPDATA || '', 'Google/Chrome/Application/chrome.exe')],
    userData: path.join(process.env.LOCALAPPDATA || '', 'Google', 'Chrome', 'User Data'),
  },
};

function pickExe(list) { return list.find(p => fs.existsSync(p)); }

(async () => {
  const argv = process.argv.slice(2);
  const pos = argv.filter(a => !a.startsWith('--'));
  const name = pos[0] || 'edge';
  const out = pos[1] || 'cdp_cookies.json';
  const profile = argv.includes('--profile') ? argv[argv.indexOf('--profile') + 1] : 'Default';
  const customUD = argv.includes('--user-data-dir') ? argv[argv.indexOf('--user-data-dir') + 1] : null;
  const tmo = argv.includes('--timeout') ? parseInt(argv[argv.indexOf('--timeout') + 1], 10) : 30000;
  console.log(`[*] 启动超时: ${tmo / 1000}s（超时视为自动获取失败 → 改请用户提供 cookie 文件）`);

  const spec = BROWSERS[name] || { exe: [name], userData: customUD };
  const exe = pickExe(spec.exe);
  const userData = customUD || spec.userData;
  if (!exe) { console.error(`[!] 找不到浏览器可执行文件（${name}）。内置: edge / chrome；其他浏览器（含便携版）请直接给 exe 路径 + --user-data-dir`); process.exit(2); }

  const pw = loadPW();
  if (!pw) { console.error('[!] 找不到 playwright-core。请运行: cd ' + __dirname + ' && npm init -y && npm install playwright-core（或用 PW_CORE 指定路径）'); process.exit(2); }

  console.log(`[*] 无头启动: ${exe}\n[*] 配置目录: ${userData} (profile=${profile})`);
  let ctx;
  try {
    ctx = await pw.chromium.launchPersistentContext(userData, {
      executablePath: exe, headless: true, timeout: tmo,
      args: [`--profile-directory=${profile}`, '--disable-blink-features=AutomationControlled',
             '--no-first-run', '--no-default-browser-check', '--mute-audio', '--disable-popup-blocking'],
    });
  } catch (e) {
    console.error('[!] 启动失败（浏览器是否还开着？profile 被占用会失败）:', e.message.split('\n')[0]);
    process.exit(1);
  }
  try {
    const raw = await ctx.cookies(['https://www.youtube.com', 'https://www.google.com']);
    const mapped = raw.map(c => ({
      domain: c.domain, expirationDate: (c.expires && c.expires > 0) ? c.expires : null,
      hostOnly: !String(c.domain).startsWith('.'), httpOnly: !!c.httpOnly, name: c.name,
      path: c.path || '/', sameSite: c.sameSite || 'unspecified', secure: !!c.secure,
      session: !(c.expires > 0), storeId: null, value: c.value,
    }));
    fs.writeFileSync(out, JSON.stringify(mapped, null, 2), 'utf-8');
    const hasLogin = mapped.some(c => ['LOGIN_INFO', 'SID', '__Secure-1PSID', 'SAPISID'].includes(c.name));
    console.log(`[ok] 取到 ${mapped.length} 条 cookie（含登录态: ${hasLogin ? '是' : '否'}）→ ${out}`);
    console.log(`[ok] 继续: python json2cookies.py ${out} <技能>/secrets/cookies.txt`);
  } finally { await ctx.close(); }
})().catch(e => { console.error('[!] 失败:', e.message.split('\n')[0]); process.exit(1); });
