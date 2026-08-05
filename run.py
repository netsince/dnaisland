import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    print(f"PID: {os.getpid()}")
    # debug 通过环境变量控制，默认关闭（生产安全）；
    # 本地开发时设 FLASK_DEBUG=true 开启交互式调试器。
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes", "on")
    # 监听端口通过环境变量控制，默认 5012。
    port = int(os.environ.get("PORT", "5012"))
    # Debug 模式下默认监听 0.0.0.0，开放所有 IP 访问（方便局域网/真机调试）；
    # 如需仅本机访问，设 HOST=127.0.0.1。
    host = os.environ.get("HOST", "0.0.0.0" if debug else "127.0.0.1")
    app.run(debug=debug, host=host, port=port)
