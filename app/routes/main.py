import base64
import math
import random
from datetime import datetime, timedelta
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import case, desc, func, literal_column, or_, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import joinedload

from ..caching import TimedCache
from ..extensions import db
from ..models import (
    Article,
    Card,
    CardCopyStat,
    CardFavorite,
    CardImage,
    CardLike,
    CardTag,
    Comment,
    Punishment,
    Sponsor,
    User,
    UserFollow,
)
from ..models.teahouse import TeaPost
from ..paging import IdListPagination
from ..routes.card_lists import explore_cards, recommend_items, search_cards
from ..services.card_service import enrich_cards, popular_tags
from ..services.image_service import send_webp

main_bp = Blueprint("main", __name__)

# 探索页「热门」排序权重（数字即业务优先级：1 = 最重要，6 = 最次要）。
#   评论(1) > 0~5日新发布(2) > 收藏(3) > 点赞(4) > 带图(5) > 浏览(6)
HOT_W_COMMENT = 6.0  # 优先级 1：评论代表讨论度，权重最高
HOT_W_FAVORITE = 3.0  # 优先级 3：每个收藏都加一次权
HOT_W_LIKE = 2.0  # 优先级 4：每个点赞都加一次权
HOT_W_VIEW = 1.0  # 优先级 6：浏览最弱信号
# 复制数信号（方案 B）：近 30 天复制量经对数压缩后作为独立加权项。
# 复制 = 强正向意图（用户实际带走使用），含金量高于浏览/点赞、介于收藏与评论之间。
HOT_W_COPY = 4.0  # 复制权重（介于收藏 3 与评论 6 之间）
COPY_WINDOW_DAYS = 30  # 只统计近 30 天复制，保证信号「当下化」并与 HN 时间衰减协同
# 浏览量对数压缩：用 log(1+views) 替代线性 views，削弱「被动累加」的浏览主导
# （几百次浏览不会再以线性方式碾压互动信号），同时保留「看得多 = 略热门」的弱信号。
HOT_VIEW_LOG_BASE = 10.0
# 带图改为「倍数放大」而非固定加分：带图为 IMG_MULT，无图为 1.0。
# 这样带图卡的整段互动得分按比例放大，不再被浏览量级淹没（固定 +5 在几百浏览面前无意义）。
HOT_IMG_MULT = 1.3  # 带图卡整体得分 ×1.3
# 冷启动基线：加在 engagement 括号内的正数（单位 = 互动分）。
# 年龄加成是**乘数**，engagement 为 0 时 0 × 1.4 仍为 0：新发布且零互动的卡会恒为
# 0 分——在探索页排在所有「被点开过至少一次」的卡之后，在首页加权抽样中权重为 0
# （数学上永远抽不到），形成「没曝光 → 没互动 → 更没曝光」的死锁。
# 取 3.0（= HOT_W_FAVORITE）：等价于「新卡默认算作已获得一次收藏」，随后与其它信号
# 一起被年龄权重衰减，因此只对新卡有效、老卡不会因此受益。
# 必须加在乘法括号**内**：这样 boost_factor（减少推流）与带图系数对基线同样生效，
# 不会变成绕过降权的后门（见 tests/test_recommendation_score.py）。
# 回滚：置 0.0 即精确恢复旧行为（零互动新卡重新恒为 0 分）。
HOT_COLD_START_BASE = 3.0
# 探索页「热门」排序的年龄权重曲线（三段式，是整个 engagement 的乘数）：
#   0~5 日  ：上升权重 —— 新卡整体得分被放大（最新 ×1.4），到 5 日回落到 ×1.0；
#             注意这是乘数：它只放大已有互动，零互动卡靠 HOT_COLD_START_BASE 保底。
#   6~8 日  ：平稳权重 —— 固定 ×1.0，不增不减；
#   9 日及以后：下降权重 —— 按半衰期平滑衰减，老卡随时间下沉、前排轮换。
HOT_RISE_END_DAYS = 5  # 上升段终点（含）
HOT_RISE_BOOST = 0.4  # 上升段额外权重：最新卡 ×(1+0.4)=1.4，线性回落到 5 日时 ×1.0
HOT_STABLE_END_DAYS = 8  # 平稳段终点（含）；6~8 日权重固定 ×1.0
HOT_DECAY_START_DAYS = 8  # 下降段起点（>8 日即衰减）
HOT_DECAY_HALF_DAYS = 7  # 下降段半衰期（天）：每过 7 天权重减半，值越小衰减越快

# 作者影响力项：以「全体作者粉丝数的 P90」为归一化基准，而不是写死绝对粉丝数。
# 平台整体涨粉时基准同步抬高（见 _follower_reference），永远只有头部约 10% 打满，
# 因此不会出现「平台火了、人人都有 100 粉」导致该阈值失效的问题。
HOT_W_FOLLOWER = 4.0  # 作者影响力权重（归一化后 0~1，达到基准即打满）
HOT_FOLLOWER_REF_PCT = 0.90  # 归一化基准取全体作者粉丝数的第 90 百分位
FOLLOWER_REF_TTL = 3600  # 基准重算间隔（秒）

