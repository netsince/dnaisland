"""角色卡「隐匿标签」：仅超级管理员可见/可设置，可驱动降权等运营动作。

设计要点
--------
- 标签是**固定枚举**：key 稳定不变（存进 JSON），label/说明用于后台展示。
  自由文本无法可靠驱动行为——拼错一个字，效果就静默失效。
- 存储：`cards.hidden_tags` 是 JSON 数组；普通用户与作者的任何接口都不会返回它。
- `cards.boost_factor` 是由标签**派生**的 SQL 可见系数（JSON 无法在 SQL 里高效判断），
  热度分直接乘它。本模块的 [set_hidden_tags] 是这两个字段的**唯一写入方**，保证一致。
"""

REDUCE_BOOST = "reduce_boost"

# 隐匿标签注册表。key 稳定不变（入库）；boost 为热度分乘数（1.0 = 不降权）。
HIDDEN_TAGS: dict[str, dict] = {
    REDUCE_BOOST: {
        "label": "减少推流",
        "desc": "擦边但未违规：降低推荐权重，不封禁、不隐藏。",
        "boost": 0.2,
    },
}


def normalize_hidden_tags(raw) -> list[str]:
    """把任意输入规范为「已知标签 key 的有序去重列表」。

    未知 key 一律丢弃（避免拼错/脏数据静默留在库里），顺序跟随注册表，保证稳定。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return []
    wanted = {str(x).strip() for x in raw}
    return [key for key in HIDDEN_TAGS if key in wanted]


def boost_factor_for(keys) -> float:
    """由标签计算热度分乘数。多个降权标签取最小值（最强制约生效）。"""
    factors = [HIDDEN_TAGS[key]["boost"] for key in normalize_hidden_tags(keys)]
    return min(factors) if factors else 1.0


def card_has_hidden_tag(key: str):
    """SQL 条件：卡片带有指定隐匿标签（供管理后台按标签筛选）。

    hidden_tags 是 JSON 列（MariaDB 落为 LONGTEXT、SQLite 落为 TEXT），
    用「带引号的 key」做子串匹配即可精确命中——`"reduce_boost"` 不会误配
    `"reduce_boost_extra"`。CAST 到文本两端 SQLAlchemy 会分别渲染成
    `CHAR`(MySQL) / `TEXT`(SQLite)，因此是跨引擎可移植的。
    """
    from sqlalchemy import Text, cast

    from ..models import Card

    return cast(Card.hidden_tags, Text).like(f'%"{key}"%')


def hidden_tags_of(card) -> list[str]:
    """读取卡片上的隐匿标签（NULL / 脏数据都安全返回列表）。"""
    return normalize_hidden_tags(getattr(card, "hidden_tags", None))


def set_hidden_tags(card, raw) -> list[str]:
    """设置隐匿标签并同步派生系数，返回规范化后的 key 列表。

    这是 hidden_tags 与 boost_factor 的**唯一写入方**，两者因此不会不一致。
    """
    keys = normalize_hidden_tags(raw)
    card.hidden_tags = keys
    card.boost_factor = boost_factor_for(keys)
    return keys


def hidden_tag_views(card) -> list[dict]:
    """后台展示用：[{key, label, desc}]。"""
    return [
        {"key": key, "label": HIDDEN_TAGS[key]["label"], "desc": HIDDEN_TAGS[key]["desc"]}
        for key in hidden_tags_of(card)
    ]
