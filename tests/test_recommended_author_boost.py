"""站长推荐作者的分发加成（需求原话：「站长推荐的用户加强推流，但不要太大」）。

背景：后台「站长推荐」（`SiteRecommendation`）原本只用于**展示位**（首页/App 的推荐
列表），完全不参与推流。现在把其中 `kind='user'` 的作者接进**分发层**：这些作者的
作品在首页「为你推荐」与「刷一刷」的加权抽样里权重 ×(1 + HOT_RECOMMEND_SPREAD)。

刻意**不做**的两件事，都有用例守着：
* 不进**排名分**：探索页「最热」与所有排序仍是裸起评分，站长推荐只影响抽样概率
  （与粉丝项同一原则，见 HOT_FOLLOWER_SPREAD）；
* 不认 `kind='card'`：推荐某一张具体的卡只影响展示位，不抬高整个作者。

加成幅度只有 `HOT_RECOMMEND_SPREAD` 一个旋钮（默认 0.15 ⇒ 上限 ×1.15），
「不要太大」由 test_boost_is_modest 守住。
"""

from datetime import datetime

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card, SiteRecommendation, UserFollow
from app.models.user import User
from app.routes.main import (
    _FEATURED_SCORE_CACHE,
    _FOLLOWER_REF_CACHE,
    _NEWCOMER_ID_CACHE,
    _RECOMMEND_AUTHOR_CACHE,
    HOT_FOLLOWER_SPREAD,
    HOT_RECOMMEND_SPREAD,
    _apply_hot_score,
    _featured_score_map,
    _recommended_author_ids,
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


def _clear_caches():
    _FEATURED_SCORE_CACHE.clear()
    _FOLLOWER_REF_CACHE.clear()
    _NEWCOMER_ID_CACHE.clear()
    _RECOMMEND_AUTHOR_CACHE.clear()


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    app = create_app(TestConfig)
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite")
    with app.app_context():
        db.create_all()
        _clear_caches()  # 模块级缓存：不清理会跨用例串味
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


def _user(name):
    u = User(username=name, nickname=name, email=f"{name}@x.com", password_hash="x")
    db.session.add(u)
    db.session.commit()
    return u


def _card(author, name):
    c = Card(
        id=f"card-{author.username}-{name}",
        author_id=author.id,
        name=name,
        gender="无性",
        persona="",
        status="approved",
        created_at=datetime.now(),
    )
    db.session.add(c)
    db.session.commit()
    return c


def _recommend_user(author, note=None):
    rec = SiteRecommendation(kind="user", ref_id=str(author.id), note=note, sort_order=0)
    db.session.add(rec)
    db.session.commit()
    return rec


def _recommend_card(card):
    rec = SiteRecommendation(kind="card", ref_id=card.id, sort_order=0)
    db.session.add(rec)
    db.session.commit()
    return rec


def _distribution_weights(app):
    """分发层抽样权重（首页/刷一刷用的那张表）。"""
    with app.test_request_context("/"):
        return {cid: w for cid, (w, _author) in _featured_score_map().items()}


def _ranking_scores():
    """排名分（探索页「最热」用的裸起评分）。"""
    q, expr = _apply_hot_score(Card.visible_to(None))
    return {cid: float(s or 0.0) for cid, s in q.with_entities(Card.id, expr).all()}


def test_recommended_author_gets_the_configured_boost(app):
    """站长推荐的作者：分发权重 ×(1 + HOT_RECOMMEND_SPREAD)，幅度就是那一个常量。"""
    with app.app_context():
        picked = _user("rec_author")
        plain = _user("plain_author")
        a = _card(picked, "x")
        b = _card(plain, "x")
        _recommend_user(picked)

        w = _distribution_weights(app)
        assert w[b.id] > 0, "对照卡必须有非零权重，否则比值没有意义"
        assert w[a.id] / w[b.id] == pytest.approx(1.0 + HOT_RECOMMEND_SPREAD, rel=0.01), (
            "被推荐作者的权重应恰好多出 HOT_RECOMMEND_SPREAD 这一档"
        )


def test_boost_does_not_touch_ranking_score(app):
    """站长推荐只影响分发抽样，不进任何排名分（探索页「最热」仍是裸分）。"""
    with app.app_context():
        picked = _user("rec_author2")
        plain = _user("plain_author2")
        a = _card(picked, "y")
        b = _card(plain, "y")
        _recommend_user(picked)

        s = _ranking_scores()
        assert s[a.id] == pytest.approx(s[b.id]), (
            "排名分必须与站长推荐无关：它只改抽样概率，不改单卡评分口径"
        )


def test_card_kind_recommendation_does_not_boost(app):
    """kind='card' 只影响展示位：推荐某一张卡不该抬高整个作者。"""
    with app.app_context():
        picked = _user("card_rec_author")
        plain = _user("plain_author3")
        a = _card(picked, "z")
        b = _card(plain, "z")

        before = _distribution_weights(app)
        assert before[a.id] == pytest.approx(before[b.id])

        _recommend_card(a)  # 推荐这张卡（不是作者）
        _FEATURED_SCORE_CACHE.clear()
        _RECOMMEND_AUTHOR_CACHE.clear()
        after = _distribution_weights(app)
        assert after[a.id] == pytest.approx(after[b.id]), (
            "kind='card' 不应该改变分发权重（想给单卡加权请用那张卡自己的推荐位）"
        )
        assert _recommended_author_ids() == frozenset(), "作者集合里只应有 kind='user' 的推荐"


def test_boost_is_modest(app):
    """「不要太大」：一个常量控制强度，且必须落在温和区间内。"""
    assert HOT_RECOMMEND_SPREAD > 0, "加成必须真的存在（需求就是加强推流）"
    assert HOT_RECOMMEND_SPREAD <= 0.5, (
        "站长推荐是「扶一把」不是「保送」：超过 +50% 的抽样权重会让推荐位变成私人版面；"
        "要调整请改这一个常量，并同步更新本条断言与注释里的说明"
    )
    # 与粉丝项同一量级：两档叠加后仍然是温和的（×1.2 × 1.15 ≈ ×1.38 封顶）。
    assert 1.0 + HOT_RECOMMEND_SPREAD <= 1.5


def test_boost_is_multiplicative_with_follower_breadth(app):
    """粉丝广度与站长推荐叠加（两者都在分发层、都有界，互不干扰）。"""
    with app.app_context():
        # 10 位粉丝 + 三位作者：1 粉未推荐 / 10 粉未推荐 / 10 粉被推荐。
        # 10 位粉丝是为了让 P90 基准 > 1，粉丝项才非 0（口径见 HOT_FOLLOWER_SPREAD）。
        followers = [_user(f"g{i}") for i in range(10)]
        for n in (1, 10):
            author = _user(f"fb{n}")
            for i in range(n):
                db.session.add(UserFollow(follower_id=followers[i].id, following_id=author.id))
        twin = _user("fb10_twin")
        for i in range(10):
            db.session.add(UserFollow(follower_id=followers[i].id, following_id=twin.id))
        db.session.commit()
        _FOLLOWER_REF_CACHE.clear()

        low = _card(db.session.query(User).filter_by(username="fb1").one(), "w")
        high = _card(db.session.query(User).filter_by(username="fb10").one(), "w")
        high_rec = _card(twin, "w")  # 与 high 同为 10 粉，但作者被站长推荐
        _recommend_user(twin)
        _FEATURED_SCORE_CACHE.clear()

        w = _distribution_weights(app)
        assert w[high.id] > w[low.id], "粉丝项本身仍然生效"
        assert w[high_rec.id] / w[high.id] == pytest.approx(1.0 + HOT_RECOMMEND_SPREAD, rel=0.02), (
            "同样粉丝数下，被推荐作者恰好多出 HOT_RECOMMEND_SPREAD 这一档（乘性叠加）"
        )


def test_new_recommendation_takes_effect_after_cache_ttl(app):
    """推荐集合带 60s 缓存：清掉缓存后新增的推荐立刻生效（后台改完最多 1 分钟）。"""
    with app.app_context():
        picked = _user("late_rec")
        plain = _user("plain_author4")
        a = _card(picked, "v")
        b = _card(plain, "v")

        w = _distribution_weights(app)
        assert w[a.id] == pytest.approx(w[b.id]), "还没推荐时两者应同权"

        _recommend_user(picked)
        # 缓存未过期：仍按旧集合算（这正是 60s 延迟的来源）
        _FEATURED_SCORE_CACHE.clear()
        assert _recommended_author_ids() == frozenset(), "推荐集合仍命中旧缓存"
        w_cached = _distribution_weights(app)
        assert w_cached[a.id] == pytest.approx(w_cached[b.id])

        # 缓存过期/被清掉之后生效
        _RECOMMEND_AUTHOR_CACHE.clear()
        _FEATURED_SCORE_CACHE.clear()
        w_fresh = _distribution_weights(app)
        assert w_fresh[a.id] > w_fresh[b.id], "新推荐在缓存刷新后必须生效"


def test_both_entry_points_use_the_boosted_map(app):
    """首页「为你推荐」与「刷一刷」都从 _featured_score_map 取权重，所以两者都吃到加成。

    静态守护：这两个入口一旦绕过这张表（各自算分），站长推荐就会只对其中一个生效。
    """
    import inspect

    from app.routes import main as main_mod

    for fn in (main_mod.featured_cards, main_mod.swipe_cards):
        src = inspect.getsource(fn)
        assert "_featured_score_map()" in src, (
            f"{fn.__name__} 必须用 _featured_score_map() 取分发权重，否则站长推荐对它不生效"
        )
