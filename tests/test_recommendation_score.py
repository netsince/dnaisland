"""推荐算法：作者粉丝数（相对口径）+ 同作者窗口衰减。"""

import random
from datetime import datetime

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card, UserFollow
from app.models.user import User
from app.routes.main import (
    _FOLLOWER_REF_CACHE,
    AUTHOR_WINDOW_DECAY,
    HOT_W_FOLLOWER,
    _apply_hot_score,
    _follower_reference,
    _sample_weights,
    _weighted_sample_with_author_decay,
)
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
        _FOLLOWER_REF_CACHE._store.clear() if hasattr(_FOLLOWER_REF_CACHE, "_store") else None
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
        # 用比值断言（年龄权重对所有卡相同，会在比值里抵消）：
        # 1 粉 → log10(2)/log10(10) ≈ 0.301，基准 → 1.0
        assert at_ref / low == pytest.approx(1 / 0.30103, rel=0.02)


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
