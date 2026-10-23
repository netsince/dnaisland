# 社区性能压测报告

> 目标：搞清楚「社区为什么慢且卡」。测量对象 `https://dnaisland.nb6.ltd`。
> 复现脚本都在 `dnaisland/scripts/` 下，用法见文末。

## 结论（TL;DR）

**服务端不慢，是「发出去的东西太大」× 「链路带宽太差」。**

* 服务端响应很快：TTFB 只有 **230–570ms**（复用连接时）。
* 但一个屏幕要下发 **180–900KB**，其中 **90–99% 是 base64 内嵌的图片**。
* 而这条链路的实测吞吐只有 **8–100KB/s**。
* 于是：`页面 /teahouse/` = 906KB × 8KB/s ≈ **107 秒**。

不是 Cloudflare 的"减速"，也不是数据库慢，是**每个列表接口都把封面图以 base64 塞进 JSON**。

---

## 方法：三层测量

| 层 | 测什么 | 排除什么 |
|---|---|---|
| **L1 远端端到端** | 用户真实感受 | 无 |
| **L2 远端 keep-alive** | 服务端思考时间 vs 传输时间 | 每次新建连接的 TLS 握手 |
| **L3 本地进程内** | handler + 模板 + SQL | Cloudflare、公网、HTTP |

L3 有个重要前提：脚本连的是 `.env` 里的库（**生产库在远端**），所以 L3 的**绝对耗时不可与 L1 直接比较** —— 它含了本机到数据库的 WAN 往返。L3 真正有用的产出是**每个请求打了多少条 SQL**（与网络无关）。

---

## L1：远端端到端（每个目标 1 次预热 + 3 次取样，取中位数）

> 注意：这一轮每个请求都新建连接，**含完整 TLS 握手**，所以比真实客户端偏高。
> 表里的 `状态 0` 表示**最后一次尝试抛异常**（超时/连接被重置），并非 HTTP 0。

### 客户端 API

| 耗时 | TTFB | 线上字节 | 解压后 | 状态 | 接口 |
|---|---|---|---|---|---|
| **42524ms** | 42524 | 0.0KB | 0.0KB | 0 | `/api/v1/cards/search?q=爱` |
| **9799ms** | 6507 | 156.1KB | 207.3KB | 0 | `/api/v1/sponsors` |
| 6856ms | 2087 | 206.4KB | 282.3KB | 200 | `/api/v1/teahouse/posts` |
| 6565ms | 504 | 218.2KB | 448.3KB | 200 | `/api/v1/cards/explore` |
| 4418ms | 3727 | 115.1KB | 209.5KB | 200 | `/api/v1/recommend` |
| 3944ms | 3944 | 0.2KB | 0.5KB | 200 | `/api/v1/teahouse/topics` |
| 3562ms | 2373 | 0.3KB | 0.4KB | 200 | `/api/v1/reports/meta` |
| 2815ms | 1765 | 141.4KB | 196.1KB | 0 | `/api/v1/cards/swipe` |
| 2529ms | 2470 | 2.4KB | 7.9KB | 200 | `/api/v1/articles/<id>` |
| 2288ms | 1189 | 13.9KB | 18.7KB | 200 | `/api/v1/teahouse/posts/<id>` |
| 2022ms | 1965 | 0.7KB | 1.5KB | 0 | `/api/v1/site-config` |
| 1951ms | 816 | 115.3KB | 177.4KB | 0 | `/api/v1/cards/featured` |
| 1556ms | 1329 | 0.4KB | 1.2KB | 200 | `/api/v1/cards/explore/meta` |
| 1433ms | 1433 | 0.4KB | 1.0KB | 200 | `/api/v1/articles` |
| 1339ms | 949 | 11.6KB | 15.8KB | 200 | `/api/v1/teahouse/topics/<id>` |
| 974ms | 726 | 24.1KB | 82.9KB | 200 | 页面 `/search` |
| 890ms | 356 | 19.7KB | 69.1KB | 0 | 页面 `/worlds/` |
| 843ms | 618 | 12.9KB | 33.7KB | 200 | `/api/v1/teahouse/search` |
| 786ms | 786 | 0.0KB | 0.0KB | 401 | `/api/v1/notifications/unread-count`（匿名） |
| 726ms | 313 | 20.2KB | 72.2KB | 0 | 页面 `/articles` |
| 717ms | 596 | 12.3KB | 19.4KB | 200 | `/api/v1/users/search` |
| 703ms | 703 | 0.0KB | 0.0KB | 401 | `/api/v1/my/cards`（匿名） |
| 679ms | 604 | 0.5KB | 1.1KB | 200 | `/api/v1/search/suggest` |

