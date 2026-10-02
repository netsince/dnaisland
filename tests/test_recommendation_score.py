"""推荐算法：作者粉丝数（相对口径）+ 同作者窗口衰减。"""

import math
import random
from datetime import datetime, timedelta

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card, UserFollow
from app.models.user import User
from app.routes.main import (
    _FEATURED_SCORE_CACHE,
    _FOLLOWER_REF_CACHE,
    AUTHOR_WINDOW_DECAY,
    HOT_W_FOLLOWER,
    _apply_hot_score,
    _follower_reference,
    _sample_weights,
    _weighted_sample_with_author_decay,
)
from app.services.card_hidden_tags import REDUCE_BOOST, set_hidden_tags
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles


@compiles(LONGTEXT, "sqlite")
def compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    app = create_app(TestConfig)
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite")
    with app.app_context():
        db.create_all()
        # 推荐相关的缓存是模块级全局：不清空会让用例互相污染
        # （例如上个用例缓存的「匿名 viewer 分数表」里没有本用例新建的卡）。
        _FOLLOWER_REF_CACHE.clear()
        _FEATURED_SCORE_CACHE.clear()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


def _user(name):
    # 直接给 password_hash 占位：这些用例不需要登录，避免 bcrypt 拖慢测试。
    u = User(username=name, nickname=name, email=f"{name}@x.com", password_hash="x")
    db.session.add(u)
    db.session.commit()
    return u


def _card(author, name, created=None):
    c = Card(
        id=f"card-{author.username}-{name}",
        author_id=author.id,
        name=name,
        gender="无性",
        persona="",
        status="approved",
        # 用「现在」避免年龄衰减把分数压到 1e-12（会让差值断言失去意义）。
        created_at=created or datetime.now(),
    )
    db.session.add(c)
    db.session.commit()
    return c


def _score_map():
    q, expr = _apply_hot_score(Card.visible_to(None))
    return {cid: float(s or 0.0) for cid, s in q.with_entities(Card.id, expr).all()}


# ---------------------------------------------------------------------------
# 作者粉丝数：相对口径（P90 归一化 + 封顶）
# ---------------------------------------------------------------------------

def test_follower_reference_is_p90(app):
    """基准取全体作者粉丝数的 P90，而不是写死的绝对值。"""
    with app.app_context():
        followers = [_user(f"f{i}") for i in range(10)]
        for n in range(1, 11):  # 作者粉丝数 1..10
            a = _user(f"a{n}")
            for i in range(n):
                db.session.add(UserFollow(follower_id=followers[i].id, following_id=a.id))
        db.session.commit()
        _FOLLOWER_REF_CACHE.clear() if hasattr(_FOLLOWER_REF_CACHE, "clear") else None
        ref = _follower_reference()
        assert ref == 9.0, f"P90 应为 9（1..10 的第 90 分位），实际 {ref}"


def test_follower_term_raises_score_and_saturates(app):
    """粉丝多的作者得分更高；但达到基准后不再继续加分（防头部）。"""
    with app.app_context():
        followers = [_user(f"g{i}") for i in range(10)]
        cards = {}
        for n in range(1, 11):
            a = _user(f"b{n}")
            for i in range(n):
                db.session.add(UserFollow(follower_id=followers[i].id, following_id=a.id))
            cards[n] = _card(a, "x")
        db.session.commit()

        scores = _score_map()
        low = scores[cards[1].id]
        mid = scores[cards[5].id]
        at_ref = scores[cards[9].id]      # 恰好 P90
        above = scores[cards[10].id]      # 高于 P90

        assert low < mid < at_ref, "粉丝越多得分应越高"
        assert above == pytest.approx(at_ref), "超过基准后应封顶，不再额外加分"

        # 粉丝项本身必须与「冷启动基线」无关地成立：基线对每张卡等量相加，
        # 会稀释直接比值。改用差分——同组内 img/age/boost 相同（记为 K），
        #   score(f) - score(f') = HOT_W_FOLLOWER * (t_f - t_f') * K
        # 两个差分之比可消掉未知的 K，直接检验对数归一化的线性。
        t1 = math.log10(2) / math.log10(10)    # 1 粉
        t5 = math.log10(6) / math.log10(10)    # 5 粉
        t9 = 1.0                               # 达到 P90 基准 → 打满
        per_unit_mid = (mid - low) / (t5 - t1)
        per_unit_ref = (at_ref - low) / (t9 - t1)
        assert per_unit_mid == pytest.approx(per_unit_ref, rel=0.02), (
            "粉丝项应按 log10(1+f)/log10(1+基准) 归一化后线性加权"
        )


