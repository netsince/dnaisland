"""积分精度与格式化的单点定义（Single Source of Truth）。

背景
----
积分原为 2 位小数（DECIMAL(10,2)）。为支持最多 POINT_SCALE 位小数，数据库列统一为
DECIMAL(POINT_PRECISION, POINT_SCALE)，且为避免 JSON 浮点丢精度，对外一律用**字符串**传输。

为什么要字符串而不是 JSON number
--------------------------------
IEEE-754 float64 只有约 15~16 位有效十进制数字。DECIMAL(30,10) 最多可有 20 位整数 + 10 位小数
（30 位有效数字），任何一次 float/double 往返都会不可逆地丢精度。字符串是唯一无损的传输形式。

精度取值依据
------------
产品要求"至少 10 位小数"。MariaDB/MySQL 的 DECIMAL 上限为 precision ≤ 65、scale ≤ 30，
故"无限位"在 DECIMAL 下不存在。取 (30, 10)：20 位整数 + 10 位小数，容量与索引开销均安全。
"""

from decimal import Context, Decimal, InvalidOperation, localcontext

# 数据库列精度：整数位 = POINT_PRECISION - POINT_SCALE = 20 位，小数位 = 10 位。
POINT_PRECISION = 30
POINT_SCALE = 10

# 10^-POINT_SCALE，即 1 个最小单位的绝对值。
POINT_QUANTUM = Decimal(1).scaleb(-POINT_SCALE)

# DECIMAL(30,10) 可表示的绝对值上限 = 10^20 - 10^-10。
# 必须用字符串字面量构造：decimal 默认上下文精度只有 28 位，10^20 - 10^-10 需要 30 位有效数字，
# 走算术运算会被静默舍入成 99999999999999999999.99999999（少两位）。
POINT_MAX_ABS = Decimal(
    "9" * (POINT_PRECISION - POINT_SCALE) + "." + "9" * POINT_SCALE
)


# 积分运算上下文。
# decimal 的默认上下文精度只有 28 位，而 DECIMAL(30,10) 的合法值最多有 30 位有效数字：
# 直接做 + - * 会被静默舍入（本模块的 POINT_MAX_ABS 就曾被这样算错）。decimal 上下文是
# 线程局部的，因此不能用 getcontext().prec 在应用启动时全局设置（WSGI 工作线程不会继承），
# 必须在每个运算点用 localcontext 显式指定精度。
POINT_ARITHMETIC_PRECISION = POINT_PRECISION + 10
_POINT_CONTEXT = Context(prec=POINT_ARITHMETIC_PRECISION)


def _to_decimal(value) -> Decimal:
    """把积分值归一为 Decimal；None 视为 0。"""
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def points_add(a, b) -> Decimal:
    """积分加法：在 POINT_ARITHMETIC_PRECISION 精度下进行，避免默认上下文截断。"""
    with localcontext(_POINT_CONTEXT):
        return _to_decimal(a) + _to_decimal(b)


def points_sub(a, b) -> Decimal:
    """积分减法：在 POINT_ARITHMETIC_PRECISION 精度下进行。"""
    with localcontext(_POINT_CONTEXT):
        return _to_decimal(a) - _to_decimal(b)


def points_mul(a, b) -> Decimal:
    """积分乘法（如 张数 × 每图积分）：在 POINT_ARITHMETIC_PRECISION 精度下进行。"""
    with localcontext(_POINT_CONTEXT):
        return _to_decimal(a) * _to_decimal(b)


def points_to_str(value) -> str:
    """把积分数值规范化为字符串：普通记数法（绝不出指数）、去尾零、整数不带小数点。

    例：Decimal("10.2500000000") -> "10.25"；Decimal("1E+2") -> "100"；
        None / "" -> "0"；Decimal("-0.0000000000") -> "0"。

    非法值直接抛 ValueError，不返回伪造数据（严格模式 S09）。
    """
    if value is None:
        return "0"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "0"
        try:
            d = Decimal(text)
        except InvalidOperation:
            raise ValueError(f"积分必须是数字，收到 {value!r}") from None
    elif isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        # 经 str() 取最短往返表示，避免二进制展开噪声（如 0.1 -> 0.1000000000000000055511151231257827）
        d = Decimal(str(value))
    else:
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            raise ValueError(f"积分必须是数字，收到 {value!r}") from None

    if not d.is_finite():
        raise ValueError(f"积分必须是有限数值，收到 {value!r}")

    text = format(d, "f")  # 'f' 强制普通记数法，1E+2 -> "100"、1E-10 -> "0.0000000001"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        return "0"
    return text


def points_to_signed_str(value) -> str:
    """带符号的规范化字符串：正数显式带 '+'，用于明细页展示变化量。"""
    text = points_to_str(value)
    return text if text.startswith("-") else "+" + text


def _decimal_places(d: Decimal) -> int:
    """有效小数位数（尾零不计入）。

    不用 Decimal.normalize()：它按 decimal 上下文精度（默认 28 位）舍入，
    会把 30 位有效数字的合法值悄悄改掉。这里只读系数与指数，全程不舍入。
    """
    if d == 0:
        return 0
    exponent = d.as_tuple().exponent
    if not isinstance(exponent, int) or exponent >= 0:
        return 0
    places = -exponent
    for digit in reversed(d.as_tuple().digits):
        if digit == 0 and places > 0:
            places -= 1
        else:
            break
    return places


def parse_points_input(raw, *, field: str = "积分数值") -> Decimal:
    """解析用户输入的积分数值，强制 ≤ POINT_SCALE 位小数且不超过列容量。

    超出精度或非法输入 -> 抛 ValueError，绝不静默四舍五入（严格模式 S09/S20）。
    尾零不计入小数位数：Decimal("1.230000000000000") 视为 2 位小数。
    """
    if raw is None:
        raise ValueError(f"{field}不能为空")
    text = str(raw).strip()
    if not text:
        raise ValueError(f"{field}不能为空")
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{field}必须是数字") from None
    if not d.is_finite():
        raise ValueError(f"{field}必须是有限数字")

    if _decimal_places(d) > POINT_SCALE:
        raise ValueError(f"{field}最多支持 {POINT_SCALE} 位小数")
    # 必须用 copy_abs()：内置 abs() 会按 decimal 上下文精度（默认 28 位）舍入，
    # 会把 30 位有效数字的合法边界值舍入成 1E+20 从而误判越界。比较运算本身不舍入。
    if d.copy_abs() > POINT_MAX_ABS:
        raise ValueError(f"{field}超出可表示范围（绝对值上限 {POINT_MAX_ABS}）")
    return d