# 同作者窗口衰减：同一个推荐窗口内，该作者已入选 k 张时，本张抽样权重再乘 decay^k。
# 只作用于「一次返回一批」的首页推荐与刷一刷（探索页是分页确定性排序，见 card_lists）。
AUTHOR_WINDOW_DECAY = 0.5


def _has_image_subquery():
    """返回「至少有一张图片」的卡片 id 子查询，用于推荐排序时给有图卡加权。"""
    return (
        db.session.query(CardImage.card_id).group_by(CardImage.card_id).subquery("card_has_image")
    )


@main_bp.route("/article-cover/<int:article_id>")
def article_cover(article_id):
    """文章封面：base64 类型走此端点转 WEBP；已为 WebP 的 data URL 直接发送；URL 类型由模板直接引用外链。"""
    a = db.session.get(Article, article_id)
    if not a or not a.cover:
        abort(404)
    if a.cover.startswith("data:"):
        # 上传时已转 WebP 的，直接解码发送，避免二次压缩损失
        if a.cover.startswith("data:image/webp"):
            try:
                b64 = a.cover.split(",", 1)[1]
                return send_file(
                    BytesIO(base64.b64decode(b64)),
                    mimetype="image/webp",
                    max_age=86400,
                )
            except Exception:
                pass
        return send_webp(a.cover, max_edge=1024, quality=82)
    abort(404)


# 首页「为你推荐」候选池（card_id -> 热度分）缓存。
# 热度分需要对整表做 4 次聚合 + 加权计算，较昂贵，且 60s 内变化极小；
# 但分数依赖 viewer 可见性，故按 viewer 身份分 key，并带 TTL 与 LRU 上限。
# 随机抽样（含「换一换」的排除与纯随机名额）保留在缓存外执行，维持推荐动态性。
_FEATURED_SCORE_CACHE = TimedCache(ttl=60, maxsize=100)  # viewer -> {card_id: score}


def _featured_score_map() -> dict:
    """返回首页推荐候选池（card_id -> (热度分, 作者id)），带 60s TTL + LRU 上限缓存。

    一并带上作者 id，供抽样阶段做「同作者窗口衰减」（见 _sample_weights）。
    """
    vid = current_user.id if current_user.is_authenticated else "anon"
    hit = _FEATURED_SCORE_CACHE.get(vid)
    if hit is not None:
        return hit
    q, score_expr = _apply_hot_score(Card.visible_to(current_user))
    rows = q.with_entities(Card.id, score_expr, Card.author_id).all()
    score_map: dict = {}
    for cid, s, author_id in rows:
        try:
            score = float(s) if s is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        score_map[cid] = (score, author_id)
    _FEATURED_SCORE_CACHE.set(vid, score_map)
    return score_map


def featured_cards(limit=12, exclude_ids=None):
    """首页「为你推荐」统一入口：网页版 index 与 API cards_featured 共用。

    与探索页同款加权得分做加权随机，但保留发现感：
    - 每张卡用探索同款得分（互动加权 × 三段年龄权重）作为抽样权重，热门卡被抽中概率更高；
      得分里已含「带图 ×1.3」与新鲜度加成，故天然「有图优先」。
    - 预留 1~2 个名额做「纯随机」均匀抽样，注入偶遇感，避免前排总被热门占据。
    - exclude_ids 为已展示过的 id（换一换时传入），从候选池剔除，保证不重复。
    - 返回带 `cover` 标记的 Card 列表；真实图片由前端按 `/card-image/...` 按需加载。
    """
    exclude = set()
    if exclude_ids:
        # Card.id 为 UUID 字符串，按字符串去重（此前按 int 处理导致 exclude 不生效）。
        exclude = {str(x).strip() for x in exclude_ids if str(x).strip()}

    score_map = _featured_score_map()
    if not score_map:
        return []

    pool = [cid for cid in score_map if cid not in exclude] or list(score_map.keys())

    # 纯随机保底名额：1~2 个；其余按得分加权无放回抽样（含同作者窗口衰减）
    pure = min(random.randint(1, 2), max(0, limit - 1))
    weighted_n = max(0, limit - pure)

    chosen = _weighted_sample_with_author_decay(pool, score_map, weighted_n)
    chosen_set = set(chosen)
    avail = [cid for cid in pool if cid not in chosen_set]

    pure_picks = random.sample(avail, min(pure, len(avail))) if pure and avail else []
    result_ids = chosen + pure_picks
    random.shuffle(result_ids)

    # 预载作者（1 条 LEFT JOIN），避免序列化时逐卡再查作者造成 N+1。
    card_map = {
        c.id: c
        for c in Card.query.filter(Card.id.in_(result_ids)).options(joinedload(Card.author)).all()
    }
    cards = [card_map[cid] for cid in result_ids if cid in card_map]

    # 批量加载封面（1 条 IN 查询）：网页用 .cover 布尔；App 用 covers 槽位路径。
    covers_by_card: dict[int, dict[str, str]] = {}
    for img in CardImage.query.filter(CardImage.card_id.in_(result_ids)).all():
        covers_by_card.setdefault(img.card_id, {})[img.slot] = (
            f"/card-image/{img.card_id}/{img.slot}"
        )
    for c in cards:
        covers = covers_by_card.get(c.id, {})
        c.covers = covers
        c.cover = "square" in covers
    return cards