def test_follower_reference_scales_with_platform(app):
    """平台整体涨粉后基准同步抬高：相对分位不变则得分关系不变。"""
    with app.app_context():
        followers = [_user(f"h{i}") for i in range(10)]
        # 第一轮：粉丝数 1..10
        authors = {}
        for n in range(1, 11):
            a = _user(f"c{n}")
            authors[n] = a
            for i in range(n):
                db.session.add(UserFollow(follower_id=followers[i].id, following_id=a.id))
        db.session.commit()
        ref_small = _follower_reference()

        # 第二轮：整体 ×10（模拟平台火了），P90 也应 ×10
        for n in range(1, 11):
            extra = [_user(f"e{n}_{k}") for k in range(n * 9)]
            for u in extra:
                db.session.add(UserFollow(follower_id=u.id, following_id=authors[n].id))
        db.session.commit()
        _FOLLOWER_REF_CACHE.clear() if hasattr(_FOLLOWER_REF_CACHE, "clear") else None
        ref_big = _follower_reference()

        assert ref_small == 9.0
        assert ref_big == 90.0, f"平台规模 ×10 后基准应 ×10，实际 {ref_big}"


# ---------------------------------------------------------------------------
# 同作者窗口衰减
# ---------------------------------------------------------------------------

def test_sample_weights_halve_per_extra_card_from_same_author():
    score_map = {
        "a1": (10.0, 1),
        "a2": (10.0, 1),
        "b1": (10.0, 2),
    }
    # 无人入选时：三者权重相同
    assert _sample_weights(["a1", "a2", "b1"], score_map, {}) == [10.0, 10.0, 10.0]
    # 作者 1 已入选 1 张：其剩余卡权重减半
    assert _sample_weights(["a1", "a2", "b1"], score_map, {1: 1}) == [
        10.0 * AUTHOR_WINDOW_DECAY,
        10.0 * AUTHOR_WINDOW_DECAY,
        10.0,
    ]
    # 已入选 2 张：再减半
    assert _sample_weights(["a1"], score_map, {1: 2}) == [10.0 * AUTHOR_WINDOW_DECAY**2]


def test_weighted_sample_spreads_across_authors(monkeypatch):
    """同作者衰减确实把单个作者的卡在一个窗口里摊开（与关闭衰减做对照）。

    固定随机种子，因此结论是确定性的、不会偶发失败。
    """
    from app.routes import main as main_mod

    score_map = {}
    for i in range(6):
        score_map[f"a{i}"] = (10.0, 1)   # 作者 1：6 张同分卡
    for i in range(6):
        score_map[f"b{i}"] = (1.0, 2)    # 作者 2：6 张低分卡
    pool = list(score_map)

    def _avg_author1(trials=80):
        total = 0
        for _ in range(trials):
            picked = _weighted_sample_with_author_decay(pool, score_map, 6)
            total += sum(1 for cid in picked if score_map[cid][1] == 1)
        return total / trials

    random.seed(20261001)
    with_decay = _avg_author1()
    monkeypatch.setattr(main_mod, "AUTHOR_WINDOW_DECAY", 1.0)  # 关闭衰减做对照
    random.seed(20261001)
    without_decay = _avg_author1()

    assert with_decay < without_decay, (
        f"衰减应降低同作者占比：有衰减 {with_decay} vs 无衰减 {without_decay}"
    )


