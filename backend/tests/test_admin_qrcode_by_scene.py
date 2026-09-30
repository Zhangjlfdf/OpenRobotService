"""按场景值查二维码接口（摇人页「扫码进入」链路）的口径。

被测：`GET /api/admin/qrcodes/by-scene/{scene}`（见 app/modules/admin/api/qrcode.py）。

口径：
  - 鉴权「登录即可」——用 app.core.auth_routes.get_current_active_user_from_token，
    而不是同文件其它接口的 require_permission("frontend:admin:other:show")：
    摇人页是 C 端，普通客服没有后台权限。注意 admin/api/auth.py 里另有一个同名函数
    （admin 侧自己的实现），两者不是同一个可调用对象，所以这里 override 的是 core 那个。
  - 命中 → 200，且录入信息行自带的 项目名/客户名/车型 原样回吐；
    录入信息行的 project_id 是业务键（后续企微同步），未必命中 project 表，
    此时 project_name 必须回退到行自带的那个，不能变 null。
  - 未命中 → 404；scene 为空或超 64 字符 → 400（长度口径对齐 scene_str 列宽）。
  - 双段路径不与单段 GET /{qid} 冲突（同 /stats/summary 先例）。

一律不连真库：conftest 已把 app.core.database 换成 MagicMock，这里只替换
qrcode 模块自己的 db_manager，返回一个可编排的假 Session。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth_routes import get_current_active_user_from_token
from app.models.wechat_qrcode import QrcodeStatus
from app.modules.admin.api import qrcode as qrcode_api
from app.modules.admin.api.qrcode import router as qrcode_router

# 普通登录用户：没有任何后台权限，用于钉住「登录即可」这个口径
LOGIN_USER = {"id": "u-1", "username": "bob", "name": "Bob", "permissions": [], "roles": {}}

SCENE = "proj_abc123def456"


def _row(**over):
    """一条录入信息行的完整属性集（覆盖 _to_dict 会读到的每个字段）。"""
    fields = {
        "id": 7,
        "scene_str": SCENE,
        "name": "项目A",
        "description": None,
        "ticket": None,
        "url": None,
        "qrcode_image_url": None,
        "type": "permanent",
        "expire_seconds": None,
        "status": QrcodeStatus.PUBLISHED,
        "batch_id": None,
        "project_id": None,
        "project_name": "项目A",
        "project_code": "P-0001",
        "project_location": "上海",
        "customer_name": "客户A",
        "vehicle_model": "XQE",
        "redirect_url": None,
        "created_by": None,
        "published_by": None,
        "deprecated_by": None,
        "ticket_created_at": None,
        "created_at": None,
        "updated_at": None,
    }
    fields.update(over)
    return SimpleNamespace(**fields)


@pytest.fixture
def db(monkeypatch):
    """假 Session：默认「查无此码」，用例按需覆盖 first() 的返回。"""
    fake = MagicMock()
    fake.query.return_value.filter.return_value.first.return_value = None
    fake.query.return_value.filter.return_value.all.return_value = []
    monkeypatch.setattr(qrcode_api, "db_manager", SimpleNamespace(get_db=lambda: fake))
    return fake


@pytest.fixture
def app(db):
    application = FastAPI()
    application.include_router(qrcode_router, prefix="/api/admin")
    return application


@pytest.fixture
def client(app):
    """已登录（覆盖 core 的登录依赖）。"""
    app.dependency_overrides[get_current_active_user_from_token] = lambda: dict(LOGIN_USER)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def anon_client(app):
    """未登录：不覆盖依赖，走真实实现（无 token 时它在读库之前就抛 401）。"""
    with TestClient(app) as test_client:
        yield test_client


def test_published_row_returns_project_customer_model(client, db):
    """命中即 200：弹窗要的三个字段一次拿全，状态原样带出。"""
    db.query.return_value.filter.return_value.first.return_value = _row()

    response = client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert response.status_code == 200
    body = response.json()
    assert body["scene_str"] == SCENE
    assert body["status"] == QrcodeStatus.PUBLISHED
    assert body["project_name"] == "项目A"
    assert body["customer_name"] == "客户A"
    assert body["vehicle_model"] == "XQE"
    assert body["project_code"] == "P-0001"
    # 录入信息行的 project_id 为空 → 不该再去联查 project 表
    assert db.query.call_count == 1


def test_project_name_survives_when_project_id_is_business_key(client, db):
    """录入信息行的 project_id 是业务键，命中不了 project 表时仍要用行自带的项目名。"""
    db.query.return_value.filter.return_value.first.return_value = _row(project_id="BIZ-9527")
    db.query.return_value.filter.return_value.all.return_value = []  # 联查 project 表查不到

    response = client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert response.status_code == 200
    assert response.json()["project_name"] == "项目A"


def test_unknown_scene_returns_404(client, db):
    """查无此码 → 404（前端据此静默降级，不打扰用户）。"""
    response = client.get("/api/admin/qrcodes/by-scene/proj_notexist")

    assert response.status_code == 404
    assert response.json()["detail"] == "二维码不存在"


def test_scene_too_long_rejected_before_touching_db(client, db):
    """超长场景值直接 400，且不进库（长度口径对齐 scene_str 列宽）。"""
    response = client.get("/api/admin/qrcodes/by-scene/" + "a" * 65)

    assert response.status_code == 400
    assert response.json()["detail"] == "scene_str 长度 1~64 字符"
    db.query.assert_not_called()


def test_requires_login(anon_client):
    """未登录被拦，不因为它是「摇人页用的接口」就免鉴权。"""
    response = anon_client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert response.status_code == 401


def test_not_gated_by_admin_permission(client, db):
    """普通登录用户（无任何后台权限）也要能查到——这正是与同文件其它接口的区别。"""
    db.query.return_value.filter.return_value.first.return_value = _row(status=QrcodeStatus.INIT)

    response = client.get(f"/api/admin/qrcodes/by-scene/{SCENE}")

    assert response.status_code == 200
    assert response.json()["status"] == QrcodeStatus.INIT
