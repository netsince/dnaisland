#!/usr/bin/env python
"""模板静态 lint：扫描全部 Jinja 模板，定位“被当作全局函数/过滤器/测试调用、
却未注册进 Jinja env”的隐形 bug（如未注册的 _ / current_user / linkify 等）。

原理：解析每个模板的 AST，收集被 Call 的 Name、被用作 filter/test 的名字，
以及本文件内定义的宏 / from-import 进来的宏；凡是不在上述白名单中的调用名，
即为未注册全局函数——这正是运行时才暴露的“_ is undefined”类错误。

用法：
    uv run lint:templates
    python scripts/lint_templates.py        # 在已激活的 venv 中直接运行
退出码：发现问题时为 1，全部通过为 0（可用于 CI / pre-commit）。
"""
import os
import sys

# 让脚本可在项目根目录直接执行（无需安装为包）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from jinja2 import nodes
from app import create_app

# Jinja2 内建名字（可被当作函数调用，且无需注册）
JINJA_BUILTINS = {
    "range", "dict", "lipsum", "cycler", "joiner", "namespace",
}
# Flask 注入的全局（已在 env.globals，保险起见一并列入白名单）
FLASK_GLOBALS = {"url_for", "get_flashed_messages", "config", "request", "session", "g"}


def main() -> int:
    app = create_app()
    env = app.jinja_env

    registered_globals = set(env.globals.keys()) | JINJA_BUILTINS | FLASK_GLOBALS
    registered_filters = set(env.filters.keys())
    registered_tests = set(env.tests.keys())

    template_names = env.list_templates(extensions=["html"])

    issues = []
    for name in template_names:
        try:
            src = env.loader.get_source(env, name)[0]
            ast = env.parse(src)
        except Exception as e:  # noqa
            issues.append((name, f"解析失败: {type(e).__name__}: {e}"))
            continue

        # 本模板 import 进来的宏名 + 本文件内 {% macro %} 定义的宏名
        allowed_macros = set()
        for node in ast.find_all((nodes.FromImport, nodes.Import, nodes.Macro)):
            if isinstance(node, nodes.FromImport):
                for n in node.names:
                    allowed_macros.add(n if isinstance(n, str) else n[0])
            elif isinstance(node, nodes.Import):
                allowed_macros.add(node.target)
            else:  # Macro 定义
                allowed_macros.add(node.name)

        # 被当作函数调用的 Name（排除属性调用 a.b()）
        for call in ast.find_all(nodes.Call):
            func = call.node
            if isinstance(func, nodes.Name):
                nm = func.name
                if nm in registered_globals or nm in allowed_macros:
                    continue
                issues.append((name, f"调用了未注册的全局函数: {nm}()"))

        # 过滤器
        for flt in ast.find_all(nodes.Filter):
            if flt.name not in registered_filters:
                issues.append((name, f"使用了未注册的过滤器: |{flt.name}"))

        # 测试
        for tst in ast.find_all(nodes.Test):
            if tst.name not in registered_tests:
                issues.append((name, f"使用了未注册的测试: is {tst.name}"))

    print(f"模板 lint 完成：共扫描 {len(template_names)} 个模板")
    if issues:
        print(f"发现 {len(issues)} 个问题：\n")
        for name, msg in issues:
            print(f"  [问题] {name}\n        {msg}\n")
        return 1

    print("未发现未注册全局函数 / 过滤器 / 测试。没有 _ 这类隐形 bug。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