def swipe_cards(limit=12, exclude_ids=None):
    """「刷一刷」专用入口：只返回**有封面**（任意槽位图）的角色卡。

    与 [featured_cards] 同用热度加权随机（带图优先），但候选池额外收窄为
    「有图」的卡，保证刷一刷每一页都有封面可展示，客户端无需二次过滤。

    区别点：
    - 候选池先按「有无图片」收窄（一次 IN 查询拿到有图卡集合）再抽样；
    - exclude 按字符串（UUID）去重，兼容 `cards.id` 为 UUID 的情况。
    """
    exclude = set()
    if exclude_ids:
        exclude = {str(x).strip() for x in exclude_ids if str(x).strip()}

    score_map = _featured_score_map()
    if not score_map:
        return []

    # 有图卡集合：存在任一 CardImage（square/landscape/portrait）即算有图。
    image_ids = set()
    q = db.session.query(CardImage.card_id).filter(CardImage.card_id.in_(list(score_map.keys())))
    for (cid,) in q.all():
        image_ids.add(str(cid))

    pool = [cid for cid in image_ids if cid not in exclude]
    if not pool:
        return []

    # 纯随机保底名额 1~2；其余按得分加权无放回抽样（含同作者窗口衰减）。
    pure = min(random.randint(1, 2), max(0, limit - 1))
    weighted_n = max(0, limit - pure)

    chosen = _weighted_sample_with_author_decay(pool, score_map, weighted_n)
    chosen_set = set(chosen)
    avail = [cid for cid in pool if cid not in chosen_set]

    pure_picks = random.sample(avail, min(pure, len(avail))) if pure and avail else []
    result_ids = chosen + pure_picks
    random.shuffle(result_ids)

    # 预载作者 + 批量封面，避免 N+1。
    card_map = {
        c.id: c
        for c in Card.query.filter(Card.id.in_(result_ids)).options(joinedload(Card.author)).all()
    }
    cards = [card_map[cid] for cid in result_ids if cid in card_map]

    covers_by_card: dict[str, dict[str, str]] = {}
    for img in CardImage.query.filter(CardImage.card_id.in_(result_ids)).all():
        covers_by_card.setdefault(img.card_id, {})[img.slot] = (
            f"/card-image/{img.card_id}/{img.slot}"
        )
    for c in cards:
        covers = covers_by_card.get(c.id, {})
        c.covers = covers
        c.cover = "square" in covers
    return cards


@main_bp.route("/")
def index():
    # 首页「为你推荐」：与探索同款加权随机 12 张（保留 1~2 纯随机名额）；
    # 点击「换一换」时 ?fragment=1 仅返回卡片网格片段，并带 ?exclude=已展示id 避免重复。
    exclude = request.args.get("exclude")
    exclude_ids = exclude.split(",") if exclude else None
    cards = featured_cards(12, exclude_ids)
    if request.args.get("fragment"):
        return render_template("partials/card_grid_fragment.html", cards=cards)
    return render_template(
        "index.html",
        cards=cards,
    )


@main_bp.route("/recommend")
def recommend():
    """站长板块（站长推荐）。与 App 共用 recommend_items 一个函数。"""
    items = recommend_items()
    return render_template("recommend/index.html", items=items)


@main_bp.route("/sponsor")
def sponsor():
    """赞助页面：展示赞助配置（标题/富文本说明/按钮链接）与随机打乱的赞助者列表。"""
    from ..services.site_service import get_site_config

    try:
        cfg = get_site_config()
        rows = Sponsor.query.order_by(func.rand()).limit(30).all()
    except Exception:
        # 表尚未建立（如迁移未执行）时优雅降级为空
        cfg = None
        rows = []

    enabled = bool(cfg and cfg.sponsor_enabled)
    items = []
    if enabled and rows:
        uid_map = {u.id: u for u in User.query.filter(User.id.in_([s.user_id for s in rows])).all()}
        for s in rows:
            u = uid_map.get(s.user_id)
            if not u:
                continue
            items.append(
                {
                    "uid": s.user_id,
                    "display_name": s.display_name,
                    "amount": s.amount or "",
                    "user": u,
                }
            )

    return render_template(
        "sponsor/index.html",
        enabled=enabled,
        title=(cfg.sponsor_title if cfg else "") or "",
        content=(cfg.sponsor_content if cfg else "") or "",
        url=(cfg.sponsor_url if cfg else "") or "",
        items=items,
    )


def _banned_author_ids():
    """处于 profile_banned 处罚的作者，其主页不可被搜索到。"""
    return (
        db.session.query(Punishment.user_id)
        .filter(Punishment.status == "active", Punishment.type == "profile_banned")
        .distinct()
    )


