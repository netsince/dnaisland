# 生图工作台与生图历史记录 CSR 客户端渲染重构设计规范

## 1. 概述与背景

生图工作台 (`/image-gen/`) 与生图历史记录 (`/image-gen/logs`) 原先采用服务端 SSR 方式，将历史记录的 `<img>` 标签与 base64 图片数据或图片 Serving URL 同步输出在初始 HTML DOM 源码中。这导致浏览器在解析首屏 HTML 时将图片强制加入关键资源加载队列，引发标签页长时间旋转转圈，影响用户体验。

本设计规范旨在将这两个页面全面重构成 **CSR (Client-Side Rendering)** 客户端渲染模式：
- 首屏仅返回极简 HTML 壳框架（无任何图片 `<img>` 源码），页面在 `<20ms` 内下载完成，标签页停止转圈。
- 页面挂载后，前端 JavaScript 异步调用 `GET /image-gen/api/logs` 获取 JSON 数据并渲染瀑布流卡片。
- 生图提交全 AJAX 交互，无全页刷新或重定向。

---

## 2. API 设计规范

### 2.1 获取生图日志 API (`GET /image-gen/api/logs`)

- **URL**: `/image-gen/api/logs`
- **Method**: `GET`
- **Query Params**:
  - `page`: 页码（默认 1）
  - `per_page`: 每页条数（默认 12）
- **Response Format (JSON)**:
  ```json
  {
    "ok": true,
    "total": 48,
    "page": 1,
    "pages": 4,
    "has_next": true,
    "has_prev": false,
    "items": [
      {
        "id": 102,
        "model_name": "SDXL Turbo",
        "prompt": "二次元银发少女...",
        "size": "auto",
        "count": 1,
        "status": "success",
        "points_spent": 10,
        "created_at": "2026-07-25 00:45",
        "images": ["/image-gen/output/102/0"],
        "references": ["/image-gen/reference/102/0"],
        "detail_url": "/image-gen/logs/102"
      }
    ]
  }
  ```

### 2.2 提交生图 API (`POST /image-gen/generate`)

- **URL**: `/image-gen/generate`
- **Method**: `POST`
- **Headers**: `X-Requested-With: XMLHttpRequest`
- **Request Body**: `FormData` (`prompt`, `model`, `count`, `size`, `references`)
- **Response Format (JSON)**:
  ```json
  {
    "ok": true,
    "log_id": 103,
    "model_name": "SDXL Turbo",
    "size": "auto",
    "status": "success",
    "points_spent": 10,
    "balance": 990,
    "images": ["/image-gen/output/103/0"],
    "references": ["/image-gen/reference/103/0"],
    "detail_url": "/image-gen/logs/103"
  }
  ```

---

## 3. 页面重构规范

### 3.1 生图工作台 (`/image-gen/`)
1. 后端 `workbench()` 路由不再执行 DB 中的 `GenerationLog` 查询，也不传 `recent` 给模板。
2. 初始 HTML 仅输出：
   - 左侧：生成表单（模型、提示词、张数、宽高比、参考图）
   - 右侧：瀑布流空容器框架与生图日志 Sheet/Drawer
3. JS 脚本：
   - 页面 DOM 加载完毕后，发起 `fetch('/image-gen/api/logs?page=1')`。
   - 收到 JSON 后，动态在瀑布流中构建卡片并渲染图片（配合 `loading="lazy"` 与 `decoding="async"`）。
   - 用户发起生图提交时，前端插入虚化骨架卡片；生图完成后更新剩余积分与卡片。

### 3.2 生图历史记录 (`/image-gen/logs`)
1. 后端 `logs()` 路由不再执行 `logs` 列表 SSR 混用渲染，仅返回结构框架。
2. JS 脚本：
   - 读取 URL 中的 `page` 参数，发起 `fetch('/image-gen/api/logs?page=X')`。
   - 动态渲染瀑布流卡片及无刷新 CSR 分页控件。

---

## 4. 验证计划

1. 自动化单元测试：运行 `uv run pytest`，确保全站测试套件 100% 通过。
2. 页面抓包验证：检查 `/image-gen/` 和 `/image-gen/logs` 初始 HTML Response，确认无任何图片 `<img>` 标签直接硬编码写在 SSR 源码中。