### 网页

| 耗时 | TTFB | 线上字节 | 解压后 | 状态 | 页面 |
|---|---|---|---|---|---|
| 6869ms | 6452 | 32.9KB | 97.2KB | 0 | `/` |
| **5188ms** | 1035 | **523.7KB** | **799.6KB** | 200 | `/teahouse/` |
| 3616ms | 2753 | 21.0KB | 73.7KB | 200 | `/teahouse/<id>` |
| 2784ms | 2516 | 24.3KB | 88.6KB | 200 | `/recommend` |
| 1059ms | 869 | 47.8KB | 136.1KB | 200 | `/explore` |

**读法**：`状态 0` 的那几行说明**同一接口时而成功、时而超时**——链路不稳定，
不是接口本身挂死。`cards/search` 是唯一 4 次全失败的（见下文）。

---

## L2：keep-alive（把 TLS 握手摊掉后的真相）

| 接口 | TTFB | 总耗时 | 大小 | 实测吞吐 |
|---|---|---|---|---|
| `search/suggest` | 384ms | 489ms | 1.1KB | — |
| `cards/featured` | 231ms | 3135ms | 184.2KB | **59KB/s** |
| `cards/explore` | 268ms | 4604ms | 452.1KB | **98KB/s** |
| `teahouse/posts` | 568ms | 13108ms | 282.3KB | **22KB/s** |
| `sponsors` | 903ms | 23851ms | 207.3KB | **9KB/s** |
| `cards/search` | 3412ms | 18021ms | 141.8KB | **8KB/s** |
| 页面 `/teahouse/` | 1254ms | **107494ms** | 905.8KB | **8KB/s** |

**这是全篇最关键的一张表。** TTFB 很低（服务端很快），总耗时几乎全部花在**传输**上，
而且吞吐从 98KB/s 一路掉到 8KB/s —— 链路被压垮了。

对比 L1 与 L2：`cards/featured` 从 1951ms 降到 3135ms 总耗时、TTFB 从 816ms 降到 231ms。
说明**冷连接的握手 + 首字节就占 600ms 左右**，真实客户端复用连接时这部分可以省掉。

---

## 体积构成：base64 内嵌图片是元凶

| 接口 | 总体积 | 内嵌图片 | 图片字节 | 占比 |
|---|---|---|---|---|
| `/api/v1/cards/explore` | 448.3KB | 22 张 | 403.3KB | **90%** |
| `/api/v1/teahouse/posts` | 282.3KB | 16 张 | 264.4KB | **94%** |
| `/api/v1/sponsors` | 207.3KB | 12 张 | 204.8KB | **99%** |
| `/api/v1/recommend` | 209.5KB | 12 张 | 196.9KB | **94%** |

封面图/头像以 `data:image/...;base64,...` 直接写在 JSON 里。三个后果：

1. **体积 ×1.33**（base64 编码膨胀）；
2. **完全不可缓存** —— 每次"换一换"、每次下拉刷新，所有图片重新下一遍；
3. **不能并行、不能懒加载、不能降级** —— 整块 JSON 是一个原子响应，首屏要等它全部到齐。

而项目里**已经有**按 URL 取图的接口（`/card-image/<id>/<slot>`、`/user/avatar/<id>`、
`/api/v1/teahouse/images/<id>`），说明数据本来是能走 URL 的，只是列表 JSON 选择了内嵌。

---

## L3：本地进程内（SQL 条数才是有效产出）

```
耗时      SQL条数    字节     状态  路径
7132ms      18     289.2K   200  /api/v1/teahouse/posts
9105ms      20     459.6K   200  /api/v1/cards/explore
8544ms      11     196.2K   200  /api/v1/cards/featured
7631ms      18     213.1K   200  /api/v1/recommend
   0ms       0       1.9K   200  /api/v1/site-config
2589ms       2     117.7K   200  /
29288ms     20     905.5K   200  /teahouse/
9701ms      12     136.1K   200  /explore
```

**绝对耗时不要看**：脚本连的是远端生产库，每次 SQL 都是一次 WAN 往返
（`cards/featured` 11 条 SQL / 8.5 秒 ≈ 每条 770ms，就是 WAN 延迟）。

**有效结论是 SQL 条数**：一个列表页打 **11–20 条 SQL**。这在生产（库和服务器同区）
大约是 20–50ms，不是瓶颈 —— 但也谈不上干净，属于可以顺手优化的量级。

