"""可达 USP 内网环境：开发者模式 CRUD + 讨论区选项 + AI 内取 SSH 配置。

试验期：配置落在 OpenRobotService_Data/usp_envs.json，不写数据库表。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.integrations.api import verify_sync_api_key
from app.modules.admin.api.auth import require_permission
from app.modules.admin.api.dispatch_dev import PERM, ensure_dispatch_dev_permission
from app.modules.admin.schemas.response import DataResponse

admin_router = APIRouter(prefix="/dispatch-dev/usp-envs", tags=["admin-dispatch-dev-usp-envs"])
public_router = APIRouter(prefix="/usp-envs", tags=["usp-envs"])

# usp_envs.py → api/admin/modules/app/backend/OpenRobotService → sibling OpenRobotService_Data
_REPO_ROOT = Path(__file__).resolve().parents[5]
_STORE_PATH = (_REPO_ROOT.parent / "OpenRobotService_Data" / "usp_envs.json").resolve()
_LOCK = threading.RLock()


class UspEnvCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    code: Optional[str] = Field(None, max_length=64)
    enabled: bool = True
    notes: Optional[str] = None
    project_id: Optional[str] = Field(None, max_length=64, description="预留关联项目")
    ssh_host: str = Field(..., min_length=1, max_length=255)
    ssh_port: int = Field(22, ge=1, le=65535)
    ssh_user: str = Field(..., min_length=1, max_length=128)
    ssh_auth_type: str = Field("password", description="key | password")
    ssh_private_key_path: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_connect_timeout_s: float = Field(8.0, ge=1.0, le=120.0)
    export_script: str = Field(..., min_length=1, max_length=512)
    export_workdir: str = Field(..., min_length=1, max_length=512)
    log_interval_min: int = Field(15, ge=1, le=1440)
    # 选填：SSH 进宿主机后，经 docker exec 在容器内跑脚本
    docker_container: Optional[str] = Field(None, max_length=128, description="Docker 容器名，如 usp_app")
    docker_sudo: bool = Field(False, description="宿主机执行 docker 是否加 sudo（需 NOPASSWD）")
    capabilities: Optional[List[str]] = None


class UspEnvUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    code: Optional[str] = Field(None, max_length=64)
    enabled: Optional[bool] = None
    notes: Optional[str] = None
    project_id: Optional[str] = Field(None, max_length=64)
    ssh_host: Optional[str] = Field(None, min_length=1, max_length=255)
    ssh_port: Optional[int] = Field(None, ge=1, le=65535)
    ssh_user: Optional[str] = Field(None, min_length=1, max_length=128)
    ssh_auth_type: Optional[str] = None
    ssh_private_key_path: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_connect_timeout_s: Optional[float] = Field(None, ge=1.0, le=120.0)
    export_script: Optional[str] = Field(None, min_length=1, max_length=512)
    export_workdir: Optional[str] = Field(None, min_length=1, max_length=512)
    log_interval_min: Optional[int] = Field(None, ge=1, le=1440)
    docker_container: Optional[str] = Field(None, max_length=128)
    docker_sudo: Optional[bool] = None
    capabilities: Optional[List[str]] = None
    clear_password: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_auth(v: Optional[str]) -> str:
    t = (v or "password").strip().lower()
    if t not in ("key", "password"):
        raise HTTPException(status_code=422, detail="ssh_auth_type 须为 key 或 password")
    return t


def _default_caps(caps: Optional[List[str]]) -> List[str]:
    if caps is None:
        return ["ssh_export_logs"]
    out = [str(c).strip() for c in caps if str(c).strip()]
    return out or ["ssh_export_logs"]


def _empty_store() -> Dict[str, Any]:
    return {"next_id": 1, "items": []}


def _read_store() -> Dict[str, Any]:
    if not _STORE_PATH.is_file():
        return _empty_store()
    try:
        raw = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取 usp_envs.json 失败: {e}") from e
    if not isinstance(raw, dict):
        return _empty_store()
    items = raw.get("items")
    if not isinstance(items, list):
        items = []
    next_id = raw.get("next_id")
    try:
        next_id = int(next_id)
    except (TypeError, ValueError):
        next_id = 1
    if next_id < 1:
        next_id = 1
    return {"next_id": next_id, "items": [x for x in items if isinstance(x, dict)]}


def _write_store(store: Dict[str, Any]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE_PATH.with_suffix(".json.tmp")
    payload = {
        "next_id": int(store.get("next_id") or 1),
        "items": list(store.get("items") or []),
        "updated_at": _now_iso(),
    }
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_STORE_PATH)


def _find(store: Dict[str, Any], env_id: int) -> Optional[Dict[str, Any]]:
    for row in store.get("items") or []:
        if int(row.get("id") or 0) == int(env_id):
            return row
    return None


def _public_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "",
        "code": row.get("code"),
        "enabled": bool(row.get("enabled", True)),
        "project_id": row.get("project_id"),
        "capabilities": list(row.get("capabilities") or []),
        "notes": row.get("notes"),
    }


def _admin_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    d = _public_dict(row)
    d.update({
        "ssh_host": row.get("ssh_host") or "",
        "ssh_port": int(row.get("ssh_port") or 22),
        "ssh_user": row.get("ssh_user") or "",
        "ssh_auth_type": row.get("ssh_auth_type") or "password",
        "ssh_private_key_path": row.get("ssh_private_key_path"),
        "ssh_password_set": bool(row.get("ssh_password")),
        "ssh_connect_timeout_s": float(row.get("ssh_connect_timeout_s") or 8.0),
        "export_script": row.get("export_script") or "",
        "export_workdir": row.get("export_workdir") or "",
        "log_interval_min": int(row.get("log_interval_min") or 15),
        "docker_container": row.get("docker_container") or "",
        "docker_sudo": bool(row.get("docker_sudo", False)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    })
    return d


def _ssh_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "",
        "enabled": bool(row.get("enabled", True)),
        "capabilities": list(row.get("capabilities") or []),
        "ssh_host": row.get("ssh_host") or "",
        "ssh_port": int(row.get("ssh_port") or 22),
        "ssh_user": row.get("ssh_user") or "",
        "ssh_auth_type": row.get("ssh_auth_type") or "password",
        "ssh_private_key_path": row.get("ssh_private_key_path") or "",
        "ssh_password": row.get("ssh_password") or "",
        "ssh_connect_timeout_s": float(row.get("ssh_connect_timeout_s") or 8.0),
        "export_script": row.get("export_script") or "",
        "export_workdir": row.get("export_workdir") or "",
        "log_interval_min": int(row.get("log_interval_min") or 15),
        "docker_container": (row.get("docker_container") or "").strip(),
        "docker_sudo": bool(row.get("docker_sudo", False)),
    }


def _validate_ssh_ready(auth: str, key_path: Optional[str], password: Optional[str], *, require_secret: bool) -> None:
    if auth == "key":
        if require_secret and not (key_path or "").strip():
            raise HTTPException(status_code=422, detail="key 认证需要 ssh_private_key_path")
    else:
        if require_secret and not (password or "").strip():
            raise HTTPException(status_code=422, detail="password 认证需要 ssh_password")


# ── 开发者模式 CRUD ──

@admin_router.get("", response_model=DataResponse, summary="USP 环境列表")
async def list_envs(
    enabled: Optional[bool] = None,
    project_id: Optional[str] = None,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    with _LOCK:
        store = _read_store()
        rows = list(store.get("items") or [])
    if enabled is not None:
        rows = [r for r in rows if bool(r.get("enabled", True)) is bool(enabled)]
    if project_id:
        pid = project_id.strip()
        rows = [r for r in rows if (r.get("project_id") or "") == pid]
    rows.sort(key=lambda r: int(r.get("id") or 0), reverse=True)
    return DataResponse(code=0, message="success", data=[_admin_dict(r) for r in rows[:200]])


@admin_router.post("", response_model=DataResponse, summary="新建 USP 环境")
async def create_env(
    body: UspEnvCreate,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    auth = _norm_auth(body.ssh_auth_type)
    _validate_ssh_ready(auth, body.ssh_private_key_path, body.ssh_password, require_secret=True)
    code = (body.code or "").strip() or None
    now = _now_iso()
    with _LOCK:
        store = _read_store()
        items = list(store.get("items") or [])
        if code and any((r.get("code") or "") == code for r in items):
            raise HTTPException(status_code=409, detail=f"code 已存在: {code}")
        env_id = int(store.get("next_id") or 1)
        row = {
            "id": env_id,
            "name": body.name.strip(),
            "code": code,
            "enabled": bool(body.enabled),
            "notes": (body.notes or "").strip() or None,
            "project_id": (body.project_id or "").strip() or None,
            "ssh_host": body.ssh_host.strip(),
            "ssh_port": int(body.ssh_port),
            "ssh_user": body.ssh_user.strip(),
            "ssh_auth_type": auth,
            "ssh_private_key_path": (body.ssh_private_key_path or "").strip() or None,
            "ssh_password": (body.ssh_password or "").strip() or None,
            "ssh_connect_timeout_s": float(body.ssh_connect_timeout_s),
            "export_script": body.export_script.strip(),
            "export_workdir": body.export_workdir.strip(),
            "log_interval_min": int(body.log_interval_min),
            "docker_container": (body.docker_container or "").strip() or None,
            "docker_sudo": bool(body.docker_sudo),
            "capabilities": _default_caps(body.capabilities),
            "created_at": now,
            "updated_at": now,
        }
        items.append(row)
        store["items"] = items
        store["next_id"] = env_id + 1
        _write_store(store)
    return DataResponse(code=0, message="success", data=_admin_dict(row))


@admin_router.get("/{env_id}", response_model=DataResponse, summary="USP 环境详情")
async def get_env(
    env_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    with _LOCK:
        row = _find(_read_store(), env_id)
    if not row:
        raise HTTPException(status_code=404, detail="环境不存在")
    return DataResponse(code=0, message="success", data=_admin_dict(row))


@admin_router.put("/{env_id}", response_model=DataResponse, summary="更新 USP 环境")
async def update_env(
    env_id: int,
    body: UspEnvUpdate,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    data = body.model_dump(exclude_unset=True)
    clear_password = bool(data.pop("clear_password", False))
    if "ssh_auth_type" in data and data["ssh_auth_type"] is not None:
        data["ssh_auth_type"] = _norm_auth(data["ssh_auth_type"])
    with _LOCK:
        store = _read_store()
        row = _find(store, env_id)
        if not row:
            raise HTTPException(status_code=404, detail="环境不存在")
        if "code" in data:
            code = (data["code"] or "").strip() or None
            if code and any(
                int(r.get("id") or 0) != env_id and (r.get("code") or "") == code
                for r in (store.get("items") or [])
            ):
                raise HTTPException(status_code=409, detail=f"code 已存在: {code}")
            data["code"] = code
        for k in ("name", "notes", "project_id", "ssh_host", "ssh_user",
                  "ssh_private_key_path", "export_script", "export_workdir", "docker_container"):
            if k in data and isinstance(data[k], str):
                if k in ("notes", "project_id", "ssh_private_key_path", "docker_container"):
                    data[k] = data[k].strip() or None
                else:
                    data[k] = data[k].strip()
        if "capabilities" in data:
            data["capabilities"] = _default_caps(data["capabilities"])
        if clear_password:
            data["ssh_password"] = None
        elif "ssh_password" in data:
            pwd = data.get("ssh_password")
            if pwd is None or str(pwd).strip() == "":
                data.pop("ssh_password", None)
            else:
                data["ssh_password"] = str(pwd).strip()
        row.update(data)
        row["updated_at"] = _now_iso()
        auth = row.get("ssh_auth_type") or "password"
        if auth == "key" and not (row.get("ssh_private_key_path") or "").strip():
            raise HTTPException(status_code=422, detail="key 认证需要 ssh_private_key_path")
        if auth == "password" and not (row.get("ssh_password") or "").strip():
            raise HTTPException(status_code=422, detail="password 认证需要 ssh_password")
        _write_store(store)
        out = _admin_dict(row)
    return DataResponse(code=0, message="success", data=out)


@admin_router.delete("/{env_id}", response_model=DataResponse, summary="删除 USP 环境")
async def delete_env(
    env_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    with _LOCK:
        store = _read_store()
        items = list(store.get("items") or [])
        new_items = [r for r in items if int(r.get("id") or 0) != int(env_id)]
        if len(new_items) == len(items):
            raise HTTPException(status_code=404, detail="环境不存在")
        store["items"] = new_items
        _write_store(store)
    return DataResponse(code=0, message="success", data={"id": env_id})


@admin_router.post("/{env_id}/test-ssh", response_model=DataResponse, summary="测试 SSH 连通")
async def test_ssh(
    env_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """开发者模式：试连 SSH 并执行 echo，不跑 export_logs。"""
    ensure_dispatch_dev_permission()
    _ = current_user
    with _LOCK:
        row = _find(_read_store(), env_id)
    if not row:
        raise HTTPException(status_code=404, detail="环境不存在")
    cfg = _ssh_dict(row)

    try:
        import paramiko
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"后端未安装 paramiko: {e}") from e

    host = cfg["ssh_host"]
    port = int(cfg["ssh_port"] or 22)
    user = cfg["ssh_user"]
    timeout = float(cfg.get("ssh_connect_timeout_s") or 8.0)
    auth = (cfg.get("ssh_auth_type") or "password").strip().lower()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if auth == "key":
            key_path = (cfg.get("ssh_private_key_path") or "").strip()
            if not key_path:
                raise HTTPException(status_code=422, detail="未配置私钥路径")
            kwargs["key_filename"] = key_path
        else:
            password = cfg.get("ssh_password") or ""
            if not password:
                raise HTTPException(status_code=422, detail="未配置密码")
            kwargs["password"] = password
        client.connect(**kwargs)
        _stdin, stdout, stderr = client.exec_command("echo ors_usp_ok", timeout=15)
        out = (stdout.read() or b"").decode("utf-8", errors="replace").strip()
        err = (stderr.read() or b"").decode("utf-8", errors="replace").strip()
        code = stdout.channel.recv_exit_status()
        ok = code == 0 and "ors_usp_ok" in out
        return DataResponse(
            code=0,
            message="success",
            data={
                "ok": ok,
                "host": host,
                "port": port,
                "user": user,
                "exit_code": code,
                "stdout": out[:200],
                "stderr": err[:200],
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        return DataResponse(
            code=0,
            message="success",
            data={
                "ok": False,
                "host": host,
                "port": port,
                "user": user,
                "error": f"{type(e).__name__}: {e}",
            },
        )
    finally:
        try:
            client.close()
        except Exception:
            pass


# ── 讨论区选项（仅开发者模式权限，试验期）──

@public_router.get("/options", response_model=DataResponse, summary="讨论区可选 USP 环境")
async def list_options(
    project_id: Optional[str] = None,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """只返回已启用环境的公开字段；与开发者模式同一权限（试验期）。"""
    ensure_dispatch_dev_permission()
    _ = current_user
    with _LOCK:
        rows = list(_read_store().get("items") or [])
    rows = [r for r in rows if bool(r.get("enabled", True))]
    if project_id and project_id.strip():
        pid = project_id.strip()
        rows = [r for r in rows if not r.get("project_id") or r.get("project_id") == pid]
    rows.sort(key=lambda r: (r.get("name") or "").lower())
    out = []
    for r in rows[:200]:
        caps = list(r.get("capabilities") or [])
        if "ssh_export_logs" not in caps:
            continue
        out.append(_public_dict(r))
    return DataResponse(code=0, message="success", data=out)


# ── AI 内取 SSH 配置（X-API-Key）──

@public_router.get("/{env_id}/ssh-config", response_model=DataResponse, summary="AI 取 SSH 配置")
async def get_ssh_config(
    env_id: int,
    _: str = Depends(verify_sync_api_key),
):
    with _LOCK:
        row = _find(_read_store(), env_id)
    if not row:
        raise HTTPException(status_code=404, detail="环境不存在")
    if not bool(row.get("enabled", True)):
        raise HTTPException(status_code=409, detail="环境已停用")
    caps = list(row.get("capabilities") or [])
    if "ssh_export_logs" not in caps:
        raise HTTPException(status_code=409, detail="未开通 ssh_export_logs")
    return DataResponse(code=0, message="success", data=_ssh_dict(row))