def _copies_agg_subquery(days=COPY_WINDOW_DAYS):
    """近 N 天每张卡的复制次数（card_id -> count），用于把复制数并入热度排序。

    只统计近 days 天内的复制，保证信号「当下化」并与 HN 时间衰减协同；
    窗口边界用 Python UTC 与库内 copied_at（db.func.now()）近似对齐（项目现状约定）。
    """
    since = datetime.utcnow() - timedelta(days=days)
    return (
        db.session.query(
            CardCopyStat.card_id,
            func.count(CardCopyStat.card_id).label("cp"),
        )
        .filter(CardCopyStat.copied_at >= since)
        .group_by(CardCopyStat.card_id)
        .subquery("card_copies_agg")
    )


def _author_followers_subquery():
    """每个作者的粉丝数（author_id -> count），用于把作者影响力并入热度分。"""
    return (
        db.session.query(
            UserFollow.following_id.label("author_id"),
            func.count(UserFollow.follower_id).label("fcnt"),
        )
        .group_by(UserFollow.following_id)
        .subquery("author_followers_agg")
    )


def _likes_agg_subquery():
    """一次聚合出每张卡的赞数（card_id -> count），避免排序时逐行关联子查询。"""
    return (
        db.session.query(
            CardLike.card_id,
            func.count(CardLike.card_id).label("lc"),
        )
        .group_by(CardLike.card_id)
        .subquery("card_likes_agg")
    )


def _favorites_agg_subquery():
    """一次聚合出每张卡的收藏数（card_id -> count），避免排序时逐行关联子查询。"""
    return (
        db.session.query(
            CardFavorite.card_id,
            func.count(CardFavorite.card_id).label("fc"),
        )
        .group_by(CardFavorite.card_id)
        .subquery("card_favs_agg")
    )


def _comments_agg_subquery():
    """一次聚合出每张卡（未隐藏）评论数（card_id -> count），避免排序时逐行关联子查询。"""
    return (
        db.session.query(
            Comment.card_id,
            func.count(Comment.card_id).label("cc"),
        )
        .filter(Comment.is_hidden.is_(False))
        .group_by(Comment.card_id)
        .subquery("card_comments_agg")
    )


def _log_base(expr, base):
    """以 base 为底的 SQL 对数，跨 SQLite / MySQL 可移植。

    不能直接用 func.log：SQLite 的 log() 是常用对数（底 10），而 MySQL 的 LOG() 是
    自然对数——同一个表达式在两端会得到差 2.3026 倍的口径（历史遗留不一致）。
    这里统一用两端口径相同的 LOG10 换底，保证开发/测试与生产行为一致。
    """
    return func.log10(expr) / math.log10(base)


# 作者影响力归一化基准（全体作者粉丝数的 P90）缓存：变化很慢，1 小时重算一次足够。
_FOLLOWER_REF_CACHE = TimedCache(ttl=FOLLOWER_REF_TTL, maxsize=2)


def _follower_reference() -> float:
    """全体作者粉丝数的 P90，作为作者影响力的归一化基准（带 TTL 缓存）。

    用「分位」而不是固定阈值：平台整体涨粉时基准同步抬高，永远只有头部约 10% 打满。
    作者数很少时可能返回 0/1，调用方会跳过该项，避免放大小样本噪声。
    """
    hit = _FOLLOWER_REF_CACHE.get("p90")
    if hit is not None:
        return hit
    counts = sorted(
        int(c or 0)
        for (c,) in db.session.query(func.count(UserFollow.follower_id))
        .group_by(UserFollow.following_id)
        .all()
    )
    ref = 0.0
    if counts:
        # 最近秩法：ceil(p * n) - 1，落在 [0, n-1]
        idx = min(
            len(counts) - 1,
            max(0, math.ceil(HOT_FOLLOWER_REF_PCT * len(counts)) - 1),
        )
        ref = float(counts[idx])
    _FOLLOWER_REF_CACHE.set("p90", ref)
    return ref


def _sample_weights(pool, score_map, author_seen):
    """计算加权抽样的权重：热度分 × 同作者窗口衰减。

    [score_map] 为 {card_id: (热度分, 作者id)}；[author_seen] 为本次窗口内
    {作者id: 已入选张数}。同一作者第 k+1 张的权重乘 AUTHOR_WINDOW_DECAY^k。
    """
    weights = []
    for cid in pool:
        score, author_id = score_map[cid]
        decay = AUTHOR_WINDOW_DECAY ** author_seen.get(author_id, 0)
        weights.append(max(score, 0.0) * decay)
    return weights


def _weighted_sample_with_author_decay(pool, score_map, n):
    """按热度分做无放回加权抽样，并对同一作者施加窗口衰减。

    返回抽中的 card_id 列表（长度 ≤ n）。权重全为 0 时提前返回（调用方兜底）。
    """
    chosen: list = []
    avail = list(pool)
    author_seen: dict = {}
    while len(chosen) < n and avail:
        weights = _sample_weights(avail, score_map, author_seen)
        if sum(weights) <= 0:
            break
        pick = random.choices(avail, weights=weights, k=1)[0]
        chosen.append(pick)
        avail.remove(pick)
        author_id = score_map[pick][1]
        author_seen[author_id] = author_seen.get(author_id, 0) + 1
    return chosen