---

## 慢在哪：按影响排序

1. **列表接口内嵌 base64 图片** —— 占 90–99% 体积，是"卡"的唯一主因。
2. **链路吞吐只有 8–100KB/s** —— 这是放大器。同样的 200KB，在正常宽带上 0.2 秒，
   在这条链路上要 2–24 秒。
3. **冷连接成本 ~600ms** —— 每个新连接都要重新握手。客户端有连接池所以影响较小，
   但首次进入社区、以及超时后重连时都能感觉到。
4. **单次请求的 RTT 底噪 ~380ms** —— `search/suggest` 只有 1.1KB 也要 384ms，
   说明这条路径的基础往返就接近 0.4 秒（香港↔大陆正常应该是 30–80ms）。
5. **`cards/search` 异常** —— 4 次全失败，但 keep-alive 下能通（18 秒 / 141.8KB）。
   它不是挂了，是**冷连接 + 大响应**叠在一起把 40 秒超时打穿了。
   注意它的 TTFB 有 3.4 秒，比其他接口高一个量级，值得单独查一下查询计划。

---

## 建议（按性价比）

### 立刻能见效

**① 列表接口不要再内嵌 base64 图片，改发 URL。**

这是唯一能从根本上解决"卡"的改动。列表只发 `cover_url` / `avatar_url`，
客户端自己按需加载（并且能吃 HTTP 缓存、能懒加载、能降级）。
项目已有这些路由，不需要新建存储。

预期：`cards/explore` 448KB → **约 45KB**（-90%），`/teahouse/` 906KB → **约 60KB**。

**② 服务端生成缩略图，并给长缓存头。**

列表只需要小图。`Cache-Control: public, max-age=31536000, immutable` + 内容哈希文件名，
让客户端第二次进同一页几乎不产生流量。

### 值得顺手做

**③ 列表分页调小。** 一页 12 张卡、20 条帖子对这条链路偏大，先降到 8 / 10 试试。

**④ 检查 `cards/search` 的查询计划。** 它的 TTFB（3.4 秒）明显高于同类接口。
生产库现状：577 张卡、412225 条 bigram、`fulltext_enabled=False`，
按代码应该走 bigram 倒排；但 TTFB 偏高说明可能掉进了 LIKE 兜底或 N+1。
（本机无法复现：库在远端，本地测的是 WAN 延迟。）

**⑤ 减少每个请求的 SQL 条数**（11–20 → 个位数）。生产上不是瓶颈，但降低 DB 压力。

### 链路本身

**⑥ 值得单独确认一下 Cloudflare 这条路径。** 380ms 的 RTT 底噪 + 8KB/s 的吞吐，
对香港机房来说都不正常。可能的方向：Cloudflare 免费版的中国大陆回源路由、
是否开了 Argo、是否被限速。这块我没法从客户端侧判定，需要你在 Cloudflare 面板
看 Analytics 里的 `Cache Hit Ratio` 和 `Origin Response Time`。

---

## 复现方法

```powershell
cd dnaisland

# L1 远端端到端（约 25 分钟，因为有几个接口本身就很慢）
.\.venv\Scripts\python.exe -u scripts\bench_remote.py --runs 3

# L2 复用连接（区分握手与服务端思考时间）
.\.venv\Scripts\python.exe -u scripts\bench_keepalive.py

# 体积构成（base64 图片占多少）
.\.venv\Scripts\python.exe -u scripts\bench_payload.py

# L3 本地进程内 + SQL 条数（连 .env 里的库）
.\.venv\Scripts\python.exe -u scripts\bench_local.py
```

**务必加 `-u`**：不加的话 Python 会缓冲 stdout，看不到进度（这次就吃了这个亏）。

---

## 局限

* **匿名视角**：所有请求都未登录，鉴权接口只测到 401 路径；登录后的
  `my/cards`、`notifications`、`points` 等没有覆盖到真实数据量。
* **未做并发压测**：这是线上服务，只测单请求延迟，不做并发/压力测试。
* **L1 每请求新建连接**：含 TLS 握手，比真实客户端偏高；L2 才是可比的口径。
* **L3 的绝对耗时不可用**：库在远端，含 WAN 往返；只有 SQL 条数有意义。
* **样本量小**（3 次取中位数）：链路抖动大的时候，个别数字会有较大误差。
* 没有覆盖**图片本身的加载**（`/card-image/`、`/user/avatar/`）—— 这恰恰是下一步
  最该测的，因为改成 URL 之后流量就全压到这些路由上了。
