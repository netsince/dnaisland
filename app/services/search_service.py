"""角色卡检索内核：CJK bigram 倒排 + BM25F 打分（自带索引，不依赖数据库全文检索）。

## 为什么要自己写

生产是 **MariaDB**（实测 11.3.2），它**没有 ngram 解析器**（`ngram_token_size` 变量
不存在），迁移里 `WITH PARSER ngram` 的 MySQL 分支从来没生效过，落回默认解析器：

* 默认解析器只按「非字母数字」切词，**一整段中文到标点为止算一个 token**；
* `innodb_ft_min_token_size = 3`，**短于 3 字符的词根本不进索引**。

生产实测（卡名「后藤一里（Gotou Hitori）/ 波奇（ぼっち）」）：

| 查询 | MATCH 命中 | LIKE 命中 |
|---|---|---|
| 藤 / 藤一 / 后藤一 | 0 / 0 / 0 | 2 / 1 / 1 |
| 后藤一里（正好是整段） | 1 | 1 |
| 波奇（二字词） | **0** | 2 |

语料侧：417 张中文名卡里 **381 张卡名没有标点** → 整张卡名就是一个 token，
等于「必须一字不差输完整卡名」才搜得到。用户反馈的「搜片段压根搜不到」即此。

## 这里的做法（对标搜索引擎，但不引服务）

1. **切词**：CJK 取相邻**二元组（bigram）**；拉丁/数字取**整词 + 其二元组**
   （整词命中精确，二元组让前缀/片段也能命中）。NFKC 归一 + 小写化。
   —— bigram 是 CJK 无词典检索的经典解，天然支持任意片段。
2. **倒排**：`search_grams(doc_type, doc_id, field, gram, tf)`，`gram` 上有索引。
3. **召回**：查询词切出的所有 gram **取交集（AND）**。连续中文查询因此等价于
   短语匹配；空格分隔的多片段则是「都要出现」。
4. **打分**：**BM25F**——先按字段长度归一化 tf、按字段权重合成虚拟 tf，再套
   BM25 的饱和函数。字段权重 name ≫ tag > intro/persona（搜卡名该排最前）。
5. **兜底**：单字查询（bigram 覆盖不到）或索引不可用时回退 LIKE，见
   `routes/main.py::_card_search_query`。索引缺失/为空一律静默回退，绝不 500。

## 边界（不改对外定义）

* 只新增两张表 + 一套内部实现，**API 响应字段与参数一个都不动**，老客户端无感；
* 排序结果变好是唯一可见变化（相关性更准）；
* 索引维护走 SQLAlchemy 事件（见本文件底部），任何卡片写入路径都覆盖，
  且**索引失败绝不影响业务写入**。
"""

from __future__ import annotations

import math
import unicodedata

from sqlalchemy import event, func, inspect
from sqlalchemy.orm import InstanceState, Session

from ..caching import TimedCache
from ..extensions import db
from ..models import Card, CardTag
from ..models.search_index import SearchDocStat, SearchGram

# ---------------------------------------------------------------------------
# 打分与切词参数
# ---------------------------------------------------------------------------

DOC_CARD = "card"

# BM25 饱和参数：越大越"奖励词频"，1.2 是通用默认值。
BM25_K1 = 1.2

# BM25F 字段权重：搜「辞安」时，卡名叫《辞安》的必须排在"人设里提了一句"的前面。
FIELD_WEIGHTS: dict[str, float] = {
    "name": 6.0,
    "tag": 3.0,
    "intro": 2.0,
    "persona": 1.0,
}

# BM25F 字段长度归一化强度 b：标签/卡名是"短标签"，不做长度惩罚（b=0）；
# 正文类字段 b=0.75，长文本里的偶发命中会被压低（这正是 BM25 相对 TF-IDF 的价值）。
FIELD_B: dict[str, float] = {
    "name": 0.0,
    "tag": 0.0,
    "intro": 0.75,
    "persona": 0.75,
}

# 可检索字段（与既有口径一致：卡名 / 简介 / 人设 / 标签）。
# 想扩到开场白或对话示例，只需在此加一项 + 在 _card_field_texts 里补一行。
CARD_FIELDS: tuple[str, ...] = ("name", "intro", "persona", "tag")

