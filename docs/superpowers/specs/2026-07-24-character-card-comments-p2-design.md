# 角色卡评论区 P2 进阶功能设计方案 (Notifications, Image Attachment & Safety)

## 1. 概述 (Overview)
本方案为角色卡评论区 P2 阶段的功能升级，涵盖：**评论站内消息通知联动**（点赞/回复/评论卡片提醒）、**评论带图/截图附件功能**（带有缩略图预览与全屏大图查看），以及**评论敏感词拦截与举报体验增强**。

---

## 2. 涉及改动模块 (Scope & Affected Files)

- **数据模型 (`app/models/card.py`)**:
  - `Comment` 表追加 `image_url = db.Column(db.String(255), nullable=True)` 字段，用于存储评论附件图片相对路径。
- **服务层与通知联动 (`app/services/notification_service.py` & `app/routes/user.py`)**:
  - `card_comment_like`: 点赞他人评论时触发 `comment_like` 站内通知。
  - `card_comment`:
    - 回复他人评论时触发 `comment_reply` 站内通知。
    - 在他人卡片下发表评论时触发 `card_comment` 站内通知提醒卡片创作者。
    - 增加敏感词拦截校验。
    - 支持处理单张评论图片上传并存储至 `app/static/uploads/comments/`。
- **前端页面与样式 (`app/templates/user/card_detail.html` & `app/static/css/style.css`)**:
  - 评论输入框下方工具栏增加“上传图片”按钮，选中图片后支持实时缩略图预览与撤销。
  - 评论列表中渲染缩略图，点击可使用 GLightbox 或弹窗全屏预览。
  - 评论项举报按钮优化及反馈弹窗。

---

## 3. 详细设计 (Detailed Design)

### 3.1 后端通知与图片处理 (`app/routes/user.py`)

#### 1) 站内通知触发规则
- **点赞通知**: 当用户 A 点赞用户 B 的评论（且 A != B）时，调用：
  `notify(user_id=B.id, message=f"{A.display_name} 点赞了你在《{card.name}》下的评论", type_="comment_like", related_card_id=card.id)`
- **回复通知**: 当用户 A 在《card.name》下回复用户 B 的评论（且 A != B）时，调用：
  `notify(user_id=B.id, message=f"{A.display_name} 在《{card.name}》中回复了你：\"{content[:30]}\"", type_="comment_reply", related_card_id=card.id)`
- **卡片作者通知**: 当用户 A 在角色卡《card.name》下发布评论（且 A != card.author_id），且未触发回复 B 时，通知卡片作者：
  `notify(user_id=card.author_id, message=f"{A.display_name} 在你的角色卡《{card.name}》下发表了评论", type_="card_comment", related_card_id=card.id)`

#### 2) 图片上传与保存
- 支持格式: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`。
- 文件大小限制: `<= 5MB`。
- 保存目录: `app/static/uploads/comments/YYYYMM/`。生成随机文件名保存，数据库记录相对路径 `uploads/comments/YYYYMM/filename.png`。

---

### 3.2 数据库迁移 (Migration)

在 `Comment` 模型加入 `image_url` 后，运行 Migration:
```bash
flask db migrate -m "add image_url to comments"
flask db upgrade
```

---

### 3.3 前端渲染与交互设计 (`card_detail.html` & `style.css`)

1. **输入框附件图片栏**:
   - 输入框下方工具栏显示图片图标按钮；
   - 点击选择文件后，下方展开预览小图及“移除”小红叉 `×`；
   - 提交表单时使用 `FormData` 包含 `image` 文件。
2. **评论列表图片展示**:
   - `renderItem(cm)` 中：若 `cm.image_url` 存在，渲染 `<div class="dna-comment-image-wrap"><img src="/static/${cm.image_url}" class="dna-comment-img glightbox" data-gallery="comment-images"></div>`。

---

## 4. 测试与验证计划 (Testing Plan)

1. **数据库迁移验证**: 验证 `image_url` 字段顺利迁移入库。
2. **消息通知自动化测试**:
   - 测试点赞别人评论触发通知，点赞自己评论不发送通知。
   - 测试回复别人评论触发通知，回复自己不发送通知。
   - 测试在他人卡片发表评论通知作者。
3. **图片上传与渲染测试**:
   - 上传合法图片评论，检查文件保存、数据库记录及列表图片预览渲染。
   - 上传超大文件或非法文件格式，检查阻断拦截。
