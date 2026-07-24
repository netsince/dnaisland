# Obsidian Monochrome UI 重构实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 DNAISLAND 全站视觉系统由刺眼花哨的大渐变/紫粉霓虹样式彻底重构为极简、沉稳、高对比度与高可读性的黑白灰暗色系统 (Obsidian Monochrome UI System)。

**架构：** 在 `app/static/css/style.css` 中重写品牌 Token (`:root` 与 `[data-bs-theme="dark"]`)，覆盖 Bootstrap 5 默认变量，替换 `body` 的 `radial-gradient` 背景，统一所有卡片 (`.card`)、导航 (`.navbar`)、按钮 (`.btn`)、表单 (`.form-control`) 及 Badge 的边框与色彩基线，并在 `base.html` 中进行结构清理。

**技术栈：** CSS / HTML5 / Bootstrap 5 Overrides / Jinja2 Templates / Flask

---

### 任务 1：重写全局基础 Token 与 Body 背景规则

**文件：**
- 修改：`app/static/css/style.css:1-75`

- [ ] **步骤 1：修改 `app/static/css/style.css` 中的 `:root` 变量与 `body` 样式**

在 `app/static/css/style.css` 顶部重写 `:root` 与 `body`：

```css
:root {
  /* Obsidian Monochrome 变量系统 */
  --dna-accent: #f4f4f5;
  --dna-accent-rgb: 244, 244, 245;
  --dna-accent-2: #e4e4e7;
  --dna-accent-3: #d4d4d8;
  --dna-grad: linear-gradient(180deg, #f4f4f5 0%, #e4e4e7 100%);
  --dna-grad-soft: rgba(255, 255, 255, 0.05);

  /* 背景与表面 */
  --dna-bg: #09090b;
  --dna-bg-2: #121215;
  --dna-surface: #121215;
  --dna-surface-solid: #121215;
  --dna-border: #27272a;
  --dna-border-strong: #3f3f46;

  /* 文本 */
  --dna-text: #f4f4f5;
  --dna-muted: #a1a1aa;

  --dna-radius: 8px;
  --dna-radius-sm: 6px;
  --dna-shadow: none;
  --dna-glow: none;

  /* —— 覆盖 Bootstrap 主题变量 —— */
  --bs-body-bg: var(--dna-bg);
  --bs-body-color: var(--dna-text);
  --bs-border-color: var(--dna-border);
  --bs-primary: #f4f4f5;
  --bs-primary-rgb: 244, 244, 245;
  --bs-link-color: #f4f4f5;
  --bs-link-hover-color: #ffffff;
  --bs-emphasis-color: #ffffff;
}

[data-bs-theme="dark"] {
  --bs-body-bg: var(--dna-bg);
  --bs-body-color: var(--dna-text);
  --bs-secondary-color: var(--dna-muted);
  --bs-tertiary-bg: var(--dna-surface-solid);
  --bs-border-color: var(--dna-border);
  --bs-emphasis-color: #ffffff;
}

/* ---------------- 全局背景 ---------------- */
body {
  background-color: var(--dna-bg);
  color: var(--dna-text);
  background-image: none; /* 彻底移除原刺眼大面积径向渐变 */
  font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, -apple-system, sans-serif;
  -webkit-font-smoothing: antialiased;
}
```

- [ ] **步骤 2：检查静态 CSS 文件并验证样式语法**

运行：`git diff app/static/css/style.css`
预期：确认旧的紫粉径向渐变已被替换为极简 `#09090b` 底色。

- [ ] **步骤 3：Commit**

```bash
git add app/static/css/style.css
git commit -m "style: replace global gradients with obsidian monochrome design tokens"
```

---

### 任务 2：重构导航栏 (Navbar) 与品牌 Logo 样式

**文件：**
- 修改：`app/static/css/style.css:75-105`

- [ ] **步骤 1：修改 `app/static/css/style.css` 中导航栏分类样式**

修改 `.navbar.bg-dark` 与 `.navbar-brand`：

```css
/* ---------------- 导航栏 ---------------- */
.navbar.bg-dark {
  background: rgba(9, 9, 11, 0.85) !important;
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--dna-border);
}
.navbar { position: sticky; top: 0; z-index: 1030; }
.navbar-brand {
  font-weight: 800;
  letter-spacing: 1px;
  background: none;
  -webkit-background-clip: initial;
  background-clip: initial;
  color: #ffffff !important;
}
.navbar .nav-link {
  color: var(--dna-muted) !important;
  font-weight: 500;
  border-radius: var(--dna-radius-sm);
  padding-inline: 0.75rem;
  transition: color 0.15s ease, background 0.15s ease;
}
.navbar .nav-link:hover,
.navbar .nav-link.active { color: #ffffff !important; background: rgba(255, 255, 255, 0.06); }
.navbar .dropdown-menu {
  background-color: var(--dna-surface);
  border: 1px solid var(--dna-border);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
}
```

