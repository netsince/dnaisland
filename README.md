# DNAISLAND

DNA island开源存储库

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

> 注意：仓库当前存在多个 alembic head，`flask db upgrade`（不带参数）会因多 head 报错。
> 生产升级请显式指定目标修订，见下方「积分精度迁移」。

### 积分精度迁移（DECIMAL(10,2) → DECIMAL(30,10)）

`migrations/versions/d8e9f0a1b2c3_point_columns_scale10.py` 把 7 个积分列扩宽到 `DECIMAL(30,10)`
（20 位整数 + 10 位小数）。精度常量单点定义在 `app/constants.py`（`POINT_PRECISION` / `POINT_SCALE`），
模型与迁移必须与之一致。

生产库当前位于 `a5b6c7d8e9f0`，升级命令：

```bash
flask db upgrade d8e9f0a1b2c3
```

验证（结果应全部为 `decimal(30,10)`）：

```sql
SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND COLUMN_NAME IN ('points','delta','balance_after','points_gained','points_per_image','points_spent');
```

**回滚有数据损失风险**：`downgrade` 会窄化回 `DECIMAL(10,2)`。若存在小数位 > 2 位的数据会被
四舍五入，整数位 > 8 位的数据会因超范围而报错。回滚前必须先核对：

```sql
SELECT COUNT(*) FROM users WHERE points >= 100000000 OR points <= -100000000;
-- 以及 point_transactions.delta / balance_after、redemption_keys.points、
-- key_usage_logs.points_gained、generation_models.points_per_image、generation_logs.points_spent
```

确认无超范围数据后再执行：

```bash
flask db downgrade a5b6c7d8e9f0
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
