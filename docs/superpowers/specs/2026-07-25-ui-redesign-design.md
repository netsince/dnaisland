# DNAISLAND 视觉 UI 彻底重构规格说明书 (Obsidian Monochrome UI Design Spec)

- **日期**：2026-07-25
- **项目**：DNAISLAND (`netsince/dnaisland`)
- **目标**：彻底重构全站视觉设计系统，摒弃刺眼的花哨大渐变背景、发光阴影与乱选的二次元色彩，替换为极简、硬朗、高可读性且高质感的黑白灰暗色系统（Obsidian Monochrome Design System）。

---

## 1. 全局设计 Token 与色彩系统

我们将覆盖原有的 CSS 变量，建立一致的微观 Token。

### 1.1 色彩 Token (`:root` / `[data-bs-theme="dark"]`)
- `--dna-bg`: `#09090b`（纯粹深黑背景，替换原本包含 3 个径向渐变大光晕的刺眼背景）
- `--dna-bg-2`: `#121215`（二级容器/侧边栏/沉淀区）
- `--dna-surface`: `#121215`（卡片与表面通用底色）
- `--dna-surface-hover`: `#18181b`（卡片悬浮态底色）
- `--dna-border`: `#27272a`（主微弱细边框 1px solid）
- `--dna-border-strong`: `#3f3f46`（强调边框/Focus 态边框）
- `--dna-text`: `#f4f4f5`（主体高对比高可读文本）
- `--dna-muted`: `#a1a1aa`（次要文本、作者信息、赞数与元数据）
- `--dna-accent`: `#f4f4f5`（主强调操作，采用高对比黑白反色）
- `--dna-radius`: `8px`（统一卡片圆角，代替原本 16px 过大圆角）
- `--dna-radius-sm`: `6px`（统一按钮与标签小圆角）
- `--dna-shadow`: `none`（彻底移除 box-shadow 刺眼发光特效）
- `--dna-glow`: `none`（移除所有荧光 Outline）

### 1.2 排版与底纹
- `body`: 背景色设定为 `var(--dna-bg)` (`#09090b`)，彻底移除 `background-image: radial-gradient(...)`。
- 字体依然保留标准无衬线字体 `Noto Sans SC` 与系统默认无衬线栈。

---

## 2. 组件与页面排版重构规范

### 2.1 导航栏 (`.navbar`)
- 属性：固定顶部 (`position: sticky; top: 0; z-index: 1030;`)
- 样式：背景色采用 `rgba(9, 9, 11, 0.85)` 配合 `backdrop-filter: blur(12px)`，底边框为 `1px solid var(--dna-border)`。
- Logo (`.navbar-brand`)：取消原本紫粉双色渐变字，改为高对比纯白黑体 (`color: #fff; font-weight: 800; letter-spacing: 1px;`)。
- 导航链接 (`.nav-link`)：默认中性灰 (`#a1a1aa`)，Hover / Active 变为纯白 (`#ffffff`)，去除炫光高亮。

### 2.2 角色卡网格与卡片 (`.card`, `.character-card`)
- 边框：`1px solid var(--dna-border)` (`#27272a`)
- 底色：`var(--dna-surface)` (`#121215`)
- Hover 交互：仅做微小位移 (`transform: translateY(-2px);`) 与边框变深 (`border-color: #3f3f46;`)，彻底取消紫/粉色 glow 阴影扩散。
- 角色卡标签 (Tags)：统一为暗灰扁平 Chip (`background: #18181b; color: #a1a1aa; border: 1px solid #27272a;`)。

### 2.3 按钮系统 (`.btn`)
- `.btn-primary`: 背景 `#f4f4f5`，文字 `#09090b`，无阴影，悬浮时亮灰 (`#ffffff`)。
- `.btn-outline-primary`: 边框 `#3f3f46`，文字 `#f4f4f5`，悬浮背景 `#18181b`。
- `.btn-outline-secondary` / `.btn-outline-light`: 边框 `#27272a`，文字 `#a1a1aa`，悬浮背景 `#18181b`，文字变白。

### 2.4 表单控制件 (`.form-control`, `.form-select`)
- 底色：`#121215`
- 边框：`1px solid #27272a`
- Focus 状态：边框变为 `#52525b`，box-shadow 设为 `0 0 0 2px rgba(255,255,255,0.1)`。

### 2.5 茶馆 Feed、评论与点赞
- 去除多余的彩光 Badge。
- 点赞数、评论数、打赏状态统一采用 Icon + 中性灰文字。

---

## 3. 实现计划与文件改动列表

1. **`app/static/css/style.css`**：彻底替换全局变量与 Bootstrap 覆盖类，清除任何包含 `#a855f7`、`#ec4899`、`radial-gradient` 的样式规则。
2. **`app/templates/base.html`**：清理顶部的内联渐变折叠/遮罩残留，确认结构契合全新极简设计。

---

## 4. 验证与成功标准
- 页面加载时无任何径向渐变大背景，呈现纯深黑黑曜石质感。
- 角色卡大厅、茶馆 feed、AI 画图、个人中心与后台等所有页面均具有一致的高对比排版与极简线条。
- 浏览器控制台无 CSS 加载错误。