def _apply_hot_score(q):
    """对查询 q 做探索热度所需的 outerjoin，并返回 (q, score_expr)。

    score_expr 与探索页排序同款：(互动加权 + 冷启动基线) × 带图系数 × 三段年龄权重 × boost_factor。
    互动加权为 评论6/收藏3/复制4/点赞2/浏览1，并叠加**作者影响力项**（粉丝数相对 P90 归一化，
    权重 HOT_W_FOLLOWER，封顶 1.0）；冷启动基线 HOT_COLD_START_BASE 保证零互动新卡不为 0 分。
    供 `_order_by_hot` 排序与首页加权随机复用，确保两处推荐口径一致。
    复制数取近 30 天并经对数压缩（HOT_W_COPY / COPY_WINDOW_DAYS）。
    """
    la = _likes_agg_subquery()
    fa = _favorites_agg_subquery()
    ca = _comments_agg_subquery()
    cpa = _copies_agg_subquery()
    ia = _has_image_subquery()
    ufa = _author_followers_subquery()
    q = q.outerjoin(la, la.c.card_id == Card.id)
    q = q.outerjoin(fa, fa.c.card_id == Card.id)
    q = q.outerjoin(ca, ca.c.card_id == Card.id)
    q = q.outerjoin(cpa, cpa.c.card_id == Card.id)
    q = q.outerjoin(ia, ia.c.card_id == Card.id)
    q = q.outerjoin(ufa, ufa.c.author_id == Card.author_id)

    if db.engine.name == "sqlite":
        age_hours = (func.julianday("now") - func.julianday(Card.created_at)) * 24.0
    else:
        age_hours = func.timestampdiff(literal_column("HOUR"), Card.created_at, func.now())
    age_days = age_hours / 24.0

    # 三段式年龄权重：
    #   0~5 日  → 上升：1 + boost * (5 - age)/5，最新 ×1.4 线性回落到 ×1.0
    #   6~8 日  → 平稳：×1.0
    #   >8 日   → 下降：0.5^((age-8)/半衰期)，平滑衰减
    age_factor = case(
        (
            age_days <= HOT_RISE_END_DAYS,
            1.0 + HOT_RISE_BOOST * (HOT_RISE_END_DAYS - age_days) / HOT_RISE_END_DAYS,
        ),
        (age_days <= HOT_STABLE_END_DAYS, 1.0),
        else_=func.pow(
            literal_column("0.5"),
            (age_days - HOT_DECAY_START_DAYS) / HOT_DECAY_HALF_DAYS,
        ),
    )

    # 浏览量对数压缩：log(1+views)/log(base)，几百次浏览也只贡献个位数量级，
    # 不再以线性方式碾压互动信号。
    view_term = _log_base(func.coalesce(Card.view_count, 0) + 1.0, HOT_VIEW_LOG_BASE)
    # 复制数对数压缩：与浏览量同款，log(1 + 近30天复制数)，避免单卡复制被线性放大碾压其他信号。
    copy_term = _log_base(func.coalesce(cpa.c.cp, 0) + 1.0, HOT_VIEW_LOG_BASE)

    # 作者影响力项：以 P90 为基准做对数归一化并封顶 1.0（达到基准即打满，头部不再额外受益）。
    # 基准由 _follower_reference() 动态给出，随平台整体涨粉自动抬高，不依赖绝对粉丝数。
    follower_ref = _follower_reference()
    if follower_ref > 1.0:
        follower_log = _log_base(func.coalesce(ufa.c.fcnt, 0) + 1.0, HOT_VIEW_LOG_BASE)
        ref_log = math.log(follower_ref + 1.0, HOT_VIEW_LOG_BASE)
        follower_term = case((follower_log >= ref_log, 1.0), else_=follower_log / ref_log)
    else:
        # 全站作者粉丝数还很少（基准 ≤ 1）：本项不参与，避免放大小样本噪声。
        follower_term = literal_column("0.0")

    engagement = (
        func.coalesce(ca.c.cc, 0) * HOT_W_COMMENT
        + func.coalesce(fa.c.fc, 0) * HOT_W_FAVORITE
        + func.coalesce(la.c.lc, 0) * HOT_W_LIKE
        + copy_term * HOT_W_COPY
        + view_term * HOT_W_VIEW
        + follower_term * HOT_W_FOLLOWER
        # 冷启动基线：保证零互动新卡得分 > 0（理由见 HOT_COLD_START_BASE）。
        + HOT_COLD_START_BASE
    )
    # 带图倍数放大：带图卡整体互动得分 ×HOT_IMG_MULT，无图 ×1.0，
    # 让带图的优势按整卡规模生效，而非被浏览量淹没的固定加分。
    img_mult = case((ia.c.card_id.isnot(None), HOT_IMG_MULT), else_=1.0)
    # 隐匿标签降权（如「减少推流」×0.2）：boost_factor 由 card_hidden_tags 派生，
    # 是 SQL 可见列，因此首页推荐/刷一刷/探索热门/搜索相关度口径统一。
    score = engagement * img_mult * age_factor * func.coalesce(Card.boost_factor, 1.0)
    return q, score


