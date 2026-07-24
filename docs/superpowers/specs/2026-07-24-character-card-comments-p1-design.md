# 角色卡评论区 P1 社交与互动增强设计方案

## 1. 概述 (Overview)
本方案为角色卡评论区 P1 阶段的功能增强，包含：**评论点赞/取消点赞**、**“最新 / 最热”排序切换**、**回复与 @ 引用**、**角色卡作者置顶评论**。

---

## 2. 数据模型变更 (`app/models/card.py`)

1. **新建 `CommentLike` 点赞记录表**:
   ```python
   class CommentLike(db.Model):
       __tablename__ = "comment_likes"
       user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
       comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"), primary_key=True)
       created_at = db.Column(db.DateTime, server_default=db.func.now())
   ```

2. **扩展 `Comment` 字段**:
   - `is_pinned = db.Column(db.Boolean, server_default="0", nullable=False, index=True)`：标记创作者置顶。
   - `reply_to_id = db.Column(db.Integer, db.ForeignKey("comments.id"), nullable=True)`：关联被回复的父评论 ID。

---

## 3. 后端路由与 API 拓展 (`app/routes/user.py`)

1. **`card_comments_api(card_id)` 查询增强**:
   - **参数 `sort`**: 支持 `latest` (最新，默认) 和 `hottest` (最热，按点赞数倒序)。
   - **置顶逻辑**: 无论怎么排序/分页，被作者置顶的评论（`is_pinned=True`）始终优先在第 1 页最顶部排布。
   - **返回数据项**:
     - `like_count` (`int`): 该评论获得的点赞数。
     - `liked` (`bool`): 当前登录用户是否已点赞。
     - `is_pinned` (`bool`): 是否处于置顶状态。
     - `can_pin` (`bool`): 当前用户是否为角色卡作者或管理员。
     - `reply_to` (`dict | null`): 包含被回复对象的 `id` 与 `display_name`。

2. **新增 API 路由**:
   - **评论点赞/取消点赞**: `POST /card/<card_id>/comment/<int:comment_id>/like`
     - 切换点赞状态，返回 `{"ok": True, "liked": bool, "count": int}`。
   - **作者置顶/取消置顶**: `POST /card/<card_id>/comment/<int:comment_id>/pin`
     - 校验是否为卡片作者或管理员；切换 `is_pinned` 字段并提交。

3. **提交评论 `card_comment(card_id)` 扩展**:
   - 支持解析 `reply_to_id` 表单参数；当包含合法回复 ID 时，写入 `Comment.reply_to_id`。

---

## 4. 数据库迁移 (Migration)

修改 `app/models/card.py` 后，运行 Flask-Migrate 生成迁移文件并升级数据库：
```bash
flask db migrate -m "add comment likes and pinned status"
flask db upgrade
```

---

## 5. 前端 UI 与交互重构 (`card_detail.html` & `style.css`)

1. **抽屉头部增加排序 Tabs**:
   - 在评论抽屉 Header 放置“最新”和“最热”按钮，点击切换 `sort` 参数并重新拉取列表。
2. **置顶卡片样式**:
   - 置顶评论渲染 `📌 置顶` 专属徽章，并赋予高亮金黄色/紫色微光边框；作者可在操作栏看到“置顶”/“取消置顶”切换按钮。
3. **点赞交互**:
   - 每条评论展示爱心/手势点赞图标及数字。点击触发 AJAX 点赞，高亮爱心并动态更新数字。
4. **回复与 @ 引用**:
   - 每条评论显示“回复”按钮；点击自动在输入框填入 `@用户名 `，并存储 `reply_to_id`。
   - 评论列表内展示“回复 @用户名”。

---

## 6. 测试与验证计划 (Testing Plan)

1. **数据库迁移测试**: 验证 Migration 顺利生成与应用，旧数据完整不受影响。
2. **点赞与取消点赞**: 验证用户可正常点赞/取消，点赞数与列表高亮状态一致，非登录用户阻断。
3. **最新/最热排序**: 验证切换 Tabs 后按点赞数与按时间正确重排。
4. **作者置顶**: 验证作者可成功置顶 1~2 条评论，置顶评论永远固定在第 1 页头部；非作者不可操作。
5. **回复关联**: 验证带 `reply_to_id` 评论提交后，列表准确展示被回复人姓名。