- [ ] **步骤 2：检查静态 CSS 导航栏变更**

运行：`git diff app/static/css/style.css`
预期：确认 `.navbar-brand` 取消渐变色变白，导航栏底色为高对比深黑半透明。

- [ ] **步骤 3：Commit**

```bash
git add app/static/css/style.css
git commit -m "style: refactor navbar to crisp dark translucent obsidian navigation"
```

---

### 任务 3：重构 卡片 (Card)、列表与 Badge 体系

**文件：**
- 修改：`app/static/css/style.css:105-145`

- [ ] **步骤 1：修改 `app/static/css/style.css` 中的卡片与 Badge 样式**

```css
/* ---------------- 卡片 / 表面 ---------------- */
.card {
  border: 1px solid var(--dna-border);
  border-radius: var(--dna-radius);
  background: var(--dna-surface);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  box-shadow: none;
  color: var(--dna-text);
  transition: border-color 0.15s ease, transform 0.15s ease;
}
.card:hover {
  border-color: var(--dna-border-strong);
}
.card-header {
  background: rgba(255, 255, 255, 0.02);
  border-bottom: 1px solid var(--dna-border);
  color: #ffffff;
  font-weight: 600;
}
.list-group-item {
  background: var(--dna-surface);
  border-color: var(--dna-border);
  color: var(--dna-text);
}
.list-group-item-primary { background: rgba(255, 255, 255, 0.06); color: #ffffff; }

/* ---------------- 徽章 ---------------- */
.badge { font-weight: 500; letter-spacing: 0.2px; border-radius: 4px; }
.text-bg-primary, .badge.bg-primary {
  background: #18181b !important;
  color: #a1a1aa !important;
  border: 1px solid var(--dna-border);
}
```

- [ ] **步骤 2：验证卡片样式**

运行：`git diff app/static/css/style.css`
预期：确认移除所有强 Glow 特效，圆角统一为 `var(--dna-radius)` (`8px`)。

- [ ] **步骤 3：Commit**

```bash
git add app/static/css/style.css
git commit -m "style: redesign cards and badges for minimal dark surface hierarchy"
```

---

### 任务 4：重构 按钮 (Btn)、表单控制件与 Toast

**文件：**
- 修改：`app/static/css/style.css:125-180`

- [ ] **步骤 1：更新按钮与表单控制件样式规则**

```css
/* ---------------- 按钮 ---------------- */
.btn {
  border-radius: var(--dna-radius-sm);
  font-weight: 600;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.btn:active { transform: translateY(1px); }
.btn-primary {
  border: none;
  background: #f4f4f5;
  color: #09090b;
  box-shadow: none;
}
.btn-primary:hover, .btn-primary:focus {
  background: #ffffff;
  color: #09090b;
  box-shadow: none;
}
.btn-outline-primary {
  border-color: var(--dna-border-strong);
  color: var(--dna-text);
}
.btn-outline-primary:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: #ffffff;
  color: #ffffff;
}

/* ---------------- 表单 ---------------- */
.form-control, .form-select {
  background: #121215;
  border: 1px solid var(--dna-border);
  color: var(--dna-text);
  border-radius: var(--dna-radius-sm);
}
.form-control:focus, .form-select:focus {
  background: #121215;
  border-color: var(--dna-border-strong);
  color: #ffffff;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.1);
}
```

- [ ] **步骤 2：验证按钮与表单控制件更新**

运行：`git diff app/static/css/style.css`
预期：确认主按钮为高对比黑白反色，无任何粉色/紫色彩阴影。

- [ ] **步骤 3：Commit**

```bash
git add app/static/css/style.css
git commit -m "style: update buttons, inputs, and form controls to monochrome contrast theme"
```

---

### 任务 5：清理由基础模板及其他结构引发的样式冲突并运行自动化全系统测试

**文件：**
- 修改：`app/templates/base.html:58-67`
- 测试：`pytest`

- [ ] **步骤 1：清理 `app/templates/base.html` 中茶馆折叠处的透明梯度**

将 `base.html` 中的内联样式线性渐变调整为适配 Obsidian Monochrome 底色 (`var(--bs-body-bg)`):

```html
 <style>
  /* 茶馆长文折叠 */
  .js-post-body.is-folded { max-height: 9em; overflow: hidden; position: relative; }
  .js-post-body.is-folded::after {
    content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 2.5em;
    background: linear-gradient(transparent, var(--bs-body-bg));
  }
  .js-post-fold { font-size: .85rem; }
  .dna-post-edited { opacity: .7; }
 </style>
```

- [ ] **步骤 2：运行 Pytest 校验功能完整性**

运行：`uv run pytest`
预期：所有后端路由与模板渲染测试全部通过 (PASS)。

- [ ] **步骤 3：Commit**

```bash
git add app/templates/base.html
git commit -m "refactor: optimize base template styling and verify clean test suite"
```
