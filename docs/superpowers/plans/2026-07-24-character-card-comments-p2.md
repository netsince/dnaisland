# 角色卡评论区 P2 进阶功能 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完成角色卡评论区 P2 功能（消息通知联动、评论单图附件上传与展示、敏感词拦截/反馈强化），包含数据库 Migration。

**架构：** 在 `app/models/card.py` 的 `Comment` 中加入 `image_url` 字段；在 `app/routes/user.py` 中增加图片上传保存逻辑与消息通知 (`notify`) 触发；在 `app/templates/user/card_detail.html` 与 `app/static/css/style.css` 中增加前端图片选图预览、缩略图展示与全屏放大。

**技术栈：** Python (Flask / SQLAlchemy / Werkzeug), HTML5, JavaScript (Vanilla JS), GLightbox, Bootstrap 5 CSS.

---

### 依赖与修改文件一览 (File Structure)

- **修改：** `app/models/card.py` — `Comment` 字段添加 `image_url`
- **修改：** `app/routes/user.py` — 点赞/回复/评论发送消息通知，评论图片文件校验与上传保存
- **修改：** `app/templates/user/card_detail.html` — 前端图片上传按钮、缩略图预览与撤销、评论列表图片显示与全屏查看
- **修改：** `app/static/css/style.css` — 补充评论图片预览缩略图、撤销按钮及展示样式
- **新建：** `tests/test_card_comments_p2.py` — 通知联动与图片上传后端单元测试

---

### 任务 1：模型字段追加、Migration 与消息通知联动

**文件：**
- 修改：`app/models/card.py`
- 修改：`app/routes/user.py`
- 新建：`tests/test_card_comments_p2.py`

- [ ] **步骤 1：修改 `app/models/card.py` 中的 Comment 模型**

```python
class Comment(db.Model):
    # 现有字段...
    image_url = db.Column(db.String(255), nullable=True)
```

- [ ] **步骤 2：生成并应用数据库 Migration**

运行：`python -m flask db migrate -m "add image_url to comments"`
运行：`python -m flask db upgrade`

- [ ] **步骤 3：修改 `app/routes/user.py` 追加消息通知触发**

1. 点赞他人评论触发通知（`user_id != current_user.id`）：
   `notify(user_id=cm.user_id, message=f"{current_user.display_name} 点赞了你在《{card.name}》下的评论", type_="comment_like", related_card_id=card.id)`
2. 回复他人评论触发通知（`parent_cm.user_id != current_user.id`）：
   `notify(user_id=parent_cm.user_id, message=f"{current_user.display_name} 在《{card.name}》中回复了你：\"{content[:30]}\"", type_="comment_reply", related_card_id=card.id)`
3. 在他人卡片下发表评论提醒作者（`card.author_id != current_user.id` 且无回复）：
   `notify(user_id=card.author_id, message=f"{current_user.display_name} 在你的角色卡《{card.name}》下发表了评论", type_="card_comment", related_card_id=card.id)`

- [ ] **步骤 4：编写测试 `tests/test_card_comments_p2.py` 验证消息通知触发**

- [ ] **步骤 5：运行测试验证 PASS**

运行：`python -m pytest tests/test_card_comments_p2.py`

- [ ] **步骤 6：Commit**

```bash
git add app/models/card.py app/routes/user.py tests/test_card_comments_p2.py migrations/
git commit -m "feat(comments): add image_url attribute to Comment and integrate comment notifications"
```

---

### 任务 2：评论图片附件上传后端支持

**文件：**
- 修改：`app/routes/user.py`
- 修改：`tests/test_card_comments_p2.py`

- [ ] **步骤 1：在 `app/routes/user.py` 的 `card_comment(card_id)` 中增加图片上传支持**

1. 读取 `image_file = request.files.get("image")`。
2. 校验文件扩展名（`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`）与文件大小（<= 5MB）。
3. 保存至 `app/static/uploads/comments/YYYYMM/filename.ext`。
4. 将相对路径 `uploads/comments/YYYYMM/filename.ext` 记录至 `Comment.image_url`。
5. 在 `card_comments_api(card_id)` 的 item 数据中返回 `"image_url": cm.image_url`。

- [ ] **步骤 2：编写 `tests/test_card_comments_p2.py` 图片上传与限制测试**

- [ ] **步骤 3：运行测试验证 PASS**

运行：`python -m pytest tests/test_card_comments_p2.py`

- [ ] **步骤 4：Commit**

```bash
git add app/routes/user.py tests/test_card_comments_p2.py
git commit -m "feat(comments): implement comment image upload validation and storage endpoint"
```

---

### 任务 3：前端图片选图预览与评论区展示

**文件：**
- 修改：`app/templates/user/card_detail.html`
- 修改：`app/static/css/style.css`

- [ ] **步骤 1：在 `app/static/css/style.css` 中追加图片预览样式**

```css
.dna-comment-img-preview {
  position: relative;
  display: inline-block;
  margin-top: 0.5rem;
}
.dna-comment-img-preview img {
  max-width: 100px;
  max-height: 100px;
  border-radius: 6px;
  border: 1px solid var(--bs-border-color, #dee2e6);
  object-fit: cover;
}
.dna-comment-img-preview .btn-close-img {
  position: absolute;
  top: -6px;
  right: -6px;
  background: var(--bs-danger, #ef4444);
  color: #fff;
  border-radius: 50%;
  width: 18px;
  height: 18px;
  font-size: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border: none;
}

.dna-comment-img-wrap {
  margin-top: 0.5rem;
}
.dna-comment-img {
  max-width: 160px;
  max-height: 160px;
  border-radius: 8px;
  cursor: zoom-in;
  object-fit: cover;
  transition: transform 0.15s ease;
}
.dna-comment-img:hover {
  transform: scale(1.02);
}
```

- [ ] **步骤 2：修改 `app/templates/user/card_detail.html` 评论输入与列表展示**

1. 输入框工具栏：加入图片图标按钮 `<button type="button" class="btn btn-link text-muted p-0 ms-2" id="cmImageBtn"><i class="bi bi-image"></i></button>` 以及隐藏文件域 `<input type="file" id="cmFileInput" accept="image/*" class="d-none">`。
2. 图片选择后：渲染 `#cmImagePreview` 缩略图盒子与 `×` 撤销按钮；选择撤销时清空文件域。
3. `renderItem(cm)` 节点渲染：若 `cm.image_url` 存在，渲染 `<div class="dna-comment-img-wrap"><a href="/static/${cm.image_url}" class="glightbox" data-gallery="comments"><img src="/static/${cm.image_url}" class="dna-comment-img" alt="评论图片"></a></div>`。
4. 渲染后自动刷新 GLightbox 图片大图预览支持。

- [ ] **步骤 3：运行全部测试验证**

运行：`python -m pytest tests/test_card_comments_p2.py tests/test_card_comments_p1.py tests/test_card_comments.py`

- [ ] **步骤 4：Commit**

```bash
git add app/templates/user/card_detail.html app/static/css/style.css
git commit -m "feat(comments): add comment image attachment selection preview and glightbox gallery view"
```