def _order_by_hot(q):
    """探索页「热门」排序：按与首页同款的加权得分降序。"""
    q, score = _apply_hot_score(q)
    return q.order_by(score.desc(), Card.created_at.desc())


def _order_by_likes(q):
    """按赞数排序：复用赞数聚合子查询，避免逐行关联子查询。"""
    agg = _likes_agg_subquery()
    q = q.outerjoin(agg, agg.c.card_id == Card.id)
    return q.order_by(func.coalesce(agg.c.lc, 0).desc(), Card.created_at.desc())


# 「热门」排序结果缓存：首页/探索默认按热度排序，每次请求都要对整表做
# 点赞/收藏/评论聚合 + Hacker News 时间衰减排序，且 paginate 还要额外 COUNT 一次。
# 这些顺序在 60s 内变化极小，故按 (路由 + 过滤条件) 缓存有序 id 列表，分页时直接切片，
# 免去每请求重算。缓存基于「全局可见」集合；登录用户屏蔽的作者可能滞后至多 TTL 出现
# 于其热门流（与 popular_tags 的取舍一致），对热门推荐流可接受。
_HOT_CARD_CACHE = TimedCache(ttl=60, maxsize=50)  # signature -> [card_id,...]


def _hot_card_order(signature, build_query, order_fn):
    """返回按热度降序排列的卡片 id 列表，带 60s TTL 缓存。"""
    ids = _HOT_CARD_CACHE.get(signature)
    if ids is not None:
        return ids
    q = order_fn(build_query())
    ids = [cid for (cid,) in q.with_entities(Card.id).all()]
    _HOT_CARD_CACHE.set(signature, ids)
    return ids


def _paginate_hot_cards(build_query, signature, order_fn, page, per_page):
    """用缓存的有序 id 列表做分页：切片取本页 id，再按 id 批量取卡并就地排序。"""
    ids = _hot_card_order(signature, build_query, order_fn)
    total = len(ids)
    if page < 1:
        page = 1
    start = (page - 1) * per_page
    slice_ids = ids[start : start + per_page]
    cards = []
    if slice_ids:
        fetched = {c.id: c for c in Card.query.filter(Card.id.in_(slice_ids)).all()}
        cards = [fetched[cid] for cid in slice_ids if cid in fetched]
    return IdListPagination(cards, page, per_page, total)


def _fulltext_enabled() -> bool:
    """搜索是否启用 MySQL 全文索引（FULLTEXT / ngram）加速。

    仅 MySQL 且配置开启时返回 True；SQLite 等不支持 FULLTEXT 的引擎一律回退
    到 LIKE，保证开发 / 测试环境行为与原有一致（可回归、可移植）。
    """
    return db.engine.name == "mysql" and current_app.config.get("FULLTEXT_SEARCH", True)


def _ft_match(cols, q):
    """构造 MySQL 全文检索 MATCH ... AGAINST 表达式。

    用 text() 手写 SQL 而非 func.match(..., against=q)：后者依赖
    SQLAlchemy 2.0.23+ 新增的 `against` 关键字，旧版运行环境会抛
    `TypeError: Function.__init__() got an unexpected keyword argument 'against'`。
    这里改用带绑定参数的原生 text 表达式：TextClause 在 SQLAlchemy 1.x / 2.x
    都支持 bindparams()，且可放进 or_ 作为布尔条件（不能用不可变的
    literal_column().bindparams()，2.0 中该写法已失效）。
    """
    return text(f"MATCH ({cols}) AGAINST (:q IN BOOLEAN MODE)").bindparams(q=q)


def _fulltext_fallback(ft_query, like_query):
    """优先执行 MySQL 全文检索；若 FULLTEXT 索引缺失/未迁移导致 MATCH...AGAINST
    报错（OperationalError），回退到 LIKE 查询。

    否则一旦生产 MySQL 未跑全文索引迁移，所有搜索（含联想）都会因 500 空结果。
    LIMIT 0 仅用于触发一次执行以探测索引是否可用，不扫描实际数据。
    """
    if not _fulltext_enabled():
        return like_query
    try:
        ft_query.limit(0).all()
        return ft_query
    except OperationalError:
        return like_query


