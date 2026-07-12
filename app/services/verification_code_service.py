import datetime
import secrets

from ..extensions import db
from ..models import VerificationCode

CODE_TTL_MINUTES = 10
RESEND_INTERVAL_SECONDS = 60


def create_code(email: str, purpose: str = "register") -> str:
    """生成、存储并返回一个新的验证码（覆盖同一邮箱旧码）。"""
    code = f"{secrets.randbelow(1_000_000):06d}"
    VerificationCode.query.filter_by(email=email, purpose=purpose).delete()
    record = VerificationCode(
        email=email,
        code=code,
        purpose=purpose,
        expires_at=datetime.datetime.now()
        + datetime.timedelta(minutes=CODE_TTL_MINUTES),
    )
    db.session.add(record)
    db.session.commit()
    return code


def can_resend(email: str, purpose: str = "register") -> bool:
    """距上次发送是否已超过重发间隔。"""
    latest = (
        VerificationCode.query.filter_by(email=email, purpose=purpose)
        .order_by(VerificationCode.created_at.desc())
        .first()
    )
    if latest is None:
        return True
    delta = datetime.datetime.now() - latest.created_at
    return delta.total_seconds() >= RESEND_INTERVAL_SECONDS


def verify_code(email: str, code: str, purpose: str = "register") -> bool:
    """校验验证码：存在、未过期、匹配。验证成功后销毁该码。"""
    record = (
        VerificationCode.query.filter_by(email=email, purpose=purpose)
        .order_by(VerificationCode.created_at.desc())
        .first()
    )
    if record is None:
        return False
    if record.expires_at < datetime.datetime.now():
        db.session.delete(record)
        db.session.commit()
        return False
    if record.code != code:
        return False
    db.session.delete(record)
    db.session.commit()
    return True