# 查询词切出的 gram 数上限：超长查询只取前若干个参与召回，避免倒排扫描过大。
MAX_QUERY_GRAMS = 16
# 单个 gram 的最大长度，必须 ≤ search_grams.gram 列宽（32）。
# 卡片正文里会出现超长拉丁串（URL、哈希、base64 片段）——**整词 token 超过上限就丢弃**，
# 但它的二元组照常入库，所以"搜其中一段"依然命中（生产回填时踩过 1406 Data too long）。
MAX_GRAM_LEN = 32
# 一次召回拉取倒排行的上限（防御性：异常查询不至于把内存拉爆）。
MAX_POSTING_ROWS = 20000
# 进精排的候选数上限（BM25F 在 Python 侧算，需要封顶）。
MAX_CANDIDATES = 400

# 索引可用性 / 文档统计缓存（秒）。索引内容变化不频繁，60s 足够。
_META_TTL = 60
_AVAILABLE_CACHE = TimedCache(ttl=_META_TTL, maxsize=2)
_AVG_LEN_CACHE = TimedCache(ttl=_META_TTL, maxsize=2)


# ---------------------------------------------------------------------------
# 切词
# ---------------------------------------------------------------------------

# CJK 与假名区段（bigram 切词只对这些字符做）
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3040, 0x30FF),  # 日文平假名/片假名
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xF900, 0xFAFF),  # CJK 兼容表意
    (0x20000, 0x2FA1F),  # CJK 扩展 B~F
)


def normalize(text: str | None) -> str:
    """检索前归一：NFKC（全角→半角、兼容字符）+ 小写。

    这样「ＡＢＣ」「ABC」「abc」等价，「１」与「1」等价 —— 用户不会因为输入法
    的全角/半角差异而搜不到。
    """
    if not text:
        return ""
    return unicodedata.normalize("NFKC", text).lower()


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _is_word(ch: str) -> bool:
    """拉丁字母 / 数字 / 下划线（与 CJK 一起构成"可检索字符"）。"""
    return ch.isalnum() or ch == "_"


def token_counts(text: str | None) -> dict[str, int]:
    """把文本切成检索 token 并统计词频（建索引与查询两侧共用同一套规则）。

    * CJK 连续段：相邻二元组。**长度为 1 的段不产出 token**（单字查询由 LIKE 兜底，
      避免为每个汉字再建一份一元索引把索引体积翻倍）；
    * 拉丁/数字连续段：整词 + 其相邻二元组（`Gotou` 既能整词命中，也能被 `Got` 命中）；
    * 其它字符（标点、空白、emoji）视作分隔符。
    """
    counts: dict[str, int] = {}
    text = normalize(text)
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if _is_cjk(ch):
            j = i
            while j < n and _is_cjk(text[j]):
                j += 1
            run = text[i:j]
            for k in range(len(run) - 1):
                gram = run[k : k + 2]
                counts[gram] = counts.get(gram, 0) + 1
            i = j
        elif _is_word(ch):
            j = i
            while j < n and _is_word(text[j]):
                j += 1
            run = text[i:j]
            # 超长拉丁串（URL/哈希/base64）不整词入索引，但二元组照常入（见 MAX_GRAM_LEN）。
            if len(run) <= MAX_GRAM_LEN:
                counts[run] = counts.get(run, 0) + 1
            for k in range(len(run) - 1):
                gram = run[k : k + 2]
                counts[gram] = counts.get(gram, 0) + 1
            i = j
        else:
            i += 1
    return counts


def query_tokens(q: str | None) -> list[str]:
    """查询词 → 要查倒排的 token（去重、保持出现顺序）。

    返回空列表表示"这次查询无法走倒排"（例如单字中文、纯标点），调用方应回退 LIKE。
    """
    counts = token_counts(q)
    if not counts:
        return []
    # dict 保持插入序：先出现先召回，截断时优先保留靠前的片段。
    return list(counts.keys())[:MAX_QUERY_GRAMS]


# ---------------------------------------------------------------------------
# 索引构建与维护
# ---------------------------------------------------------------------------


