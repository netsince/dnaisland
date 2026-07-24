# 角色卡评论区 P0 体验优化 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完成角色卡评论区 P0 功能优化（作者专属徽章、用户自主删除评论、输入框快捷键/字数统计/Emoji面板、楼层号与相对时间展示）。

**架构：** 在 `app/routes/user.py` 中拓展评论 API (`card_comments_api`) 与新增删除接口 (`card_comment_delete`)；在 `app/templates/user/card_detail.html` 中优化评论渲染与输入框交互逻辑；在 `app/static/css/style.css` 中增加必要样式。

**技术栈：** Python (Flask / SQLAlchemy), HTML5, JavaScript (Vanilla JS), Bootstrap 5 CSS.

---

### 依赖与修改文件一览 (File Structure)

- **修改：** `app/routes/user.py` — API 补充作者/权限/楼层信息，新增删除路由与 500 字上限校验
- **修改：** `app/templates/user/card_detail.html` — 前端渲染逻辑、相对时间、删除逻辑、字数统计、快捷键与 Emoji 快捷面板
- **修改：** `app/static/css/style.css` — 补充作者徽章、Emoji 选择框、删除按钮微调样式
- **新建：** `tests/test_card_comments.py` — 评论接口与删除路由的单元与集成测试

---

### 任务 1：后端路由与 API 拓展

**文件：**
- 修改：`app/routes/user.py`
- 新建：`tests/test_card_comments.py`

- [ ] **步骤 1：编写后端接口与权限测试**

```python
# tests/test_card_comments.py
import pytest
from app import create_app, db
from app.models import User, Card, Comment

@pytest.fixture
def client():
    app = create_app("testing")
    app.config["TESTING"] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            yield client
            db.session.remove()
            db.drop_all()

def test_comment_api_extended_fields(client):
    # 测试 card_comments_api 返回 is_author, can_delete, delete_url, floor
    pass

def test_comment_delete_permission(client):
    # 测试作者本人与超级管理员可删除，非本人不可删
    pass
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_card_comments.py`

- [ ] **步骤 3：编写后端实现**

在 `app/routes/user.py` 中：
1. 拓展 `card_comments_api`:
   - 计算 `floor` 楼层号：基于 `pagination.total - ((page - 1) * per_page + idx)`。
   - `is_author = (cm.user_id == card.author_id)`
   - `can_delete = current_user.is_authenticated and (cm.user_id == current_user.id or current_user.is_super_admin)`
   - `delete_url = url_for("user.card_comment_delete", card_id=card_id, comment_id=cm.id)`
2. 新增路由 `card_comment_delete(card_id, comment_id)`:
   - 校验当前用户与评论归属（`comment.user_id == current_user.id or current_user.is_super_admin`）。
   - 删除成功后 `db.session.delete(cm)` 并 `commit()`。

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_card_comments.py`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add app/routes/user.py tests/test_card_comments.py
git commit -m "feat(comments): add comment deletion endpoint and extend comments API fields"
```

---

### 任务 2：前端渲染与样式增强

**文件：**
- 修改：`app/templates/user/card_detail.html`
- 修改：`app/static/css/style.css`

- [ ] **步骤 1：在 `app/static/css/style.css` 中追加辅助样式**

```css
/* dna-comment-author-badge */
.dna-comment-author-badge {
  font-size: 0.68rem;
  padding: 0.1rem 0.35rem;
  vertical-align: middle;
  border-radius: 4px;
}

/* dna-emoji-picker */
.dna-emoji-bar {
  display: flex;
  gap: 0.25rem;
  padding: 0.35rem;
  background: var(--bs-tertiary-bg, #f8f9fa);
  border: 1px solid var(--bs-border-color, #dee2e6);
  border-radius: 6px;
  margin-top: 0.35rem;
  flex-wrap: wrap;
}

.dna-emoji-btn {
  background: none;
  border: none;
  padding: 0.15rem 0.35rem;
  cursor: pointer;
  border-radius: 4px;
  font-size: 1.1rem;
  line-height: 1;
  transition: background-color 0.15s ease;
}

.dna-emoji-btn:hover {
  background-color: var(--bs-secondary-bg, #e9ecef);
}
```

- [ ] **步骤 2：更新 `app/templates/user/card_detail.html` 评论渲染 `renderItem`**

包含：
- `is_author` 时显示 `<span class="badge bg-primary-subtle text-primary border border-primary-subtle ms-1 dna-comment-author-badge">作者</span>`
- 显示 `#${cm.floor}楼`
- 相对时间计算助手 `formatRelativeTime(cm.created_at)`
- `can_delete` 时显示删除操作与绑定 AJAX 点击事件确认逻辑。

- [ ] **步骤 3：更新 `app/templates/user/card_detail.html` 底部输入框 `buildFooter`**

包含：
- 键盘事件监听：捕获 `Ctrl+Enter` / `Cmd+Enter` 触发表单提交
- 字数计数器：实时渲染 `<span id="cmCharCount" class="small text-muted ms-auto">0/500</span>`
- Emoji 选择面板：包含常用 Emoji，点击自动追加到光标处或末尾。

- [ ] **步骤 4：手动/自动化界面检查**

验证输入框字数控制、Emoji 快捷插入、删除弹窗逻辑正常。

- [ ] **步骤 5：Commit**

```bash
git add app/templates/user/card_detail.html app/static/css/style.css
git commit -m "feat(comments): enhance comment UI with author badge, deletion, relative time, and emoji picker"
```
