"""后台「审核员」身份（users.role = reviewer）。

需求（用户原话）：「加一个审核的用户身份，这个用户身份就只能审核，只能查看已通过和
pending 的内容，其他的都没有任何权限」。

落地的权限边界（已与用户确认）：
* **只在后台受限**：审核员能进后台的三个审核台（角色卡 / 评论 / 茶馆），
  后台其它任何页面一律 403；在 App/社区里仍是普通用户（能发帖、评论、点赞）。
* **审核范围**：角色卡 + 评论 + 茶馆帖三处，且只能做通过/驳回/批量
  （这三个审核台的批量接口只支持 approve/reject，没有删除/隐藏等管理动作）。

本文件同时守住"不能被顺手放开"的边界：删除用户、改角色、删卡、改隐匿标签这些
超管动作，审核员必须仍然 403。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card, Comment, TeaPost, User
from app.models.user import ROLES
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
    # 与 test_admin_quick_action.py 同款的兜底断言：绝不能连到真库
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"), (
        f"🧨 测试连到了非 SQLite 数据库！{app.config['SQLALCHEMY_DATABASE_URI']}"
    )
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, identifier, password="pass123"):
    return client.post("/auth/login", data={"identifier": identifier, "password": password})


# 三个审核台的路径（审核员应当能进）
REVIEW_PAGES = (
    "/admin/review",
    "/admin/comments/moderation",
    "/admin/teahouse/moderation",
)

# 审核员必须 403 的后台页面（覆盖侧边栏四个分组的代表页）
# 注意：/admin/ 与 /admin/dashboard 是同一个端点（index），它不 403 而是把审核员
# 转送到审核台 —— 见 test_reviewer_lands_on_review_queue_from_admin_root。
FORBIDDEN_PAGES = (
    "/admin/data-dashboard",
    "/admin/reports",
    "/admin/punish/appeals",
    "/admin/tickets",
    "/admin/users",
    "/admin/comments",
    "/admin/teahouse",
    "/admin/notify",
    "/admin/articles",
    "/admin/cards",
    "/admin/recommend",
    "/admin/image-models",
    "/admin/stickers",
    "/admin/sponsors",
    "/admin/keys",
    "/admin/copy-stats",
    "/admin/system",
)


def _seed(app):
    """一个审核员 + 一个超管 + 一个普通用户 + 待审卡/评论/茶馆帖各一条。"""
    with app.app_context():
        reviewer = User(username="rev", nickname="审核员", email="rev@x.com", role="reviewer")
        reviewer.set_password("pass123")
        admin = User(username="boss", nickname="老板", email="boss@x.com", role="super_admin")
        admin.set_password("pass123")
        normal = User(username="norm", nickname="普通人", email="norm@x.com")
        normal.set_password("pass123")
        db.session.add_all([reviewer, admin, normal])
        db.session.commit()

        card = Card(
            id="card-pending-1",
            author_id=normal.id,
            name="待审角色卡",
            persona="人设内容",
            status="pending",
        )
        approved = Card(
            id="card-approved-1",
            author_id=normal.id,
            name="已通过角色卡",
            persona="人设内容",
            status="approved",
        )
        db.session.add_all([card, approved])
        db.session.commit()

        comment = Comment(card_id=card.id, user_id=normal.id, content="待审评论", moderated=False)
        post = TeaPost(user_id=normal.id, content="待审茶馆帖", parent_id=None, moderated=False)
        db.session.add_all([comment, post])
        db.session.commit()
        return {
            "card_id": card.id,
            "approved_id": approved.id,
            "comment_id": comment.id,
            "post_id": post.id,
            "normal_id": normal.id,
        }


# ---------------------------------------------------------------------------
# 权限矩阵
# ---------------------------------------------------------------------------


def test_reviewer_can_open_the_three_review_consoles(app, client):
    _seed(app)
    assert _login(client, "rev").status_code == 302
    for path in REVIEW_PAGES:
        r = client.get(path)
        assert r.status_code == 200, f"审核员应能打开 {path}，实际 {r.status_code}"


def test_reviewer_cannot_open_any_other_admin_page(app, client):
    """除三个审核台外，后台其它页面一律 403。"""
    _seed(app)
    assert _login(client, "rev").status_code == 302
    for path in FORBIDDEN_PAGES:
        r = client.get(path)
        assert r.status_code == 403, f"审核员不该能打开 {path}，实际 {r.status_code}"


def test_reviewer_lands_on_review_queue_from_admin_root(app, client):
    """/admin/ 与 /admin/dashboard 是同一个端点（后台默认入口），审核员应被送到审核台
    而不是 403（直接 403 会像"后台坏了"）；普通用户仍然 403，见上一个用例。"""
    _seed(app)
    assert _login(client, "rev").status_code == 302
    for path in ("/admin/", "/admin/dashboard"):
        r = client.get(path)
        assert r.status_code == 302, path
        assert r.headers["Location"].endswith("/admin/review"), path


def test_normal_user_gets_403_on_review_consoles(app, client):
    _seed(app)
    assert _login(client, "norm").status_code == 302
    for path in REVIEW_PAGES:
        assert client.get(path).status_code == 403, f"普通用户不该能打开 {path}"


def test_anonymous_is_redirected_to_login(app, client):
    _seed(app)
    r = client.get("/admin/review")
    assert r.status_code == 302
    assert "/auth/login" in r.headers["Location"]


def test_super_admin_still_has_full_access(app, client):
    """放开审核员不能影响超管：三个审核台 + 后台页面都应可用。"""
    _seed(app)
    assert _login(client, "boss").status_code == 302
    for path in REVIEW_PAGES:
        assert client.get(path).status_code == 200, path
    for path in ("/admin/dashboard", "/admin/users", "/admin/cards", "/admin/system"):
        assert client.get(path).status_code == 200, path


# ---------------------------------------------------------------------------
# 审核动作：能通过/能驳回（角色卡、评论、茶馆帖）
# ---------------------------------------------------------------------------


def test_reviewer_can_approve_and_reject_cards(app, client):
    ids = _seed(app)
    assert _login(client, "rev").status_code == 302

    r = client.post(f"/admin/review/{ids['card_id']}/approve")
    assert r.status_code in (200, 302)
    with app.app_context():
        assert db.session.get(Card, ids["card_id"]).status == "approved"

    # 再驳回已通过的卡（review_reject 允许）
    r = client.post(f"/admin/review/{ids['approved_id']}/reject", data={"reason": "不合格"})
    assert r.status_code in (200, 302)
    with app.app_context():
        assert db.session.get(Card, ids["approved_id"]).status == "rejected"


def test_reviewer_can_approve_comment_and_teahouse_post(app, client):
    ids = _seed(app)
    assert _login(client, "rev").status_code == 302

    assert client.post(f"/admin/comments/{ids['comment_id']}/approve").status_code in (200, 302)
    with app.app_context():
        assert db.session.get(Comment, ids["comment_id"]).moderated is True

    assert client.post(f"/admin/teahouse/{ids['post_id']}/approve").status_code in (200, 302)
    with app.app_context():
        assert db.session.get(TeaPost, ids["post_id"]).moderated is True


def test_reviewer_can_use_batch_endpoints(app, client):
    """批量接口只支持 approve/reject（没有删除/隐藏），所以对审核员开放是安全的。"""
    ids = _seed(app)
    assert _login(client, "rev").status_code == 302

    r = client.post("/admin/review/batch", json={"action": "approve", "card_ids": [ids["card_id"]]})
    assert r.status_code in (200, 302)
    with app.app_context():
        assert db.session.get(Card, ids["card_id"]).status == "approved"


# ---------------------------------------------------------------------------
# 越权动作必须仍然被挡
# ---------------------------------------------------------------------------


def test_reviewer_cannot_manage_users_or_roles(app, client):
    ids = _seed(app)
    assert _login(client, "rev").status_code == 302

    # 改角色
    r = client.post(
        f"/admin/users/{ids['normal_id']}/edit",
        data={"role": "super_admin", "status": "active"},
    )
    assert r.status_code == 403
    # 删除用户
    assert client.post(f"/admin/users/{ids['normal_id']}/delete").status_code == 403
    # 处罚 / 快捷调整
    assert client.post(f"/admin/users/{ids['normal_id']}/punish").status_code == 403
    assert (
        client.post(
            f"/admin/users/{ids['normal_id']}/quick-action",
            json={"action": "change_status", "status": "admin_del"},
        ).status_code
        == 403
    )

    with app.app_context():
        target = db.session.get(User, ids["normal_id"])
        assert target.role == "user", "审核员绝不能改别人的角色"
        assert target.status == "active", "审核员绝不能改别人的账号状态"


def test_reviewer_cannot_manage_cards_beyond_review(app, client):
    ids = _seed(app)
    assert _login(client, "rev").status_code == 302

    assert client.post(f"/admin/cards/{ids['card_id']}/delete").status_code == 403
    assert (
        client.post(f"/admin/cards/{ids['card_id']}/hidden-tags", data={"tags": "x"}).status_code
        == 403
    )
    assert client.get(f"/admin/cards/{ids['card_id']}/edit").status_code == 403
    with app.app_context():
        assert db.session.get(Card, ids["card_id"]) is not None, "审核员不能删卡"


def test_reviewer_cannot_handle_reports(app, client):
    """举报处置涉及封禁/删内容，不属于"审核"，审核员必须没有权限。"""
    _seed(app)
    assert _login(client, "rev").status_code == 302
    assert client.get("/admin/reports").status_code == 403


# ---------------------------------------------------------------------------
# 侧边栏：审核员看不到无权访问的入口
# ---------------------------------------------------------------------------


def test_reviewer_sidebar_only_shows_review_entries(app, client):
    _seed(app)
    assert _login(client, "rev").status_code == 302
    html = client.get("/admin/review").get_data(as_text=True)

    for label in ("角色卡审核", "评论审核", "茶馆审核"):
        assert label in html, f"审核员侧边栏应包含「{label}」"
    for label in ("用户管理", "系统配置", "举报管理", "处罚申诉", "工单管理", "兑换码管理"):
        assert label not in html, f"审核员侧边栏不该出现「{label}」"


def test_super_admin_sidebar_keeps_everything(app, client):
    """对照用例：同一个模板对超管必须仍然完整（防止我误伤超管）。"""
    _seed(app)
    assert _login(client, "boss").status_code == 302
    html = client.get("/admin/review").get_data(as_text=True)
    for label in ("用户管理", "系统配置", "举报管理", "角色卡审核"):
        assert label in html, f"超管侧边栏应包含「{label}」"


def test_reviewer_sees_admin_entry_on_public_site(app, client):
    """前台导航里的「后台」入口对审核员也要显示 —— 否则他只能手敲 URL 进审核台。

    对照：普通用户看不到这个入口。
    """
    _seed(app)
    assert _login(client, "rev").status_code == 302
    html = client.get("/").get_data(as_text=True)
    assert "/admin/review" in html, "审核员应在前台看到后台入口"

    client.get("/auth/logout")
    assert _login(client, "norm").status_code == 302
    html = client.get("/").get_data(as_text=True)
    assert "/admin/review" not in html, "普通用户不该看到后台入口"


def test_review_detail_hides_super_admin_only_actions(app, client):
    ids = _seed(app)
    assert _login(client, "rev").status_code == 302
    html = client.get(f"/admin/review/{ids['card_id']}").get_data(as_text=True)
    assert "编辑内容" not in html, "审核详情页不该给审核员「编辑内容」入口"
    assert "隐匿标签" not in html, "审核详情页不该给审核员「隐匿标签」入口"
    assert "确认驳回" in html or "通过" in html, "审核员必须能通过/驳回"


# ---------------------------------------------------------------------------
# 角色本身
# ---------------------------------------------------------------------------


def test_role_properties():
    assert User(role="user").can_review is False
    assert User(role="user").is_reviewer is False
    assert User(role="reviewer").is_reviewer is True
    assert User(role="reviewer").can_review is True
    assert User(role="reviewer").is_super_admin is False
    assert User(role="super_admin").can_review is True
    assert ROLES == ("user", "reviewer", "super_admin")


def test_super_admin_can_assign_reviewer_role_via_user_edit(app, client):
    ids = _seed(app)
    assert _login(client, "boss").status_code == 302
    r = client.post(
        f"/admin/users/{ids['normal_id']}/edit",
        data={
            "username": "norm",
            "nickname": "普通人",
            "email": "norm@x.com",
            "role": "reviewer",
            "status": "active",
        },
    )
    assert r.status_code in (200, 302)
    with app.app_context():
        assert db.session.get(User, ids["normal_id"]).role == "reviewer"


def test_unknown_role_falls_back_to_user(app, client):
    """非法角色值不能被写进库（历史行为保持：回落到 user）。"""
    ids = _seed(app)
    assert _login(client, "boss").status_code == 302
    client.post(
        f"/admin/users/{ids['normal_id']}/edit",
        data={
            "username": "norm",
            "nickname": "普通人",
            "email": "norm@x.com",
            "role": "root",  # 非法
            "status": "active",
        },
    )
    with app.app_context():
        assert db.session.get(User, ids["normal_id"]).role == "user"
