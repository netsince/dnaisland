# DNAISLAND 茶馆模块全面升级设计文档 (Teahouse Upgrade Design)

## 1. 概述与设计目标

DNAISLAND 项目目前是一个基于 AI 角色卡（Character Card）的分享、展示与社区交流平台。“茶馆”作为社区短帖讨论板块，原仅具备基础的 280 字纯文本动态发布与简单点赞回复功能。

本次升级旨在显著提升茶馆的内容丰富度、社交互动频率与内容发现效率，共包含 6 大核心功能：
1. **关联/推荐角色卡**：发帖时关联本站已有角色卡，帖文中生成直达详情页的组件。
2. **配图与多媒体支持**：复用现有 WebP 高效压缩机制（`compress_image`），支持多图上传与 Lightbox 预览。
3. **话题/标签系统 (`#Hashtag#`)**：自动识别与筛选话题，提升主题讨论度。
4. **快速收藏与引用发帖**：一键收藏动态至个人中心；引用他人帖文作为卡片嵌入撰写点评。
5. **投票功能 (Polls)**：支持单选/多选投票及倒计时，低门槛提升用户互动。
6. **茶馆热搜榜/热门话题榜**：侧边栏与顶栏展示近 7 天热搜话题与热贴，增强内容发现。

---

## 2. 详细功能规格与设计

### 2.1 关联/推荐角色卡 (Attach Character Card)
- **数据设计**：在 `teahouse_posts` 表中新增可选外键 `card_id = Column(String(36), ForeignKey("cards.id"), nullable=True)`。
- **发帖交互**：
  - 发帖/回复框下方新增“📌 关联角色卡”按钮。
  - 点击调出角色卡选择器 modal，列出“我创建的卡”、“我收藏的卡”并支持搜索。
  - 选中后发帖框下方显示所选角色的微型预览标签，支持随时点击 `✕` 移除。
- **动态展示**：
  - 帖文底栏下方渲染【角色卡挂件】：包含立绘封面、卡片名称、作者、一句话简介。
  - 包含高亮按钮 **“查看/下载卡片 →”**，点击跳转到 `/card/<card_id>`。

### 2.2 配图与多媒体支持 (Image Upload & WebP Compression)
- **数据设计**：建立 `teahouse_post_images` 表：
  - `id` (Integer, primary_key)
  - `post_id` (Integer, ForeignKey("teahouse_posts.id"), index=True)
  - `image_data` (LONGTEXT, Base64 Data URL)
  - `sort_order` (Integer, default=0)
- **压缩与上传**：
  - 复用 `app/services/image_service.py` 的 `compress_image(data_url, max_edge=1024, quality=80)`。
  - 单帖限制最多 4 张图片。发帖框支持选取图片、实时九宫格缩略预览与一键移除。
- **前端渲染**：
  - 1 张图居左展现；2~4 张图采用自适应网格展示。
  - 点击图片触发 Lightbox 模态框展开全屏大图。

### 2.3 话题/标签系统 (#Hashtag#)
- **解析与存储**：
  - 发帖与回复提交时，正则匹配内容中的 `#([^#\s]{1,20})#`。
  - 新建 `teahouse_topics` 表（`id`, `name`, `post_count`, `created_at`）及 `teahouse_post_topics` 关联表。
- **交互与过滤**：
  - 发帖框输入 `#` 弹出热门话题补全菜单。
  - 帖文中 `#话题名#` 渲染为蓝色超链接，点击跳转至 `/teahouse?tag=话题名` 进行筛选。
  - 顶部筛选胶囊栏增设热门话题入口。

### 2.4 快速收藏与引用发帖 (Bookmark & Quote Post)
- **快速收藏 (Bookmark)**：
  - 新建 `teahouse_post_favorites` 收藏联表 (`user_id`, `post_id`, `created_at`)。
  - 帖文底栏新增“收藏”按钮，AJAX 无刷新切换状态。
  - 个人主页 `/u/<username>?tab=teahouse_favs` 中展示已收藏的茶馆贴。
- **引用发帖 (Quote Post)**：
  - 新建 `quote_post_id` 外键字段于 `teahouse_posts`。
  - 帖文底栏新增“引用”按钮，点击弹发帖框并嵌入原帖的简易卡片（作者、发布时间、原帖摘要/缩略图）。
  - 发布后在贴文中以嵌套卡片样式展示原帖，点击跳转至原帖详情页。

### 2.5 投票功能 (Polls)
- **数据设计**：
  - `teahouse_polls` 表：`id`, `post_id`, `is_multiple` (Boolean), `expires_at` (DateTime, nullable)
  - `teahouse_poll_options` 表：`id`, `poll_id`, `option_text`, `vote_count` (Integer, default=0)
  - `teahouse_poll_votes` 表：`poll_id`, `option_id`, `user_id`, `created_at`
- **发帖与展示**：
  - 发帖框新增“📊 投票”按钮，展开 2~4 个选项输入框与时长选择（1天/3天/7天/永久）。
  - 帖文卡片内渲染投票组件：未投票用户点击选项即时完成投票；已投票/已截止用户展示各选项票数及百分比进度条。

### 2.6 茶馆热搜榜 / 热门话题榜 (Trending Topics & Hot Posts)
- **版面设计**：
  - PC 端茶馆右侧栏新增【🔥 茶馆热榜】侧边栏。
  - 移动端在筛选胶囊栏顶部/折叠层提供热榜入口。
- **算法逻辑**：
  - **热门话题**：统计近 7 天关联动态最多、互动频次最高的前 10 个 `#话题#`。
  - **热贴精选**：综合 `(like_count * 1 + reply_count * 2 + favorite_count * 2 + quote_count * 3)` 算法排序出的热门动态。

---

## 3. 架构与改动范围汇总

1. **数据库模型** (`app/models/teahouse.py`)：
   - 扩展 `TeaPost`：新增 `card_id`, `quote_post_id`
   - 新增表：`TeaPostImage`, `TeaTopic`, `TeaPostTopic`, `TeaPostFavorite`, `TeaPoll`, `TeaPollOption`, `TeaPollVote`
2. **服务与路由** (`app/routes/teahouse.py`)：
   - 增加发帖逻辑（处理关联卡、图片压缩、话题抽取、投票创建、引用绑定）。
   - 增加 API 端点：`POST /teahouse/<id>/favorite`, `POST /teahouse/<id>/vote`, `GET /teahouse/search_cards`, `GET /teahouse/trending`
3. **前端模板与样式**：
   - 更新 `app/templates/teahouse/feed.html`, `app/templates/teahouse/_post_item.html`, `app/templates/teahouse/post.html`
   - 增加关联卡挂件、配图网格与 Lightbox、投票柱状条、引用卡片排版、热榜侧边栏样式于 `app/static/css/style.css`

---

## 4. 安全与审核

- **XSS 防范**：话题名称、投票选项与配图 Base64 均经 Jinja2 / Escape 严格转义处理，URL 经 `teahouse_linkify` 过滤。
- **先发后审集成**：引用贴、带图帖与带投票帖依然遵循平台的 `is_hidden` 与管理员审核逻辑 (`admin.tea_moderation`)。
