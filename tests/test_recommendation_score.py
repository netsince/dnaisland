"""推荐算法：作者粉丝数（相对口径）+ 同作者窗口衰减 + 内容分量 + 新人保底名额。"""

import math
import random
from datetime import datetime, timedelta

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card, CardDialogueStyle, CardImage, UserFollow
from app.models.user import User
from app.routes.main import (
    _FEATURED_SCORE_CACHE,
    _FOLLOWER_REF_CACHE,
    _NEWCOMER_ID_CACHE,
    AUTHOR_WINDOW_DECAY,
    HOT_W_FOLLOWER,
    _apply_hot_score,
    _follower_reference,
    _newcomer_ids,
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
        _NEWCOMER_ID_CACHE.clear()
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
        at_ref = scores[cards[9].id]  # 恰好 P90
        above = scores[cards[10].id]  # 高于 P90

        assert low < mid < at_ref, "粉丝越多得分应越高"
        assert above == pytest.approx(at_ref), "超过基准后应封顶，不再额外加分"

        # 粉丝项本身必须与「冷启动基线」无关地成立：基线对每张卡等量相加，
        # 会稀释直接比值。改用差分——同组内 img/age/boost 相同（记为 K），
        #   score(f) - score(f') = HOT_W_FOLLOWER * (t_f - t_f') * K
        # 两个差分之比可消掉未知的 K，直接检验对数归一化的线性。
        t1 = math.log10(2) / math.log10(10)  # 1 粉
        t5 = math.log10(6) / math.log10(10)  # 5 粉
        t9 = 1.0  # 达到 P90 基准 → 打满
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
        score_map[f"a{i}"] = (10.0, 1)  # 作者 1：6 张同分卡
    for i in range(6):
        score_map[f"b{i}"] = (1.0, 2)  # 作者 2：6 张低分卡
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
    """回归：零互动新卡得分必须为正，否则加权抽样永远抽不到它。

    注意：内容分量乘数（0.4~1.0）会缩放冷启动基线，所以**空壳**新卡的分数不再
    必然 ≥ 基线本身 —— 这正是本次要的效果（空壳不该和充实卡同分）。这里只保证
    「> 0，仍可被抽到」；「有内容的新卡仍不低于基线」见下一个用例。
    """
    with app.app_context():
        a = _user("cold_a")
        c = _card(a, "fresh")
        score = _score_map()[c.id]
        assert score > 0, f"零互动新卡得分必须为正，实际 {score}"


def test_content_rich_new_card_keeps_baseline_floor(app):
    """有内容的新卡：内容分量打满 ⇒ 分数仍不低于冷启动基线（旧保证只对空壳收紧）。"""
    from app.routes.main import HOT_COLD_START_BASE, HOT_CONTENT_FULL

    with app.app_context():
        a = _user("cold_rich")
        c = _card(a, "rich")
        c.persona = "字" * int(HOT_CONTENT_FULL)
        db.session.commit()
        score = _score_map()[c.id]
        assert score >= HOT_COLD_START_BASE, (
            f"内容量已达及格线的新卡得分 {score} 不应低于冷启动基线 {HOT_COLD_START_BASE}"
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


# ---------------------------------------------------------------------------
# 内容分量：惩罚空壳，而不是奖励长度
#
# 用户反馈：字少的劣质卡会被推荐到前面，字多但新人的卡反而吃亏。
# 根因是热度分里原本没有任何内容量信号 —— 空壳卡与充实卡只要互动/图/年龄相同
# 就完全同分。下面用「同作者、同年龄、零互动、无图」的对照组来隔离这一项。
# ---------------------------------------------------------------------------


def _rich_card(author, name, chars=2000, dialogues=0, created=None):
    """造一张内容充实的卡：人设 chars 个字，可选 dialogues 组对话示例。

    [created] 显式传同一个时间时，同组卡的年龄权重完全相同 —— 否则「刚创建」
    的先后差几微秒就足以让严格不等号成立，断言会为错误的原因通过。
    """
    c = _card(author, name, created=created)
    c.persona = "字" * chars
    db.session.flush()
    for i in range(dialogues):
        db.session.add(
            CardDialogueStyle(
                card_id=c.id,
                turn_index=i,
                user_text=f"用户第{i}句",
                assistant_text=f"角色第{i}句",
            )
        )
    db.session.commit()
    return c


def test_thin_card_scores_far_below_rich_card(app):
    """同作者、同年龄、零互动、无图：空壳卡必须显著低于充实卡。

    断言写死「至少低 40%」而不是引用 HOT_CONTENT_FLOOR —— 否则把下限调回 1.0
    （等于关掉内容分量）时，断言会跟着一起放松，测试就抓不到回归了。
    """
    with app.app_context():
        a = _user("content_a")
        now = datetime.now()
        thin = _card(a, "thin", created=now)  # persona=""（空壳）
        rich = _rich_card(a, "rich", chars=2000, created=now)
        scores = _score_map()

        assert scores[thin.id] < scores[rich.id] * 0.6, (
            "空壳卡应至少比充实卡低 40%（内容分量下限），实际 "
            f"{scores[thin.id]} vs {scores[rich.id]}"
        )
        # 但仍为正：空壳卡依旧可被抽到，只是不再享受同等待遇。
        assert scores[thin.id] > 0


def test_content_multiplier_saturates(app):
    """超过及格线不再加分：避免「谁字多谁赢」的字数军备竞赛。"""
    with app.app_context():
        a = _user("content_b")
        now = datetime.now()
        below = _rich_card(a, "below", chars=900, created=now)  # 未到及格线
        at_full = _rich_card(a, "at_full", chars=1500, created=now)
        way_over = _rich_card(a, "way_over", chars=9000, created=now)
        scores = _score_map()
        assert scores[below.id] < scores[at_full.id] * 0.95, "及格线以下应继续获益"
        assert scores[at_full.id] == pytest.approx(scores[way_over.id], rel=0.01), (
            "内容量超过及格线后不应继续获益"
        )


def test_content_multiplier_ramps_monotonically(app):
    """及格线以下单调爬升：越空越低（用严格不等号，关掉内容分量即失败）。"""
    with app.app_context():
        a = _user("content_c")
        now = datetime.now()
        empty = _card(a, "c0", created=now)
        small = _rich_card(a, "c300", chars=300, created=now)
        mid = _rich_card(a, "c900", chars=900, created=now)
        full = _rich_card(a, "c1500", chars=1500, created=now)
        scores = _score_map()
        ordered = [scores[c.id] for c in (empty, small, mid, full)]
        assert ordered[0] < ordered[1] * 0.95, f"300 字应明显高于空壳，实际 {ordered}"
        assert ordered[1] < ordered[2] * 0.95, f"900 字应明显高于 300 字，实际 {ordered}"
        assert ordered[2] < ordered[3] * 0.95, f"及格线应明显高于 900 字，实际 {ordered}"
        assert ordered[0] > 0


def test_content_counts_dialogue_examples(app):
    """对话示例也算投入：人设很短但示例齐全的卡同样能打满内容分量。"""
    with app.app_context():
        a = _user("content_d")
        now = datetime.now()
        bare = _card(a, "bare", created=now)  # 人设空、无示例
        with_examples = _rich_card(a, "examples", chars=100, dialogues=8, created=now)
        rich = _rich_card(a, "persona", chars=1500, created=now)
        scores = _score_map()

        assert scores[with_examples.id] > scores[bare.id] * 1.5, "带示例的卡必须明显高于空壳卡"
        assert scores[with_examples.id] == pytest.approx(scores[rich.id], rel=0.01), (
            "8 组示例折算的字数已达及格线，应与纯人设打满的卡同分"
        )


def test_content_multiplier_cannot_bypass_reduce_boost(app):
    """内容分量在乘法括号内：降权卡的「内容收益」同样被 ×0.2 压制，不是后门。"""
    with app.app_context():
        u = _user("content_e")
        plain = _rich_card(u, "plain_rich", chars=2000)
        flagged = _rich_card(u, "flagged_rich", chars=2000)
        set_hidden_tags(flagged, [REDUCE_BOOST])
        db.session.commit()

        scores = _score_map()
        assert scores[flagged.id] == pytest.approx(scores[plain.id] * 0.2, rel=0.01)


# ---------------------------------------------------------------------------
# 新人保底名额：新人作品固定占几个推荐位
#
# 作者影响力项是加在 engagement 里的固定加数（最多 +4），与卡片质量无关：
# 老作者的零互动空壳卡得分是新人的零互动充实卡的 2.3 倍。新人缺的是曝光位，
# 只能靠固定名额来给（见 NEWCOMER_SLOTS）。
# ---------------------------------------------------------------------------


def test_newcomer_pool_definition(app):
    """新人作品口径：作者作品数 ≤ 3 且本卡发布 ≤ 14 天。"""
    from app.routes.main import NEWCOMER_CARD_MAX_AGE_DAYS, NEWCOMER_MAX_APPROVED_CARDS

    with app.app_context():
        fresh_author = _user("nc_fresh")
        first = _card(fresh_author, "first")  # 第 1 张、刚发布 ⇒ 新人作品
        old_card = _card(
            fresh_author,
            "old",
            created=datetime.now() - timedelta(days=NEWCOMER_CARD_MAX_AGE_DAYS + 1),
        )

        prolific = _user("nc_prolific")
        cards = [_card(prolific, f"p{i}") for i in range(NEWCOMER_MAX_APPROVED_CARDS + 1)]

        hidden = _user("nc_hidden")
        hidden_card = _card(hidden, "hidden")
        hidden_card.is_hidden = True
        pending = _user("nc_pending")
        pending_card = _card(pending, "pending")
        pending_card.status = "pending"
        db.session.commit()

        ids = _newcomer_ids()
        assert first.id in ids, "刚发布的第一张卡应算新人作品"
        assert old_card.id not in ids, "超过保底天数的卡不再参与"
        assert all(c.id not in ids for c in cards), (
            f"作者已通过 {len(cards)} 张卡（>{NEWCOMER_MAX_APPROVED_CARDS}）就不算新人了"
        )
        assert hidden_card.id not in ids, "隐藏卡不参与"
        assert pending_card.id not in ids, "未通过审核的卡不参与"


def _veteran_cards(author, n, prefix, with_image=False):
    """造 n 张「老作者的高分卡」：内容充实 + 高浏览 + 高复制，把加权池占满。"""
    for i in range(n):
        c = _rich_card(author, f"{prefix}{i}", chars=2000)
        c.view_count = 100000
        c.copy_count = 5000
        if with_image:
            db.session.add(
                CardImage(card_id=c.id, slot="portrait", data="data:image/png;base64,AAAA")
            )
    db.session.commit()


def test_featured_reserves_slots_for_newcomers(app, monkeypatch):
    """首页推荐：即使新人卡分数垫底，也必须靠保底名额被排进这一批。

    为了让结论**确定**而不是靠运气：
    - 关掉「纯随机名额」（randint→0）—— 否则它偶尔会顺手把新人卡抽进来，
      把「保底名额失效」这个回归掩盖掉；
    - 新人卡 boost_factor 压到 0.001（等效于「分数垫底到加权池几乎抽不到」），
      老作者 30 张高分卡把加权池占满；
    - 固定随机种子，结果可复现。
    对照验证过：NEWCOMER_SLOTS 置 0 时本用例失败。
    """
    from app.routes import main as main_mod
    from app.routes.main import _featured_score_map, featured_cards

    with app.app_context():
        veteran = _user("nc_veteran")
        _veteran_cards(veteran, 30, "v")

        rookie = _user("nc_rookie")
        rookie_card = _card(rookie, "rookie")  # 空壳 + 零互动 + 极低权重
        rookie_card.boost_factor = 0.001
        db.session.commit()

        with app.test_request_context("/"):
            _featured_score_map()  # 预热缓存，确保分数表包含新卡
            monkeypatch.setattr(main_mod.random, "randint", lambda a, b: 0)
            random.seed(20261101)
            picked = [c.id for c in featured_cards(limit=12)]

        assert rookie_card.id in picked, (
            "新人保底名额没有生效：分数垫底的新人卡被加权池挤掉了"
        )
        assert len(picked) == 12, "结果条数应保持 limit（名额不足时由加权池补满）"
        assert len(set(picked)) == 12, "同一批里不应出现重复卡"


def test_swipe_reserves_slots_for_newcomers(app, monkeypatch):
    """刷一刷：同一套三池抽样，新人卡（有封面）也保底入选。"""
    from app.routes import main as main_mod
    from app.routes.main import _featured_score_map, swipe_cards

    with app.app_context():
        veteran = _user("nc_sw_veteran")
        _veteran_cards(veteran, 30, "sv", with_image=True)

        rookie = _user("nc_sw_rookie")
        rookie_card = _card(rookie, "sw_rookie")
        rookie_card.boost_factor = 0.001
        db.session.add(
            CardImage(card_id=rookie_card.id, slot="square", data="data:image/png;base64,AAAA")
        )
        db.session.commit()

        with app.test_request_context("/"):
            _featured_score_map()
            monkeypatch.setattr(main_mod.random, "randint", lambda a, b: 0)
            random.seed(20261101)
            picked = {c.id for c in swipe_cards(limit=12)}

        assert rookie_card.id in picked, "刷一刷也应给新人作品留名额"