def _card_field_texts(card_id: str) -> dict[str, str]:
    """取一张卡各可检索字段的文本（标签、对话示例从关联表拼）。"""
    card = db.session.get(Card, card_id)
    if card is None:
        return {}
    tags = [t.tag or "" for t in CardTag.query.filter_by(card_id=card_id).all()]
    return {
        "name": card.name or "",
        "intro": card.intro or "",
        "persona": card.persona or "",
        "tag": " ".join(tags),
    }


def reindex_card(card_id: str) -> int:
    """重建一张卡的倒排（先删该卡旧行再写新行），返回写入的 gram 行数。

    只动这张卡自己的索引行；卡片本身与任何业务数据都不碰。
    """
    SearchGram.query.filter_by(doc_type=DOC_CARD, doc_id=card_id).delete(synchronize_session=False)
    stat = db.session.get(SearchDocStat, (DOC_CARD, card_id))
    if stat is None:
        stat = SearchDocStat(doc_type=DOC_CARD, doc_id=card_id)
        db.session.add(stat)

    texts = _card_field_texts(card_id)
    field_lens: dict[str, int] = {}
    rows: list[SearchGram] = []
    total_len = 0
    for field in CARD_FIELDS:
        text = texts.get(field, "")
        field_lens[field] = len(normalize(text))
        total_len += field_lens[field]
        for gram, tf in token_counts(text).items():
            if len(gram) > MAX_GRAM_LEN:  # 防御：任何来源的超长 token 都不入库
                continue
            rows.append(
                SearchGram(
                    doc_type=DOC_CARD,
                    doc_id=card_id,
                    field=field,
                    gram=gram,
                    tf=tf,
                )
            )
    stat.field_lens = field_lens
    stat.total_len = total_len
    # 批量插入：单卡最多几千行，一次 add_all 足够；分批可避免极端长人设撑爆单条语句。
    for i in range(0, len(rows), 1000):
        db.session.add_all(rows[i : i + 1000])
    # 索引内容变了：清掉"可用性 / 平均字段长度"缓存，让检索侧立刻看到新状态。
    _invalidate_meta_cache()
    return len(rows)


def reindex_all(batch: int = 200, only_approved: bool = True) -> tuple[int, int]:
    """全量重建（初次回填 / 修复用）。返回 (卡数, 写入行数)。

    [only_approved] 为真时只索引「已通过」的卡 —— 检索本来就只搜得到它们，
    索引未通过的卡纯属浪费（审核通过时会由事件自动补上）。
    """
    q = Card.query
    if only_approved:
        q = q.filter(Card.status == "approved")
    ids = [cid for (cid,) in q.with_entities(Card.id).all()]
    cards = 0
    rows = 0
    for idx, cid in enumerate(ids, start=1):
        rows += reindex_card(cid)
        cards += 1
        if idx % batch == 0:
            db.session.commit()
    db.session.commit()
    _invalidate_meta_cache()
    return cards, rows


def _invalidate_meta_cache() -> None:
    _AVAILABLE_CACHE.clear()
    _AVG_LEN_CACHE.clear()


# ---------------------------------------------------------------------------
# 写入路径挂钩：卡片内容一变，索引自动跟上
# ---------------------------------------------------------------------------

# 需要纳入索引的可检索字段（改这些字段才需要重建；view_count 之类的噪声不算）。
_WATCHED_ATTRS = ("name", "intro", "persona")

# 单次 flush 最多重建多少张卡（防止批量操作把请求拖慢）
_MAX_REINDEX_PER_FLUSH = 60

_DIRTY_KEY = "search_index_dirty_ids"


def _collect_dirty_cards(session: Session) -> None:
    """flush 之后收集「可检索字段有变化」的卡 id（不在这里做写操作）。"""
    dirty: set[str] = session.info.setdefault(_DIRTY_KEY, set())
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Card) or obj.id is None:
            continue
        if obj in session.dirty:
            insp: InstanceState = inspect(obj)
            if not any(insp.attrs[a].history.has_changes() for a in _WATCHED_ATTRS):
                continue  # view_count 之类的噪声字段，不重建索引
        dirty.add(str(obj.id))
    # 标签/对话示例是关联表：改它们不会把 Card 标脏，这里显式覆盖新增/修改/删除。
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if obj.__class__.__name__ not in ("CardTag", "CardDialogueStyle"):
            continue
        cid = getattr(obj, "card_id", None)
        if cid is not None:
            dirty.add(str(cid))


