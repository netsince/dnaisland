"""角色卡检索：CJK bigram 倒排 + BM25F。

本文件锁定的是**生产实况回归**（MariaDB 无 ngram，全文索引对中文片段恒 0 命中）：

* 卡名「后藤一里（Gotou Hitori）/ 波奇（ぼっち）」搜「藤」「藤一」「后藤一」此前全部
  0 命中，搜「波奇」（二字词）也是 0，只有整段卡名才命中；
* 用户实例：后台按卡名 LIKE 能搜到 7 张未隐藏的「辞安」卡，前台只返回 3 张（还是靠
  标签撞上的），卡名/简介/人设里的「辞安」一张都没匹配上。

同时锁定：**API 参数与响应字段不变**、索引不可用时静默回退 LIKE（老客户端与
"先部署后迁移"都必须无感）。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card, CardTag
from app.models.user import User
from app.services import search_service as ss
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
    with app.app_context():
        db.create_all()
        ss._invalidate_meta_cache()  # 模块级缓存：不清理会跨用例串味
        yield app
        ss._invalidate_meta_cache()
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _user(name="u1"):
    u = User(username=name, nickname=name, email=f"{name}@example.com")
    u.set_password("pass123")
    db.session.add(u)
    db.session.commit()
    return u


def _card(author, cid, name, persona="", intro="", status="approved", tags=()):
    c = Card(
        id=cid,
        author_id=author.id,
        name=name,
        persona=persona,
        intro=intro,
        status=status,
    )
    db.session.add(c)
    db.session.flush()
    for t in tags:
        db.session.add(CardTag(card_id=cid, tag=t))
    db.session.commit()
    return c


# ---------------------------------------------------------------------------
# 切词
# ---------------------------------------------------------------------------


def test_tokenizer_bigrams_for_cjk():
    assert ss.token_counts("辞安") == {"辞安": 1}
    assert ss.token_counts("后藤一里") == {"后藤": 1, "藤一": 1, "一里": 1}
    # 重复出现累加词频
    assert ss.token_counts("辞安辞安") == {"辞安": 2, "安辞": 1}


def test_tokenizer_latin_words_and_bigrams():
    counts = ss.token_counts("Gotou")
    assert counts["gotou"] == 1  # 整词
    assert counts["go"] == 1 and counts["ot"] == 1 and counts["ou"] == 1  # 片段


def test_tokenizer_normalizes_fullwidth_and_case():
    assert ss.token_counts("ＡＢＣ") == ss.token_counts("abc")
    assert "abc" in ss.token_counts("ＡＢＣ")


def test_tokenizer_single_cjk_char_yields_nothing():
    """单字不建索引（bigram 覆盖不到），由 LIKE 兜底 —— 这是有意为之。"""
    assert ss.token_counts("傲") == {}
    assert ss.query_tokens("傲") == []


def test_tokenizer_splits_on_punctuation():
    """标点是分隔符：「后藤一里（Gotou Hitori）」不产生跨标点的 bigram。"""
    counts = ss.token_counts("后藤一里（Gotou Hitori）")
    assert "里（" not in counts
    assert "后藤" in counts and "gotou" in counts and "hitori" in counts


def test_tokenizer_drops_overlong_word_but_keeps_its_bigrams():
    """回归（生产回填实况）：正文里的超长拉丁串会让 gram 超出列宽报 1406。

    整词丢弃（搜不到整串无所谓），但二元组保留 —— 搜其中一段仍然命中。
    """
    long_token = "a" * 40 + "xyz"
    counts = ss.token_counts(long_token)
    assert all(len(g) <= ss.MAX_GRAM_LEN for g in counts), "不能产出超长 gram"
    assert long_token not in counts
    assert "xy" in counts and "yz" in counts


# ---------------------------------------------------------------------------
# 召回：片段、二字词、标签、跨字段
# ---------------------------------------------------------------------------


def test_fragment_of_a_run_is_findable(app):
    """回归（生产实况）：整段是「后藤一里」，搜「藤一」此前 0 命中。"""
    with app.app_context():
        author = _user()
        c = _card(author, "c1", "后藤一里（Gotou Hitori）/ 波奇（ぼっち）")
        ss.reindex_card(c.id)

        for frag in ("藤", "藤一", "后藤一", "后藤一里"):
            if frag == "藤":  # 单字走 LIKE 兜底，不走倒排
                assert ss.query_tokens(frag) == []
                continue
            assert c.id in ss.rank_card_ids(frag), f"片段「{frag}」应该能召回"


def test_two_char_word_inside_persona_is_findable(app):
    """回归（生产实况）：二字词「波奇」「辞安」此前 MATCH 恒 0 命中。"""
    with app.app_context():
        author = _user()
        c = _card(author, "c1", "某卡", persona="她叫波奇，性格内向。辞安是她姐姐。")
        ss.reindex_card(c.id)
        assert c.id in ss.rank_card_ids("波奇")
        assert c.id in ss.rank_card_ids("辞安")
        assert c.id in ss.rank_card_ids("内向")


def test_tag_only_hit_is_recallable(app):
    """只有标签含关键词的卡也必须能召回（旧口径靠 tag LIKE，索引不能丢这条）。"""
    with app.app_context():
        author = _user()
        c = _card(author, "c1", "无关卡名", tags=("辞安",))
        ss.reindex_card(c.id)
        assert c.id in ss.rank_card_ids("辞安")


def test_multi_fragment_query_requires_all(app):
    """空格分隔的多片段是「都要出现」（AND），不是任选其一。"""
    with app.app_context():
        author = _user()
        both = _card(author, "both", "卡", persona="傲娇又青梅")
        only_one = _card(author, "one", "卡", persona="只有傲娇")
        ss.reindex_card(both.id)
        ss.reindex_card(only_one.id)

        hits = ss.rank_card_ids("傲娇 青梅")
        assert both.id in hits
        assert only_one.id not in hits


def test_unrelated_query_returns_nothing(app):
    with app.app_context():
        author = _user()
        c = _card(author, "c1", "辞安")
        ss.reindex_card(c.id)
        assert ss.rank_card_ids("完全无关的词") == []


# ---------------------------------------------------------------------------
# BM25F 排序性质
# ---------------------------------------------------------------------------


def test_name_hit_outranks_persona_hit(app):
    with app.app_context():
        author = _user()
        by_name = _card(author, "n", "辞安")
        by_persona = _card(author, "p", "别的名字", persona="人设里提到了辞安一次")
        ss.reindex_card(by_name.id)
        ss.reindex_card(by_persona.id)

        ranked = ss.rank_card_ids("辞安")
        assert ranked[0] == by_name.id, f"卡名命中应排最前，实际 {ranked}"


def test_shorter_field_outranks_longer_field(app):
    """BM25 的长度归一化：同样命中一次，短人设应排在超长人设之前。"""
    with app.app_context():
        author = _user()
        short = _card(author, "s", "卡A", persona="辞安")
        long = _card(author, "l", "卡B", persona="辞安" + "填充内容" * 1200)
        ss.reindex_card(short.id)
        ss.reindex_card(long.id)

        ranked = ss.rank_card_ids("辞安")
        assert ranked.index(short.id) < ranked.index(long.id), f"实际 {ranked}"


def test_more_occurrences_rank_higher(app):
    with app.app_context():
        author = _user()
        once = _card(author, "o", "卡A", persona="辞安")
        thrice = _card(author, "t", "卡B", persona="辞安辞安辞安")
        ss.reindex_card(once.id)
        ss.reindex_card(thrice.id)

        ranked = ss.rank_card_ids("辞安")
        assert ranked.index(thrice.id) < ranked.index(once.id), f"实际 {ranked}"


# ---------------------------------------------------------------------------
# 索引维护：写入即自动重建
# ---------------------------------------------------------------------------


def test_new_card_is_indexed_automatically(app):
    """建卡（发布/导入路径）不需要显式调用，事件会自动建索引。"""
    with app.app_context():
        author = _user()
        # 先让索引「可用」：任何一次重建后，后续写入才会自动跟上。
        seed = _card(author, "seed", "种子卡", persona="辞安")
        ss.reindex_card(seed.id)
        assert ss.index_available(force=True)

        _card(author, "new", "新卡", persona="傲娇青梅竹马")
        assert ss.SearchGram.query.filter_by(doc_id="new").count() > 0
        assert "new" in ss.rank_card_ids("青梅")


def test_editing_persona_updates_index(app):
    with app.app_context():
        author = _user()
        c = _card(author, "c1", "卡", persona="原来的设定")
        ss.reindex_card(c.id)
        assert c.id not in ss.rank_card_ids("青梅")

        c.persona = "改成了青梅竹马"
        db.session.commit()  # 事件应在提交后重建
        assert c.id in ss.rank_card_ids("青梅")


def test_unapproved_card_is_not_indexed_by_bulk(app):
    with app.app_context():
        author = _user()
        _card(author, "ok", "通过卡", status="approved")
        _card(author, "no", "待审卡", status="pending")
        cards, _rows = ss.reindex_all()
        assert cards == 1
        assert ss.SearchDocStat.query.filter_by(doc_id="no").count() == 0


def test_reindex_is_idempotent(app):
    with app.app_context():
        author = _user()
        c = _card(author, "c1", "辞安", persona="辞安的人设")
        ss.reindex_card(c.id)
        first = ss.SearchGram.query.filter_by(doc_id=c.id).count()
        ss.reindex_card(c.id)
        assert ss.SearchGram.query.filter_by(doc_id=c.id).count() == first


# ---------------------------------------------------------------------------
# 端到端：API 行为与响应契约
# ---------------------------------------------------------------------------


def test_api_finds_name_fragment_with_index(app, client):
    """用户实例回归：搜「辞安」必须返回卡名含「辞安」的卡（此前 MATCH 命中 0）。"""
    with app.app_context():
        author = _user()
        _card(author, "n1", "辞安（老鸨）", persona="人设")
        _card(author, "n2", "辞安", persona="人设")
        _card(author, "n3", "白切黑版辞安", persona="人设")
        ss.reindex_all()

    r = client.get("/api/v1/cards/search?q=辞安")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    ids = {it["id"] for it in body["data"]["items"]}
    assert {"n1", "n2", "n3"} <= ids, f"应全部召回，实际 {ids}"
    # 响应字段与旧版一致（老客户端不改）
    item = body["data"]["items"][0]
    for key in ("id", "name", "gender", "intro", "view_count", "covers", "author"):
        assert key in item, f"响应缺少字段 {key}"


def test_api_still_works_without_index(app, client):
    """索引未回填/未迁移时静默回退 LIKE：结果依旧正确，绝不 500。"""
    with app.app_context():
        author = _user()
        _card(author, "n1", "辞安（老鸨）", persona="人设")
        assert not ss.index_available(force=True), "本用例前提是索引为空"

    r = client.get("/api/v1/cards/search?q=辞安")
    assert r.status_code == 200
    items = r.get_json()["data"]["items"]
    assert [it["id"] for it in items] == ["n1"]


def test_api_single_char_query_falls_back_to_like(app, client):
    """单字查询走 LIKE 兜底（bigram 覆盖不到），不能因为索引而变成 0 结果。"""
    with app.app_context():
        author = _user()
        _card(author, "n1", "辞安", persona="人设")
        ss.reindex_all()

    r = client.get("/api/v1/cards/search?q=安")
    assert r.status_code == 200
    assert [it["id"] for it in r.get_json()["data"]["items"]] == ["n1"]


def test_api_sort_hot_and_new_still_work(app, client):
    with app.app_context():
        author = _user()
        _card(author, "n1", "辞安甲")
        _card(author, "n2", "辞安乙")
        ss.reindex_all()

    for sort in ("hot", "new", "relevance"):
        r = client.get(f"/api/v1/cards/search?q=辞安&sort={sort}")
        assert r.status_code == 200
        assert len(r.get_json()["data"]["items"]) == 2, f"sort={sort} 结果数不对"


def test_api_suggest_uses_index(app, client):
    with app.app_context():
        author = _user()
        _card(author, "n1", "辞安")
        ss.reindex_all()

    r = client.get("/api/v1/search/suggest?q=辞安")
    assert r.status_code == 200
    assert [c["id"] for c in r.get_json()["data"]["cards"]] == ["n1"]
