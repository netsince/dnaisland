from flask import Blueprint, jsonify

from ..services.site_service import public_config

system_bp = Blueprint("system", __name__)


@system_bp.route("/api/site-config")
def site_config():
    """公开站点配置 JSON（关站时依然可用，供前端展示关站横幅/公告）。

    返回 hero / announcement / shutdown / agreements / email_whitelist 的子集。
    """
    return jsonify(public_config())