def _card_search_query(q, sort, tag=None, viewer=None):
    """构造角色卡检索查询（已包含信息层可见性过滤与相关度排序）。

    viewer 为可选的可见性视角（App 传 JWT 用户，Web 传 current_user），
    缺省回退到 current_user，保持向后兼容。

    MySQL 且开启 FULLTEXT 时，name/intro/persona 的检索走全文索引（MATCH
    AGAINST），tag 仍用 LIKE（标签表未建全文索引）；否则回退到原有的
    全表 LIKE，语义不变。索引缺失时自动回退 LIKE，避免搜索 500。
    """
    like = f"%{q}%"
    base = Card.visible_to(viewer if viewer is not None else current_user).outerjoin(
        CardTag, CardTag.card_id == Card.id
    )
    use_ft = _fulltext_enabled() and bool(q.strip())
    if use_ft:
        ft = _ft_match("cards.name, cards.intro, cards.persona", q)
        filters = [or_(ft, CardTag.tag.like(like))]
    else:
        filters = [
            or_(
                Card.name.like(like),
                Card.intro.like(like),
                Card.persona.like(like),
                CardTag.tag.like(like),
            )
        ]
    if tag:
        filters.append(CardTag.tag == tag)
    base = base.filter(*filters).distinct()

    def _apply_order(query, use_fulltext=None):
        use_fulltext = use_ft if use_fulltext is None else use_fulltext
        if sort == "hot":
            return _order_by_hot(query)
        if sort == "new":
            return query.order_by(Card.created_at.desc())
        # relevance：全文检索直接用 MATCH 相关度排序，否则用命中列加权 CASE。
        if use_fulltext:
            return query.order_by(
                desc(ft),
                Card.view_count.desc(),
                Card.created_at.desc(),
            )
        score = case(
            (Card.name.like(like), 3),
            (CardTag.tag.like(like), 2),
            (or_(Card.intro.like(like), Card.persona.like(like)), 1),
            else_=0,
        )
        return query.order_by(score.desc(), Card.view_count.desc(), Card.created_at.desc())

    # 同时构造 LIKE 版本作为兜底：FULLTEXT 索引缺失时由 _fulltext_fallback 切换。
    if use_ft:
        ft_base = base.filter(or_(ft, CardTag.tag.like(like)))
        if tag:
            ft_base = ft_base.filter(CardTag.tag == tag)
        ft_base = ft_base.distinct()
        ft_query = _apply_order(ft_base, use_fulltext=True)
        like_filters = [
            or_(
                Card.name.like(like),
                Card.intro.like(like),
                Card.persona.like(like),
                CardTag.tag.like(like),
            )
        ]
        if tag:
            like_filters.append(CardTag.tag == tag)
        like_base = Card.visible_to(viewer if viewer is not None else current_user).outerjoin(
            CardTag, CardTag.card_id == Card.id
        )
        like_query = _apply_order(like_base.filter(*like_filters).distinct(), use_fulltext=False)
        return _fulltext_fallback(ft_query, like_query)
    return _apply_order(base)


def _user_search_query(q, sort):
    """构造作者检索查询。"""
    like = f"%{q}%"
    banned = _banned_author_ids()
    base = User.query.filter(
        User.status == "active",
        User.id.notin_(banned),
        or_(
            User.nickname.like(like),
            User.username.like(like),
            User.bio.like(like),
        ),
    )
    if sort == "new":
        order = [User.created_at.desc()]
    else:  # relevance：昵称命中优先
        score = case(
            (User.nickname.like(like), 2),
            (or_(User.username.like(like), User.bio.like(like)), 1),
            else_=0,
        )
        order = [score.desc(), User.created_at.desc()]
    return base.order_by(*order)


def _post_search_query(q):
    base = TeaPost.query.filter(
        TeaPost.parent_id.is_(None),
        TeaPost.is_hidden.is_(False),
        TeaPost.is_deleted.is_(False),
    )
    if _fulltext_enabled() and bool(q.strip()):
        # 先走全文索引；索引缺失时由 _fulltext_fallback 回退到 LIKE。
        ft_query = base.filter(_ft_match("teahouse_posts.content", q)).order_by(
            TeaPost.created_at.desc()
        )
        like_query = base.filter(TeaPost.content.ilike(f"%{q}%")).order_by(
            TeaPost.created_at.desc()
        )
        return _fulltext_fallback(ft_query, like_query)
    return base.filter(TeaPost.content.ilike(f"%{q}%")).order_by(TeaPost.created_at.desc())


@main_bp.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    search_type = request.args.get("type", "all")
    sort = request.args.get("sort", "relevance")
    tag = (request.args.get("tag") or "").strip() or None
    page = request.args.get("page", 1, type=int)

    valid_types = ("all", "card", "user", "post")
    if search_type not in valid_types:
        search_type = "all"
    valid_sorts = ("relevance", "hot", "new")
    if sort not in valid_sorts:
        sort = "relevance"

    args = {"q": q, "type": search_type, "sort": sort}
    if tag:
        args["tag"] = tag

    cards = []
    cards_pagination = None
    users = []
    users_pagination = None
    posts = []
    posts_pagination = None
    cards_count = 0
    users_count = 0
    posts_count = 0

    if q:
        if search_type == "all":
            # 汇总视图：需三类总数 + 各取少量样例，分别 count 一次（此处无分页，故无重复计数）
            cards_count = _card_search_query(q, sort, tag).count()
            users_count = _user_search_query(q, sort).count()
            posts_count = _post_search_query(q).count()
            cards = enrich_cards(_card_search_query(q, sort, tag).limit(4).all())
            users = _user_search_query(q, sort).limit(3).all()
            posts = _post_search_query(q).limit(4).all()
        elif search_type == "card":
            # 与 App 共用 search_cards 一个函数。
            cards_pagination, cards = search_cards(
                current_user, q, sort=sort, tag=tag, page=page, per_page=12
            )
            cards_count = cards_pagination.total  # 复用分页的 total，避免重复 COUNT
        elif search_type == "user":
            users_pagination = _user_search_query(q, sort).paginate(
                page=page, per_page=20, error_out=False
            )
            users = users_pagination.items
            users_count = users_pagination.total
        elif search_type == "post":
            posts_pagination = _post_search_query(q).paginate(
                page=page, per_page=15, error_out=False
            )
            posts = posts_pagination.items
            posts_count = posts_pagination.total

    # 当前登录用户已关注的用户 id 集合（供模板渲染关注按钮的初始状态）。
    following_ids: set = set()
    if current_user.is_authenticated and users:
        ids = [u.id for u in users]
        rows = UserFollow.query.filter(
            UserFollow.follower_id == current_user.id,
            UserFollow.following_id.in_(ids),
        ).all()
        following_ids = {r.following_id for r in rows}

    return render_template(
        "search.html",
        q=q,
        search_query=q,
        search_type=search_type,
        sort=sort,
        tag=tag,
        cards=cards,
        cards_pagination=cards_pagination,
        cards_count=cards_count,
        users=users,
        users_pagination=users_pagination,
        users_count=users_count,
        posts=posts,
        posts_pagination=posts_pagination,
        posts_count=posts_count,
        following_ids=following_ids,
        args=args,
    )


