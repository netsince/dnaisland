# 茶馆全面升级 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 DNAISLAND 茶馆模块实现关联角色卡、配图压缩、话题标签系统、收藏与引用发帖、投票功能以及茶馆热搜榜 6 大核心功能。

**架构：** 扩展 `app/models/teahouse.py` 数据库模型；在 `app/routes/teahouse.py` 中完善后端接口；结合 `app/services/image_service.py` 压图逻辑；在 `app/templates/teahouse/` 模板与 `style.css` 中升级 UI 与前端交互。

**技术栈：** Python 3, Flask, SQLAlchemy, Pillow (PIL), Bootstrap 5, Vanilla JS, HTML5

---

## 涉及文件目录与变动范围

- **模型**：`app/models/teahouse.py`
- **路由/服务**：`app/routes/teahouse.py`
- **模板**：
  - `app/templates/teahouse/feed.html`
  - `app/templates/teahouse/_post_item.html`
  - `app/templates/teahouse/post.html`
- **样式与脚本**：
  - `app/static/css/style.css`
- **测试**：
  - `tests/test_teahouse.py`

---

## 详细任务步骤拆解

### 任务 1：扩展数据库模型与数据迁移
**文件：**
- 修改：`app/models/teahouse.py`

- [ ] **步骤 1：在 `app/models/teahouse.py` 中添加扩展模型类与关联字段**
  - `TeaPost` 增加外键 `card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), nullable=True)`
  - `TeaPost` 增加外键 `quote_post_id = db.Column(db.Integer, db.ForeignKey("teahouse_posts.id"), nullable=True)`
  - 新增类 `TeaPostImage` (id, post_id, image_data, sort_order)
  - 新增类 `TeaTopic` (id, name, post_count, created_at)
  - 新增类 `TeaPostTopic` (post_id, topic_id)
  - 新增类 `TeaPostFavorite` (user_id, post_id, created_at)
  - 新增类 `TeaPoll` (id, post_id, is_multiple, expires_at)
  - 新增类 `TeaPollOption` (id, poll_id, option_text, vote_count)
  - 新增类 `TeaPollVote` (poll_id, option_id, user_id, created_at)

- [ ] **步骤 2：在交互环境下初始化数据表结构或应用 Migration**

---

### 任务 2：关联角色卡与配图压缩发帖后端实现
**文件：**
- 修改：`app/routes/teahouse.py`
- 修改：`app/models/teahouse.py`

- [ ] **步骤 1：拓展 `create_post` 发帖接口处理关联角色卡 `card_id` 与图片**
  - 从 `request.form` 中读取可选 `card_id`
  - 从 `request.form.getlist("images[]")` 或 JSON 读取 Base64 Data URL 列表
  - 对图片依次调用 `compress_image(data_url, max_edge=1024, quality=80)` 保存到 `TeaPostImage`

---

### 任务 3：话题标签解析与自动检索接口
**文件：**
- 修改：`app/routes/teahouse.py`

- [ ] **步骤 1：在发帖时正则提取 `#话题名#`**
  - 正则表达式：`re.findall(r"#([^#\s]{1,20})#", content)`
  - 自动查重与维护 `TeaTopic` 及 `TeaPostTopic` 关系
- [ ] **步骤 2：在 `teahouse.index` 中接收 `tag` 参数进行话题筛选**

---

### 任务 4：快速收藏与引用发帖后端接口
**文件：**
- 修改：`app/routes/teahouse.py`

- [ ] **步骤 1：添加无刷新收藏路由 `POST /teahouse/<post_id>/favorite`**
  - 切换 `TeaPostFavorite` 记录并返回 JSON 响应 `{ok: true, favorited: bool, count: int}`
- [ ] **步骤 2：发帖接口支持接收 `quote_post_id`**

---

### 任务 5：投票功能后端数据与 API 接口
**文件：**
- 修改：`app/routes/teahouse.py`

- [ ] **步骤 1：支持在发帖时建立 `TeaPoll` 及 `TeaPollOption`**
- [ ] **步骤 2：添加投票接口 `POST /teahouse/poll/<poll_id>/vote`**
  - 校验是否过期、校验单选/多选，保存 `TeaPollVote` 并更新计数

---

### 任务 6：茶馆热搜榜与热门话题算法
**文件：**
- 修改：`app/routes/teahouse.py`

- [ ] **步骤 1：计算近 7 天热门话题榜单与精选热贴**
  - 聚合 `TeaTopic` 按关联帖子数降序输出 Top 10 传递至 `index` 模板上下文

---

### 任务 7：前端模板更新、交互与 CSS 样式调整
**文件：**
- 修改：`app/templates/teahouse/feed.html`
- 修改：`app/templates/teahouse/_post_item.html`
- 修改：`app/templates/teahouse/post.html`
- 修改：`app/static/css/style.css`

- [ ] **步骤 1：重构 `_post_item.html` 渲染组件**
  - 渲染关联角色卡组件挂件
  - 渲染图片网格与全屏 Lightbox
  - 渲染 `#话题名#` 可点击标签
  - 渲染引用卡片展示
  - 渲染交互式投票柱状图与收藏按钮
- [ ] **步骤 2：更新 `feed.html` 侧边栏与发帖扩展区域**
  - 放置热搜榜 / 热门话题列表
  - 发帖框补充关联角色卡、多图上传、投票配置和插入话题交互
