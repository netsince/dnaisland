# 生图工作台与生图历史记录 CSR 重构实现计划

## Task 1: 升级后端 API 路由与剥离 SSR 冗余数据库查询
**Target Files:**
- `app/routes/image_gen.py`

**Steps:**
1. 在 `api_logs()` 中增加 `prompt`、`references` 数组以及完整产出图 `images` 数组 URL，供前端完整渲染卡片。
2. 清理 `workbench()` 函数中对 `GenerationLog.query.paginate` 的 SSR 数据库查询，不再传递 `recent` 或 `pagination` 给 `workbench.html`。
3. 清理 `logs()` 函数中对 `GenerationLog.query.paginate` 的 SSR 数据库查询，不再传递 `logs` 或 `pagination` 给 `logs.html`。
4. 运行 `uv run pytest` 确保路由测试不崩溃。

---

## Task 2: 重构生图工作台模板与客户端渲染逻辑 (`workbench.html`)
**Target Files:**
- `app/templates/image_gen/workbench.html`

**Steps:**
1. 移除 `workbench.html` 中硬编码 SSR 循环输出的 `#igRawItems` 及 `{% for log in recent %}` DOM 节点。
2. 保持初始 HTML 为纯净工作台壳（左侧表单与右侧瀑布流容器），完全不包含任何 `<img>` 标签。
3. 在脚本中编写页面初始化异步 `fetch('/image-gen/api/logs?page=1')`，动态生成瀑布流卡片。
4. 生图提交成功后，直接通过 AJAX 拿到的 JSON `images` 与 `user_points` 动态追加最新生成卡片并刷新积分显示。

---

## Task 3: 重构生图历史记录列表模板与 CSR 分页 (`logs.html`)
**Target Files:**
- `app/templates/image_gen/logs.html`

**Steps:**
1. 移除 `logs.html` 中 SSR 循环输出的 `#mgRawItems` 及 `{% for l in logs %}` DOM 节点。
2. 修改脚本，根据当前 URL 参数或默认 `page=1` 异步请求 `GET /image-gen/api/logs`。
3. 用 JS 动态构建瀑布流卡片及分页导航按钮，实现翻页无刷新 CSR 渲染。

---

## Task 4: 系统全量测试验证与成果提交
**Steps:**
1. 运行 `uv run pytest` 验证全量 15 个测试用例全部通过。
2. 确认 git 状态干净并提交代码。
