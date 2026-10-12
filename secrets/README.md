# secrets/ —— 使用者私密目录（**随技能分发时必须为空**）

本目录**只存放"使用者本人"的运行时凭据**，绝不随技能打包/复制给其他人：

- `cookies.txt`：YouTube/Google 的 Netscape 格式 cookie —— **属于使用者个人账号会话，私密**。
  首次使用（或下载报 "Sign in / bot"）时现场获取：
  `node scripts/cookies/get_cookies.js <浏览器> out.json` → `python scripts/cookies/json2cookies.py out.json secrets/cookies.txt`
  （也可由使用者提供自己的 cookie 文件，或 `--cookies` 指向任意路径）。
- **Cookie 是技能的前置必做项**：缺失/失效时由 AI 与使用者交互现场获取（问哪个浏览器已登录 YouTube → 提示关闭该浏览器 → CDP 提取）。
  本目录随分发为空是设计如此，运行时获取后写入这里。

**导出/移植技能前**：确认本目录为空（含 `cookies.txt`、任何 `*.json` 中间产物）。