@main_bp.route("/explore")
def explore():
    page = request.args.get("page", 1, type=int)
    gender = (request.args.get("gender") or "").strip()
    tag = (request.args.get("tag") or "").strip() or None
    sort = request.args.get("sort", "hot")
    if sort not in ("hot", "new", "likes"):
        sort = "hot"

    # 与 App 共用 explore_cards 一个函数（含热门缓存 + 批量装配）。
    pagination, cards = explore_cards(
        current_user, page=page, gender=gender, tag=tag, sort=sort, per_page=24
    )

    genders = [
        g[0]
        for g in (
            Card.visible_to(current_user)
            .with_entities(Card.gender)
            .filter(Card.gender.is_not(None), Card.gender != "")
            .distinct()
            .all()
        )
    ]
    tags = popular_tags(current_user, limit=30)

    # 分页链接需保留当前筛选条件
    args = {"sort": sort}
    if gender:
        args["gender"] = gender
    if tag:
        args["tag"] = tag

    return render_template(
        "explore.html",
        cards=cards,
        pagination=pagination,
        genders=genders,
        tags=tags,
        gender=gender,
        tag=tag,
        sort=sort,
        args=args,
    )


@main_bp.route("/search/suggest")
def search_suggest():
    """顶栏实时下拉建议：返回匹配度最高的若干角色卡与作者。"""
    q = (request.args.get("q") or "").strip()
    if len(q) < 1:
        return jsonify({"cards": [], "users": []})

    cards = _card_search_query(q, "relevance").limit(6).all()
    card_hits = [
        {
            "id": c.id,
            "name": c.name,
            "gender": c.gender,
            "url": url_for("user.card_detail", card_id=c.id),
        }
        for c in cards
    ]

    users = _user_search_query(q, "relevance").limit(5).all()
    user_hits = [
        {
            "username": u.username,
            "nickname": u.nickname,
            "verified": bool(u.verified),
            "url": url_for("user.profile", username=u.username),
        }
        for u in users
    ]
    return jsonify({"cards": card_hits, "users": user_hits})


# ---------------- 法律协议（前台公开页，供系统配置中的协议链接使用） ----------------
@main_bp.route("/privacy")
def privacy():
    return render_template("legal/privacy.html")


@main_bp.route("/tos")
def tos():
    return render_template("legal/tos.html")


# ---------------- 文章（前台） ----------------
@main_bp.route("/articles")
def articles():
    page = request.args.get("page", 1, type=int)
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort", "new")

    # 排序：最新（默认）/ 最早 / 最近更新
    if sort == "old":
        order = Article.created_at.asc()
    elif sort == "updated":
        order = Article.updated_at.desc()
    else:
        sort = "new"
        order = Article.created_at.desc()

    try:
        query = Article.query.filter_by(is_published=True)
        if q:
            like = f"%{q}%"
            query = query.filter(
                or_(
                    Article.title.like(like),
                    Article.summary.like(like),
                    Article.content.like(like),
                )
            )
        pagination = query.order_by(order).paginate(page=page, per_page=10, error_out=False)
        items = pagination.items
    except Exception:
        # 表尚未建立（如迁移未执行）时优雅降级为空列表
        pagination = None
        items = []

    # 翻页链接需保留当前搜索词与排序
    args = {}
    if q:
        args["q"] = q
    if sort != "new":
        args["sort"] = sort
    return render_template(
        "articles/index.html",
        articles=items,
        pagination=pagination,
        args=args,
        q=q,
        sort=sort,
    )


@main_bp.route("/articles/<int:article_id>")
def article_detail(article_id):
    try:
        a = db.session.get(Article, article_id)
    except Exception:
        a = None
    if a is None or not a.is_published:
        abort(404)
    return render_template("articles/show.html", article=a)