def test_weighted_sample_handles_zero_scores():
    score_map = {"a": (0.0, 1), "b": (0.0, 2)}
    assert _weighted_sample_with_author_decay(list(score_map), score_map, 2) == []


# ---------------------------------------------------------------------------
# 冷启动：零互动新卡不得恒为 0 分
#
# 旧实现 score = engagement × img × age × boost，年龄加成是**乘数**，
# 零互动新卡 engagement 恒为 0，于是 0 × 1.4 = 0：在探索页垫底、
# 在首页加权抽样中权重为 0（数学上永远抽不到）。
# ---------------------------------------------------------------------------

def test_zero_engagement_new_card_scores_above_zero(app):
    """回归：零互动新卡得分必须为正，且不低于冷启动基线。"""
    from app.routes.main import HOT_COLD_START_BASE

    with app.app_context():
        a = _user("cold_a")
        c = _card(a, "fresh")
        score = _score_map()[c.id]
        assert score > 0, f"零互动新卡得分必须为正，实际 {score}"
        # 新卡（age≤5、无图）的年龄系数 ≥1.0、带图系数 1.0，故分数不低于基线本身。
        assert score >= HOT_COLD_START_BASE, (
            f"零互动新卡得分 {score} 应不低于冷启动基线 {HOT_COLD_START_BASE}"
        )


def test_zero_engagement_new_card_outranks_zero_engagement_old_card(app):
    """同样零互动：新卡必须压过老卡，因为基线同样吃年龄衰减。"""
    with app.app_context():
        a = _user("cold_b")
        old = _card(a, "old", created=datetime.now() - timedelta(days=30))
        new = _card(a, "new")
        scores = _score_map()
        assert scores[new.id] > scores[old.id], (
            f"新卡 {scores[new.id]} 应高于 30 天前的老卡 {scores[old.id]}"
        )


def test_cold_start_baseline_is_still_suppressed_by_reduce_boost(app):
    """基线在乘法括号内：降权卡的新卡基线同样 ×0.2，不能成为绕过降权的后门。"""
    with app.app_context():
        u = _user("cold_c")
        now = datetime.now()
        plain = _card(u, "plain", created=now)
        flagged = _card(u, "flagged", created=now)
        set_hidden_tags(flagged, [REDUCE_BOOST])
        db.session.commit()

        scores = _score_map()
        assert scores[plain.id] > 0
        assert scores[flagged.id] == pytest.approx(scores[plain.id] * 0.2, rel=0.01)


def test_zero_engagement_new_card_has_nonzero_sampling_weight(app):
    """首页加权抽样：零互动新卡权重必须 > 0（旧实现为 0，永远抽不到）。"""
    from app.routes.main import _featured_score_map

    with app.app_context():
        a = _user("cold_d")
        c = _card(a, "fresh")
        with app.test_request_context("/"):
            smap = _featured_score_map()
            assert c.id in smap, "零互动新卡必须进入首页候选池"
            assert _sample_weights([c.id], smap, {})[0] > 0, "零互动新卡在首页抽样中的权重必须为正"


def test_new_card_sorts_before_old_card_with_a_single_view(app):
    """端到端：老卡只要被点开过**一次**，就不得压过零互动的全新卡。

    这是「新卡沉底」最直接的复现：旧实现里零互动新卡恒为 0 分，
    而任何被点开过一次的老卡都有 log10(2)≈0.301 的正分，必然排在前面。
    """
    from app.routes.main import _order_by_hot

    with app.app_context():
        a = _user("cold_e")
        old = _card(a, "old", created=datetime.now() - timedelta(days=45))
        old.view_count = 1
        new = _card(a, "new")
        db.session.commit()
        ids = [cid for (cid,) in _order_by_hot(Card.visible_to(None)).with_entities(Card.id).all()]
        assert ids.index(new.id) < ids.index(old.id), (
            "零互动新卡必须排在「只被点开过一次」的老卡之前"
        )
