"""个人资料编辑：Web 与 App 共用的核心逻辑。

两端口径一致：禁改检查、昵称/简介/位置/网站(birthday 校验)、notify_like 偏好、
头像移除 / 上传压缩（crop_square_and_compress_bytes）。
"""
import re
from datetime import datetime

from ..extensions import db
from ..services.image_service import crop_square_and_compress_bytes


def update_profile(
    viewer,
    *,
    nickname,
    bio,
    location,
    website,
    birthday_raw,
    notify_like,
    avatar_bytes=None,
    remove_avatar=False,
):
    """更新 viewer 的个人资料。返回 error（非空表示校验/权限失败）。

    - avatar_bytes: 原始图片字节；为 None 且 remove_avatar 为 False 时保持原头像不变。
    - birthday_raw: "YYYY-MM-DD" 或空串（清空）。
    """
    if viewer.is_edit_profile_banned:
        return "你当前被禁止更改资料"

    viewer.nickname = (nickname or "").strip() or viewer.nickname
    viewer.bio = (bio or "").strip()
    viewer.location = (location or "").strip()

    website = (website or "").strip()
    if website:
        if not re.match(r"^https?://", website):
            website = "https://" + website
        viewer.website = website
    else:
        viewer.website = None

    birthday_raw = (birthday_raw or "").strip()
    if birthday_raw:
        try:
            viewer.birthday = datetime.strptime(birthday_raw, "%Y-%m-%d").date()
        except ValueError:
            return "生日格式不正确，应为 YYYY-MM-DD"
    else:
        viewer.birthday = None

    viewer.notify_like = bool(notify_like)

    if remove_avatar:
        viewer.avatar = None
    elif avatar_bytes:
        try:
            viewer.avatar = crop_square_and_compress_bytes(avatar_bytes)
        except Exception:
            return "头像处理失败，请重试"

    db.session.commit()
    return None
