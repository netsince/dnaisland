import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # debug 通过环境变量控制，默认关闭（生产安全）；
    # 本地开发时设 FLASK_DEBUG=true 开启交互式调试器。
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes", "on")
    # 监听端口通过环境变量控制，默认 5012。
    port = int(os.environ.get("PORT", "5012"))
    app.run(debug=debug, port=port)
