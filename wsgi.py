"""WSGI 入口，供 gunicorn / waitress 等生产 WSGI 服务器使用。

示例：
    gunicorn "wsgi:app" --workers 4 --bind 0.0.0.0:8000        # Linux / macOS
    waitress-serve --port 8000 wsgi:app                        # Windows

此入口不会调用 app.run()，因此始终以生产模式运行（不会启用调试器）。
"""

from app import create_app

app = create_app()
