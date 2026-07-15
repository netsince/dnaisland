# DNAISLAND

基于 Flask 的社区 / 内容网站，支持文章发布、深色 / 浅色主题切换、封面图渐显加载。

## 环境要求

- Python >= 3.13
- MySQL
- 包管理：`uv`（推荐）或 `pip`

## 安装依赖

```bash
cd dnaisland
uv sync            # 推荐，按 uv.lock 锁定版本
# 或： pip install .
```

## 配置

使用 `python-dotenv` 自动加载项目根目录的 `.env`：

```bash
cp .env.example .env    # 然后填入真实值
```

| 变量 | 说明 |
| --- | --- |
| `SECRET_KEY` | 会话签名密钥，**生产必须设为强随机值** |
| `DATABASE_URL` | MySQL 连接串，如 `mysql+pymysql://user:pass@host:3306/dnaisland` |
| `MAIL_*` | SMTP 发信配置 |
| `FLASK_DEBUG` | 调试器开关，`true` 开启，`false` 关闭，**生产务必 `false`** |
| `PORT` | 开发服务器端口，默认 `5012` |

## 数据库迁移

```bash
export FLASK_APP=run:app          # Windows: $env:FLASK_APP="run:app"
flask db upgrade                 # 应用迁移到数据库
```

## 开发模式启动

```bash
# 本地开发：在 .env 中设 FLASK_DEBUG=true，或直接前缀覆盖
FLASK_DEBUG=true uv run python run.py
# 访问 http://localhost:5000
```

## 正式环境部署（WSGI）

开发服务器 `app.run()` 性能差且有调试风险，**生产必须使用 WSGI 服务器**。项目已内置 WSGI 入口 `wsgi.py`。

1. 安装生产依赖（`prod` 可选组，Linux/macOS 装 gunicorn，Windows 装 waitress）：

   ```bash
   uv sync --extra prod          # 或： pip install ".[prod]"
   ```

2. 确保 `.env` 中 `FLASK_DEBUG=false`，然后启动：

   ```bash
   # gunicorn (Linux / macOS)
   # 4核4G 但服务器上还跑着别的项目、流量仅千级：2 进程 + 4 线程足够，
   # 省内存、少占 DB 连接，避免与同机其他项目争资源。
   gunicorn "wsgi:app" --workers 2 --threads 4 --bind 0.0.0.0:5012 --timeout 30
   # waitress (Windows)
   waitress-serve --port 5012 wsgi:app
   ```

   > WSGI 服务器加载的是 `wsgi.py`，不会执行 `run.py` 里的 `app.run()`，因此始终以生产模式运行，不会启用调试器。
   > 端口固定为 5012。

3. （推荐）用 Nginx 反向代理并直接托管静态文件：

   ```nginx
   server {
       listen 80;
       server_name your-domain.com;

       location /static {
           alias /path/to/dnaisland/app/static;
           expires 30d;
       }

       location / {
           proxy_pass http://127.0.0.1:5012;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```

4. （可选）用 systemd 守护进程：

   ```ini
   [Unit]
   Description=DNAISLAND
   After=network.target

   [Service]
   WorkingDirectory=/path/to/dnaisland
   ExecStart=/path/to/dnaisland/.venv/bin/gunicorn "wsgi:app" --workers 2 --threads 4 --bind 0.0.0.0:5012 --timeout 30
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```