def _apply_dirty_cards(session: Session) -> None:
    """flush 之后在**同一事务内**重建索引。

    挂载点与写法是实测选出来的（tests/test_search_bigram.py 的自动重建用例守着）：

    * 必须挂 `after_flush_postexec`：此刻追加的行会被同一个 commit 一起提交，
      索引与卡片同事务（不会一半成功一半失败）；
    * **不能**挂 `after_commit`：那里发 SQL 会抛
      `InvalidRequestError: This session is in 'committed' state`；
    * **不能**在这里调 `session.flush()`：会抛 `Session is already flushing`；
    * 整体 try/except：索引失败只记日志，**不 rollback**（那会把业务写入一起回滚），
      也绝不让发布/编辑卡片的请求失败；
    * 表还没建（未跑迁移）时静默跳过，保证「先部署后迁移」也不会炸。
    """
    ids = session.info.pop(_DIRTY_KEY, None)
    if not ids:
        return
    # cache=False：此刻索引可能还没回填，不能把这个"空"结论缓存住。
    if not index_available(force=True, cache=False):
        return
    try:
        for cid in list(ids)[:_MAX_REINDEX_PER_FLUSH]:
            reindex_card(cid)
        _invalidate_meta_cache()
    except Exception:  # noqa: BLE001 - 索引是附属品，任何异常都不许影响业务
        try:
            from flask import current_app

            current_app.logger.exception("重建检索索引失败（已忽略，业务不受影响）")
        except Exception:  # noqa: BLE001
            pass


def register_events() -> None:
    """注册全局事件监听（在 create_app 里调用一次）。

    两段式：**before_flush** 收集「可检索字段变了」的卡（此刻 session.new/dirty
    还是 flush 前的状态），**after_flush_postexec** 在同一事务内重建索引
    （理由见 [_apply_dirty_cards]）。
    """

    @event.listens_for(Session, "before_flush")
    def _on_before_flush(session: Session, _ctx, _instances) -> None:  # noqa: ANN001
        _collect_dirty_cards(session)

    @event.listens_for(Session, "after_flush_postexec")
    def _on_flush_postexec(session: Session, _ctx) -> None:  # noqa: ANN001
        _apply_dirty_cards(session)


# ---------------------------------------------------------------------------
# 查询：倒排召回 + BM25F 精排
# ---------------------------------------------------------------------------


def index_available(force: bool = False, cache: bool = True) -> bool:
    """倒排索引是否可用（表存在且有数据）。

    返回 False 时调用方回退 LIKE —— 这是「先部署后迁移」「索引尚未回填」等情况的
    安全网，保证搜索永远不会因为索引问题而 500 或空结果。

    [force] 为真时绕过缓存重新探测；[cache] 为假时不写缓存 —— 写入路径（事件里
    那次探测）必须用 `cache=False`：否则"此刻索引还空着"这个结论会被缓存 60s，
    把紧接着刚写好的索引也判成不可用。
    """
    if not force:
        hit = _AVAILABLE_CACHE.get("v")
        if hit is not None:
            return hit
    ok = False
    try:
        ok = bool(
            db.session.query(func.count())
            .select_from(SearchDocStat)
            .filter(SearchDocStat.doc_type == DOC_CARD)
            .scalar()
        )
    except Exception:  # noqa: BLE001 - 表不存在/无权限：当作不可用
        ok = False
    if cache:
        _AVAILABLE_CACHE.set("v", ok)
    return ok


