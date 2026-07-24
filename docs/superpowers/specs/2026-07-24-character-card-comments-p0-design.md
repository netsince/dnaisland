# 角色卡评论区 P0 体验优化设计方案

## 1. 概述 (Overview)
旨在提升角色卡详情页评论区的基础易用性与交互体验，解决作者无标识、用户无法删除自己评论、时间楼层展示模糊以及输入框功能简陋的问题。

---

## 2. 涉及改动模块 (Scope & Affected Files)

- **后端模型与路由**:
  - `app/routes/user.py`:
    - 修改 `card_comments_api`: 返回 `is_author`、`can_delete`、`delete_url` 及楼层号 `floor`。
    - 新增路由 `card_comment_delete(card_id, comment_id)`: 处理评论删除逻辑并校验权限（本人或管理员）。
    - 优化 `card_comment`: 强化内容校验与 500 字上限限制。
- **前端页面与样式**:
  - `app/templates/user/card_detail.html`:
    - 更新 `renderItem(cm)` JS 渲染逻辑，包含“作者”标签、楼层号、相对时间以及删除按钮。
    - 更新 `buildFooter()` 输入框，增加字数统计 (`0/500`)、Emoji 快捷选择面板、`Ctrl+Enter` 快捷发送。
    - 增加 AJAX 删除评论的前端交互逻辑（带确认框与评论总数同步）。
  - `app/static/css/style.css`:
    - 补充“作者”徽章样式、Emoji 选择器样式及删除按钮相关微调。

---

## 3. 详细设计 (Detailed Design)

### 3.1 后端 API 改动 (`app/routes/user.py`)

#### 1) `card_comments_api(card_id)` 接口数据拓展
在给前端返回的 `items` JSON 中补充以下字段：
- `is_author` (`bool`): `cm.user_id == card.author_id`（评论者是否为该角色卡作者）。
- `can_delete` (`bool`): `current_user.is_authenticated and (cm.user_id == current_user.id or current_user.is_super_admin)`。
- `delete_url` (`str`): `url_for("user.card_comment_delete", card_id=card_id, comment_id=cm.id)`。
- `floor` (`int`): 当前评论楼层号。计算公式：正序/倒序下总数为 `total`，基于 `page` 和 index 计算楼层。

#### 2) 新增删除评论接口 `/card/<card_id>/comment/<int:comment_id>/delete` (POST)
- **权限校验**: `@login_required`。获取 `Comment` 实例后校验：`comment.user_id == current_user.id or current_user.is_super_admin`。若不满足则返回 `403` JSON 或 Flash。
- **业务逻辑**: 从 `db.session` 中删除该记录，`commit()` 并返回 `{"ok": True, "total": remaining_count}`。

---

### 3.2 前端渲染与交互设计 (`card_detail.html`)

#### 1) 评论项 UI 结构增强
- **作者标识**: 若 `cm.is_author` 为 `true`，在用户名后渲染 `<span class="badge bg-primary-subtle text-primary border border-primary-subtle ms-1 dna-comment-author-badge">作者</span>`。
- **楼层与时间**: 显示 `#X楼` 编号；发布时间调用 JavaScript 相对时间函数 `formatRelativeTime(cm.created_at)`（如“刚刚”、“5分钟前”、“昨天 14:20”）。
- **删除按钮**: 若 `cm.can_delete` 为 `true`，在举报按钮旁渲染 `<button type="button" class="btn btn-link p-0 ms-2 text-danger small opacity-75 text-decoration-none dna-comment-del-btn">删除</button>`。点击弹窗确认后发 AJAX 请求，删除成功后添加淡出动画并更新顶部评论计数。

#### 2) 输入框体验提升 (Footer Area)
- **快捷键支持**: 监听 textarea `keydown` 事件，捕获 `Ctrl+Enter` / `Cmd+Enter` 自动提交表单。
- **字数统计与限制**: 输入框下方显示 `0/500` 字数统计。超过 500 字时，数字高亮红字，阻止提交。
- **Emoji 快捷输入面板**: 输入框底部工具栏提供 Emoji 按钮，点击展开/收起常用 Emoji 快捷列表（例如 `😊 👍 ❤️ 🎉 😭 👏 🤣 🙏 ✨ 💡`）。点击 Emoji 会在当前光标位置插入对应字符。

---

## 4. 测试与验证计划 (Testing Plan)

1. **角色卡作者发言验证**:
   - 使用角色卡作者账号发言，检查评论列表是否准确展示“作者”徽章。
2. **删除功能与权限测试**:
   - 本人发布评论后，检查是否出现“删除”按钮；点击删除后确认弹窗，检查接口及 DOM 是否正确移除。
   - 非本人账号查看该评论，验证无“删除”按钮且无法通过 API 越权删除。
3. **输入框体验测试**:
   - 输入文字检查 `0/500` 实时更新；输入超过 500 字检查提交拦截。
   - 按 `Ctrl + Enter` 校验是否能成功发布。
   - 点击 Emoji 按钮，选择表情，检查是否插入光标所在处。
4. **楼层与相对时间测试**:
   - 查看新建评论时间是否为“刚刚”或相对时间；检查各楼层编号递增/定位无误。
