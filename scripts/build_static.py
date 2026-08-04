#!/usr/bin/env python
"""构建静态资源压缩版（当前仅 CSS minify）。

对 app/static/css/style.css 做保守压缩，产出 style.min.css。
模板侧通过 staticv()（见 app/utils.py）在存在 .min 版本时自动引用压缩版并加
内容指纹；未运行本脚本则回退到原文件，不会 404。

压缩仅做不影响样式的安全操作：
- 去掉 /* ... */ 注释
- 去掉每行首尾空白
- 压缩连续空白为单个空格
- 去掉紧邻 { } : ; , > 的空白

用法：
    uv run python scripts/build_static.py
    python scripts/build_static.py      # 在已激活的 venv 中直接运行
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_SRC = os.path.join(ROOT, "app", "static", "css", "style.css")
CSS_DST = os.path.join(ROOT, "app", "static", "css", "style.min.css")

# 去掉注释（CSS 注释不会出现在字符串字面量中，安全）
_RE_COMMENT = re.compile(r"/\*[\s\S]*?\*/")
# 去掉紧邻 { } : ; , > 的空白（不触碰引号内文本）
_RE_BRACE_SPACE = re.compile(r"\s*([{}:;,>])\s*")


def minify_css(css: str) -> str:
    css = _RE_COMMENT.sub("", css)
    lines = [line.strip() for line in css.splitlines() if line.strip()]
    css = " ".join(lines)
    css = _RE_BRACE_SPACE.sub(r"\1", css)
    return css


def main() -> int:
    if not os.path.exists(CSS_SRC):
        print(f"未找到源文件：{CSS_SRC}")
        return 1
    with open(CSS_SRC, encoding="utf-8") as f:
        source = f.read()
    out = minify_css(source)
    with open(CSS_DST, "w", encoding="utf-8") as f:
        f.write(out)
    print(
        f"style.min.css 已生成：{len(source)} -> {len(out)} 字符 "
        f"（{len(source) - len(out)} 减少）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