def _avg_field_lens() -> tuple[dict[str, float], int]:
    """各字段平均长度与文档总数（BM25 的长度归一化基准），带缓存。"""
    hit = _AVG_LEN_CACHE.get("v")
    if hit is not None:
        return hit
    rows = (
        db.session.query(SearchDocStat.field_lens).filter(SearchDocStat.doc_type == DOC_CARD).all()
    )
    totals: dict[str, float] = dict.fromkeys(CARD_FIELDS, 0.0)
    for (lens,) in rows:
        if not isinstance(lens, dict):
            continue
        for f in CARD_FIELDS:
            totals[f] += float(lens.get(f) or 0)
    n = len(rows)
    avgs = {f: (totals[f] / n if n else 1.0) for f in CARD_FIELDS}
    result = (avgs, n)
    _AVG_LEN_CACHE.set("v", result)
    return result


def rank_card_ids(q: str, limit: int = MAX_CANDIDATES) -> list[str]:
    """按 BM25F 相关度返回卡 id 列表（高相关在前）。

    调用前应先用 [index_available] / [query_tokens] 判断是否走这条路：
    索引不可用或查询切不出 token 时返回空列表，调用方回退 LIKE。
    """
    tokens = query_tokens(q)
    if not tokens or not index_available():
        return []

    postings = (
        db.session.query(
            SearchGram.doc_id,
            SearchGram.field,
            SearchGram.gram,
            SearchGram.tf,
        )
        .filter(SearchGram.doc_type == DOC_CARD, SearchGram.gram.in_(tokens))
        .limit(MAX_POSTING_ROWS)
        .all()
    )
    if not postings:
        return []

    # doc -> field -> gram -> tf；同时统计每个 token 的文档频率 df（跨字段去重）。
    per_doc: dict[str, dict[str, dict[str, int]]] = {}
    doc_grams: dict[str, set[str]] = {}
    df: dict[str, set[str]] = {t: set() for t in tokens}
    for doc_id, field, gram, tf in postings:
        per_doc.setdefault(doc_id, {}).setdefault(field, {})[gram] = int(tf or 1)
        doc_grams.setdefault(doc_id, set()).add(gram)
        df.setdefault(gram, set()).add(doc_id)

    # 召回：包含**全部** query token 的文档（AND 语义）。
    candidates = [doc_id for doc_id, grams in doc_grams.items() if all(t in grams for t in tokens)]
    if not candidates:
        return []

    avgs, n_docs = _avg_field_lens()
    # N 至少取到最大文档频率，避免 df>N 时 IDF 变负（索引与统计短暂不一致时兜底）。
    n_docs = max(n_docs, max((len(v) for v in df.values()), default=0), 1)
    idf = {
        t: math.log(1.0 + (n_docs - len(df.get(t, ())) + 0.5) / (len(df.get(t, ())) + 0.5))
        for t in tokens
    }

    stats = {
        doc_id: (lens if isinstance(lens, dict) else {})
        for doc_id, lens in db.session.query(SearchDocStat.doc_id, SearchDocStat.field_lens)
        .filter(
            SearchDocStat.doc_type == DOC_CARD,
            SearchDocStat.doc_id.in_(candidates[:MAX_CANDIDATES]),
        )
        .all()
    }

    scored: list[tuple[str, float]] = []
    for doc_id in candidates[:MAX_CANDIDATES]:
        fields = per_doc[doc_id]
        lens = stats.get(doc_id, {})
        score = 0.0
        for t in tokens:
            # BM25F：先按字段长度归一化 tf、按字段权重合成虚拟 tf，再套 BM25 饱和函数。
            virtual_tf = 0.0
            for field, weight in FIELD_WEIGHTS.items():
                tf = fields.get(field, {}).get(t)
                if not tf:
                    continue
                b = FIELD_B.get(field, 0.75)
                flen = float(lens.get(field) or 0.0)
                avg = avgs.get(field) or 1.0
                norm = 1.0 - b + b * (flen / avg if avg else 1.0)
                virtual_tf += weight * (tf / norm if norm > 0 else tf)
            if virtual_tf <= 0:
                continue
            score += idf.get(t, 0.0) * (virtual_tf * (BM25_K1 + 1.0)) / (BM25_K1 + virtual_tf)
        if score > 0:
            scored.append((doc_id, score))

    scored.sort(key=lambda x: (-x[1], x[0]))
    return [doc_id for doc_id, _ in scored[:limit]]
