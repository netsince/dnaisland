# 角色卡评论区 P1 社交与互动增强 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完成角色卡评论区 P1 功能（评论点赞、最新/最热排序切换、回复与 @ 引用、作者置顶评论），包含数据库 Migration。

**架构：** 在 `app/models/card.py` 中增加 `CommentLike` 模型并在 `Comment` 中增加 `is_pinned` 和 `reply_to_id` 字段；更新 `app/routes/user.py` 路由逻辑与新增点赞/置顶 API；更新 `app/templates/user/card_detail.html` 及 `app/static/css/style.css` 实现全套交互。

**技术栈：** Python (Flask / SQLAlchemy / Flask-Migrate), HTML5, JavaScript (Vanilla JS), Bootstrap 5 CSS.

---

### 依赖与修改文件一览 (File Structure)

- **修改：** `app/models/card.py` — 新增 `CommentLike` 模型，`Comment` 追加 `is_pinned` / `reply_to_id` 字段与关系
- **修改：** `app/routes/user.py` — API 排序、置顶优先排序、点赞数与用户已赞标记，新增点赞/置顶路由及回复解析
- **修改：** `app/templates/user/card_detail.html` — 最新/最热排序 Tab、置顶显示与按钮、点赞交互、回复链接与预填
- **修改：** `app/static/css/style.css` — 补充置顶高亮框、点赞红心、排序 Tab 样式
- **新建：** `tests/test_card_comments_p1.py` — 包含点赞、排序、置顶、回复等完整后端单元测试

---

### 任务 1：数据库模型变更与 Migration

**文件：**
- 修改：`app/models/card.py`
- 新建：`tests/test_card_comments_p1.py`

- [ ] **步骤 1：修改 `app/models/card.py` 拓展 Comment 模型与添加 CommentLike**

```python
class CommentLike(db.Model):
    __tablename__ = "comment_likes"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"), primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class Comment(db.Model):
    # 现有字段...
    is_pinned = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    reply_to_id = db.Column(db.Integer, db.ForeignKey("comments.id"), nullable=True)
    reply_to = db.relationship("Comment", remote_side=[id], backref="replies")
```

- [ ] **步骤 2：编写模型与关系测试 `tests/test_card_comments_p1.py`**

- [ ] **步骤 3：运行测试验证模型能成功建表并关联**

运行：`python -m pytest tests/test_card_comments_p1.py`

- [ ] **步骤 4：运行 Flask-Migrate 或生成 Migration 数据库文件**

运行：`flask db migrate -m "add comment likes and pinned status"`
运行：`flask db upgrade`

- [ ] **步骤 5：Commit**

```bash
git add app/models/card.py tests/test_card_comments_p1.py migrations/
git commit -m "feat(models): add CommentLike and extend Comment with pinned and reply_to attributes"
```

---

### 任务 2：后端 API 路由拓展与交互支持

**文件：**
- 修改：`app/routes/user.py`
- 修改：`tests/test_card_comments_p1.py`

- [ ] **步骤 1：在 `tests/test_card_comments_p1.py` 中编写点赞、置顶、最新/最热排序测试**

- [ ] **步骤 2：运行测试验证预期失败**

- [ ] **步骤 3：在 `app/routes/user.py` 中实现业务逻辑**

1. 修改 `card_comments_api(card_id)`:
   - 读取 `sort = request.args.get("sort", "latest")`。
   - 查询排序：`sort == "hottest"` 时按点赞数与时间混合降序，`sort == "latest"` 按时间降序。
   - `is_pinned` 的评论无论何种排序，全表固定最先列出在第 1 页顶部。
   - 返回项中加入 `like_count`, `liked`, `is_pinned`, `can_pin`, `reply_to`。
2. 新增路由 `@user_bp.route("/card/<card_id>/comment/<int:comment_id>/like", methods=["POST"])`:
   - `@login_required`。若已赞则取消点赞，未赞则新增点赞记录，返回最新 `liked` 状态和 `like_count`。
3. 新增路由 `@user_bp.route("/card/<card_id>/comment/<int:comment_id>/pin", methods=["POST"])`:
   - 校验是否为角色卡创作者或超级管理员。切换 `cm.is_pinned = not cm.is_pinned` 并提交。
4. 修改 `card_comment(card_id)`:
   - 支持读取 `reply_to_id` 表单字段并校验关联正确性。

- [ ] **步骤 4：运行 pytest 验证测试全绿通过**

运行：`python -m pytest tests/test_card_comments_p1.py`

- [ ] **步骤 5：Commit**

```bash
git add app/routes/user.py tests/test_card_comments_p1.py
git commit -m "feat(comments): implement comment like, pin, reply_to and latest/hottest sorting endpoints"
```

---

### 任务 3：前端 UI 重构与交互升级

**文件：**
- 修改：`app/templates/user/card_detail.html`
- 修改：`app/static/css/style.css`

- [ ] **步骤 1：在 `app/static/css/style.css` 中追加 P1 样式**

```css
/* 置顶评论卡片样式 */
.dna-comment-item--pinned {
  border-left: 3px solid var(--bs-warning, #f59e0b) !important;
  background-color: var(--bs-warning-bg-subtle, rgba(245, 158, 11, 0.05));
}
.dna-comment-pin-badge {
  font-size: 0.68rem;
  padding: 0.1rem 0.35rem;
}

/* 点赞按钮样式 */
.dna-comment-like-btn {
  cursor: pointer;
  transition: color 0.15s ease, transform 0.15s ease;
}
.dna-comment-like-btn.liked {
  color: var(--bs-danger, #ef4444) !important;
}
.dna-comment-like-btn:active {
  transform: scale(1.2);
}

/* 排序 Tabs */
.dna-comment-sort-tabs .btn-link {
  font-weight: 500;
  text-decoration: none;
  opacity: 0.6;
}
.dna-comment-sort-tabs .btn-link.active {
  opacity: 1;
  font-weight: 600;
  border-bottom: 2px solid var(--bs-primary, #6366f1);
}
```

- [ ] **步骤 2：重构 `app/templates/user/card_detail.html` 评论抽屉头部与列表项**

- 抽屉 Header 增加 `最新` 和 `最热` Tabs，切换 `currentSort` 重新加载 `loadPage(1)`。
- `renderItem(cm)` 中：
  - 渲染 `📌 置顶` 标志；
  - 渲染被回复对象：`回复 @xxx`；
  - 渲染点赞图标及数量：`<button class="dna-comment-like-btn ${cm.liked ? 'liked' : ''}"><i class="bi bi-heart${cm.liked ? '-fill' : ''}"></i> <span>${cm.like_count || 0}</span></button>`；
  - 渲染“回复”链接；若 `cm.can_pin` 为 true，渲染“置顶/取消置顶”操作项。
- 绑定点击事件委托：
  - 点击“点赞”图标触发 AJAX `POST` 请求，动态更新爱心与计数；
  - 点击“置顶”触发 AJAX `POST` 请求，重新拉取第 1 页列表；
  - 点击“回复”提取作者名字，在 textarea 中注入 `@用户名 ` 并聚焦。

- [ ] **步骤 3：手动/自动化体验检查**

- [ ] **步骤 4：Commit**

```bash
git add app/templates/user/card_detail.html app/static/css/style.css
git commit -m "feat(comments): add sorting tabs, comment like toggle, pinning UI, and reply mention handler"
```
