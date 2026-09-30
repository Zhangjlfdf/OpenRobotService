"""二维码管理路由 —— 带参数二维码的 CRUD、批量创建、状态流转。

生命周期：init → entering → confirming → published → deprecated
- init: 场景值已定义，尚未调微信接口
- entering: 已调微信创建 ticket
- confirming: 已核对 ticket/图片正确
- published: 对外使用中
- deprecated: 停止使用

项目关联：project_id 指向 project.id（选填）。一个项目可有多张码（多台车/重印），
引用放在码这侧；非项目码为 NULL。创建/更新时校验项目存在且未软删。

录入信息（「新建项目 → 录入信息」页）：项目id/项目编号/项目名/项目地点/客户名/车型
六字段一条信息落成本表一行（和行 id 同行存），见下方 project-info 两个接口。
该组字段里的 project_id 是业务键（后续企微表格同步），不做 project 表存在性校验；
项目id/项目编号的唯一性只在「录入信息行」（project_code 非空）范围内查重。

权限依赖写法：current_user=require_permission(...)——require_permission 本身已经
返回 Depends(permission_dependency)，不能再套一层 Depends(...)，否则 FastAPI 0.14x
在注册路由时会抛 "Depends(...) is not a callable object"，应用启动即失败。

永久码上限 10 万，批量生成有速率限制。
"""
import time
import uuid
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import db_manager
from app.models.delivery import Project, PROJECT_DELETED
from app.models.wechat_qrcode import WechatQrcode, QrcodeStatus, QrcodeType
from app.modules.admin.api.auth import require_permission
from app.wechat.services.wechat_service import wechat_service as get_wechat_service

router = APIRouter(prefix="/qrcodes", tags=["admin-qrcodes"])

logger = logging.getLogger(__name__)


# ── 请求/响应 Schema（用 dict，避免额外 schemas 文件） ──

def _to_dict(q: WechatQrcode, project_name: Optional[str] = None) -> dict:
    return {
        "id": q.id,
        "scene_str": q.scene_str,
        "name": q.name,
        "description": q.description,
        "ticket": q.ticket,
        "url": q.url,
        "qrcode_image_url": q.qrcode_image_url,
        "type": q.type,
        "expire_seconds": q.expire_seconds,
        "status": q.status,
        "batch_id": q.batch_id,
        "project_id": q.project_id,
        # 录入信息行自带项目名（优先）；普通码行该列为 NULL，用联查到的所属项目名
        "project_name": q.project_name or project_name,
        "project_code": q.project_code,
        "project_location": q.project_location,
        "customer_name": q.customer_name,
        "vehicle_model": q.vehicle_model,
        "redirect_url": q.redirect_url,
        "created_by": q.created_by,
        "published_by": q.published_by,
        "deprecated_by": q.deprecated_by,
        "ticket_created_at": q.ticket_created_at.isoformat() if q.ticket_created_at else None,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "updated_at": q.updated_at.isoformat() if q.updated_at else None,
    }


def _project_name_map(db: Session, project_ids) -> dict:
    """按 project.id 批量取项目名（列表页避免 N+1）。

    不加软删过滤：码挂过的项目之后改名/软删，历史码的列表里仍要显示得出名字。
    """
    ids = {pid for pid in project_ids if pid}
    if not ids:
        return {}
    rows = db.query(Project.id, Project.name).filter(Project.id.in_(ids)).all()
    return {pid: name for pid, name in rows}


def _dict_with_project(db: Session, q: WechatQrcode) -> dict:
    """单条响应：附上项目名（一次额外查询，单条接口无 N+1 问题）。"""
    return _to_dict(q, _project_name_map(db, {q.project_id}).get(q.project_id))


def _resolve_project_ref(db: Session, project_id: Optional[str]) -> Optional[str]:
    """校验并规范化项目引用。

    - None → None（create=不关联；update 不用本函数语义，见该接口注释）
    - 空串/空白 → None（清除关联）
    - 其他 → 必须命中存在且未软删的项目，否则 400
    """
    if project_id is None:
        return None
    pid = project_id.strip()
    if not pid:
        return None
    row = db.query(Project.id, Project.status).filter(Project.id == pid).first()
    if not row or row[1] == PROJECT_DELETED:
        raise HTTPException(status_code=400, detail=f"项目不存在或已删除: {pid}")
    return pid


# ── 列表 ──

@router.get("/", summary="获取二维码列表")
async def list_qrcodes(
    status: Optional[str] = Query(None, description="按状态过滤"),
    qrcode_type: Optional[str] = Query(None, description="按类型过滤: temporary/permanent"),
    keyword: Optional[str] = Query(None, description="按 scene_str / name 模糊搜索"),
    batch_id: Optional[str] = Query(None, description="按批次过滤"),
    project_id: Optional[str] = Query(None, description="按所属项目过滤（project.id）"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        query = db.query(WechatQrcode)
        if status:
            query = query.filter(WechatQrcode.status == status)
        if qrcode_type:
            query = query.filter(WechatQrcode.type == qrcode_type)
        if batch_id:
            query = query.filter(WechatQrcode.batch_id == batch_id)
        if project_id:
            query = query.filter(WechatQrcode.project_id == project_id)
        if keyword:
            kw = f"%{keyword}%"
            query = query.filter(or_(
                WechatQrcode.scene_str.like(kw),
                WechatQrcode.name.like(kw),
            ))

        total = query.count()
        items = query.order_by(WechatQrcode.created_at.desc()).offset(skip).limit(limit).all()
        names = _project_name_map(db, {q.project_id for q in items})

        return {
            "total": total,
            "items": [_to_dict(q, names.get(q.project_id)) for q in items],
        }
    finally:
        db.close()


# ── 单条 ──

@router.get("/{qid}", summary="获取二维码详情")
async def get_qrcode(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        return _dict_with_project(db, q)
    finally:
        db.close()


# ── 创建（单条） ──

@router.post("/", summary="创建二维码记录（init 状态，不调微信接口）")
async def create_qrcode(
    scene_str: str = Body(..., embed=True),
    name: str = Body("", embed=True),
    description: Optional[str] = Body(None, embed=True),
    qrcode_type: str = Body(QrcodeType.PERMANENT, embed=True),
    redirect_url: Optional[str] = Body(None, embed=True),
    project_id: Optional[str] = Body(None, embed=True, description="所属项目ID（project.id），选填"),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        if not scene_str or len(scene_str) > 64:
            raise HTTPException(status_code=400, detail="scene_str 长度 1~64 字符")
        if db.query(WechatQrcode).filter(WechatQrcode.scene_str == scene_str).first():
            raise HTTPException(status_code=400, detail=f"scene_str '{scene_str}' 已存在")

        q = WechatQrcode(
            scene_str=scene_str,
            name=name or scene_str,
            description=description,
            type=qrcode_type,
            redirect_url=redirect_url,
            project_id=_resolve_project_ref(db, project_id),
            created_by=current_user.get("username") if isinstance(current_user, dict) else str(current_user),
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return _dict_with_project(db, q)
    finally:
        db.close()


# ── 批量创建（只落库，不调微信） ──

@router.post("/batch", summary="批量创建二维码记录（init 状态）")
async def batch_create_qrcodes(
    scene_list: List[str] = Body(..., embed=True),
    name_prefix: str = Body("", embed=True),
    qrcode_type: str = Body(QrcodeType.PERMANENT, embed=True),
    redirect_url: Optional[str] = Body(None, embed=True),
    project_id: Optional[str] = Body(None, embed=True, description="整批统一关联的项目ID，选填"),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    created_by = current_user.get("username") if isinstance(current_user, dict) else str(current_user)

    results = {"batch_id": batch_id, "created": [], "skipped": []}

    try:
        pid = _resolve_project_ref(db, project_id)
        existing_scenes = {r[0] for r in db.query(WechatQrcode.scene_str).all()}

        for scene in scene_list:
            if not scene or len(scene) > 64:
                results["skipped"].append({"scene": scene, "reason": "scene_str 长度 1~64"})
                continue
            if scene in existing_scenes:
                results["skipped"].append({"scene": scene, "reason": "已存在"})
                continue

            q = WechatQrcode(
                scene_str=scene,
                name=f"{name_prefix}{scene}" if name_prefix else scene,
                type=qrcode_type,
                redirect_url=redirect_url,
                project_id=pid,
                batch_id=batch_id,
                created_by=created_by,
            )
            db.add(q)
            results["created"].append(scene)

        db.commit()
        results["created_count"] = len(results["created"])
        results["skipped_count"] = len(results["skipped"])
        results["project_id"] = pid
        return results
    finally:
        db.close()


# ── 生成 ticket（调微信接口） ──

PERMANENT_QRCODE_MAX = 100_000
BATCH_THROTTLE_SECONDS = 0.5  # 每次调用间隔，避免限流

@router.post("/{qid}/generate", summary="调微信接口生成 ticket")
async def generate_qrcode_ticket(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        if q.status not in (QrcodeStatus.INIT, QrcodeStatus.ENTERING):
            raise HTTPException(status_code=400, detail=f"当前状态 {q.status} 不可生成 ticket")

        is_perm = q.type == QrcodeType.PERMANENT
        if is_perm:
            perm_count = db.query(WechatQrcode).filter(WechatQrcode.type == QrcodeType.PERMANENT).count()
            if perm_count >= PERMANENT_QRCODE_MAX:
                raise HTTPException(status_code=400, detail=f"永久码已达上限 {PERMANENT_QRCODE_MAX}")

        svc = get_wechat_service()
        result = svc.create_qrcode_ticket(
            scene_str=q.scene_str,
            is_permanent=is_perm,
            expire_seconds=q.expire_seconds or 2592000,
        )
        if not result:
            raise HTTPException(status_code=502, detail="微信接口调用失败")

        q.ticket = result.get("ticket")
        q.url = result.get("url")
        q.expire_seconds = result.get("expire_seconds")
        q.status = QrcodeStatus.ENTERING
        q.ticket_created_at = datetime.utcnow()
        db.commit()
        db.refresh(q)
        return _dict_with_project(db, q)
    finally:
        db.close()


@router.post("/batch-generate", summary="批量生成 ticket（循环调微信接口）")
async def batch_generate_tickets(
    batch_id: Optional[str] = Body(None, embed=True, description="按 batch_id 筛选 init 状态记录"),
    qid_list: Optional[List[int]] = Body(None, embed=True, description="指定 ID 列表（优先级高于 batch_id）"),
    only_init: bool = Body(True, embed=True, description="只处理 init 状态"),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        query = db.query(WechatQrcode)
        if qid_list:
            query = query.filter(WechatQrcode.id.in_(qid_list))
        elif batch_id:
            query = query.filter(WechatQrcode.batch_id == batch_id)
        else:
            raise HTTPException(status_code=400, detail="必须提供 batch_id 或 qid_list")
        if only_init:
            query = query.filter(WechatQrcode.status.in_([QrcodeStatus.INIT, QrcodeStatus.ENTERING]))

        records = query.all()
        results = {"total": len(records), "success": [], "failed": []}

        svc = get_wechat_service()
        perm_count = db.query(WechatQrcode).filter(WechatQrcode.type == QrcodeType.PERMANENT).count()

        for q in records:
            is_perm = q.type == QrcodeType.PERMANENT
            if is_perm and perm_count >= PERMANENT_QRCODE_MAX:
                results["failed"].append({"id": q.id, "scene_str": q.scene_str, "reason": "永久码配额已达上限"})
                continue

            try:
                wx_result = svc.create_qrcode_ticket(
                    scene_str=q.scene_str,
                    is_permanent=is_perm,
                    expire_seconds=q.expire_seconds or 2592000,
                )
                if wx_result:
                    q.ticket = wx_result.get("ticket")
                    q.url = wx_result.get("url")
                    q.expire_seconds = wx_result.get("expire_seconds")
                    q.status = QrcodeStatus.ENTERING
                    q.ticket_created_at = datetime.utcnow()
                    results["success"].append({"id": q.id, "scene_str": q.scene_str})
                    if is_perm:
                        perm_count += 1
                else:
                    results["failed"].append({"id": q.id, "scene_str": q.scene_str, "reason": "微信接口返回空"})
            except Exception as e:
                results["failed"].append({"id": q.id, "scene_str": q.scene_str, "reason": str(e)})

            time.sleep(BATCH_THROTTLE_SECONDS)

        db.commit()
        return results
    finally:
        db.close()


# ── 更新字段 ──

@router.put("/{qid}", summary="更新二维码字段")
async def update_qrcode(
    qid: int,
    name: Optional[str] = Body(None, embed=True),
    description: Optional[str] = Body(None, embed=True),
    redirect_url: Optional[str] = Body(None, embed=True),
    project_id: Optional[str] = Body(None, embed=True, description="所属项目ID；传空串清除关联，不传则不修改"),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")

        if name is not None:
            q.name = name
        if description is not None:
            q.description = description
        if redirect_url is not None:
            q.redirect_url = redirect_url
        if project_id is not None:
            # 空串 → _resolve_project_ref 返回 None → 清除关联
            q.project_id = _resolve_project_ref(db, project_id)

        db.commit()
        db.refresh(q)
        return _dict_with_project(db, q)
    finally:
        db.close()


# ── 录入信息（其他项目登记） ──
#
# 「新建项目 → 录入信息」一条录入 = wechat_qrcodes 一行：项目id/项目编号/项目名/
# 项目地点/客户名/车型六个字段和行 id 同行存（2026-09-29 用户口径），码记录名跟随项目名。
# 与「扫码关联 USP 项目」的区别：这里的 project_id 是业务键（后续企微表格同步过来），
# 不做 project 表存在性校验。项目id「唯一不可改」（先可留空，之后补填一次）、
# 项目编号「唯一可改」——唯一性只在「录入信息行」（project_code 非空）范围内查重，
# 普通码行这些列为 NULL、其 project_id 仍允许多行指向同一项目（多台车/重印）。

_INFO_FIELD_MAX = {
    "project_id": 64, "project_code": 64, "project_name": 128,
    "project_location": 128, "customer_name": 128, "vehicle_model": 128,
}
_INFO_FIELD_LABEL = {
    "project_id": "项目id", "project_code": "项目编号", "project_name": "项目名",
    "project_location": "项目地点", "customer_name": "客户名", "vehicle_model": "车型",
}


def _clean_info_value(value: Optional[str], field: str) -> Optional[str]:
    """strip 后返回；空白 → None（选填字段清空）；超长 → 400。"""
    if value is None:
        return None
    v = str(value).strip()
    if len(v) > _INFO_FIELD_MAX[field]:
        raise HTTPException(status_code=400, detail=f"{_INFO_FIELD_LABEL[field]}最长 {_INFO_FIELD_MAX[field]} 字符")
    return v or None


def _check_info_unique(db: Session, field: str, value: Optional[str], exclude_id: Optional[int] = None) -> None:
    """录入信息行内查重（见本段注释：范围只限 project_code 非空的行）。"""
    if not value:
        return
    query = db.query(WechatQrcode.id).filter(
        getattr(WechatQrcode, field) == value,
        WechatQrcode.project_code.isnot(None),
    )
    if exclude_id is not None:
        query = query.filter(WechatQrcode.id != exclude_id)
    if query.first():
        raise HTTPException(status_code=400, detail=f"{_INFO_FIELD_LABEL[field]}已存在：{value}")


def _gen_info_scene(db: Session) -> str:
    """录入信息行的场景值：proj_ + 随机 hex（不承载业务含义，只需唯一）。"""
    while True:
        scene = f"proj_{uuid.uuid4().hex[:12]}"
        if not db.query(WechatQrcode.id).filter(WechatQrcode.scene_str == scene).first():
            return scene


@router.post("/project-info", summary="录入信息：登记一条项目信息（一项目一行）")
async def create_project_info(
    project_id: Optional[str] = Body(None, embed=True, description="项目id（唯一；可先留空后补，存过不可改）"),
    project_code: str = Body(..., embed=True, description="项目编号（唯一，可改）"),
    project_name: str = Body(..., embed=True, description="项目名"),
    project_location: Optional[str] = Body(None, embed=True, description="项目地点"),
    customer_name: Optional[str] = Body(None, embed=True, description="客户名"),
    vehicle_model: Optional[str] = Body(None, embed=True, description="车型"),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        pid = _clean_info_value(project_id, "project_id")
        code = _clean_info_value(project_code, "project_code")
        name = _clean_info_value(project_name, "project_name")
        if not code:
            raise HTTPException(status_code=400, detail="项目编号不能为空")
        if not name:
            raise HTTPException(status_code=400, detail="项目名不能为空")
        _check_info_unique(db, "project_id", pid)
        _check_info_unique(db, "project_code", code)

        q = WechatQrcode(
            scene_str=_gen_info_scene(db),
            # 码记录名跟随项目名：列表/预览不用另开字段就能看到是哪个项目
            name=name,
            type=QrcodeType.PERMANENT,
            project_id=pid,
            project_code=code,
            project_name=name,
            project_location=_clean_info_value(project_location, "project_location"),
            customer_name=_clean_info_value(customer_name, "customer_name"),
            vehicle_model=_clean_info_value(vehicle_model, "vehicle_model"),
            created_by=current_user.get("username") if isinstance(current_user, dict) else str(current_user),
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return _dict_with_project(db, q)
    finally:
        db.close()


@router.put("/{qid}/project-info", summary="录入信息：更新一条项目信息")
async def update_project_info(
    qid: int,
    project_id: Optional[str] = Body(None, embed=True, description="项目id；只在原为空时补填一次，已存过则不可改；不传不改"),
    project_code: Optional[str] = Body(None, embed=True, description="项目编号（唯一，可改）；不传不改"),
    project_name: Optional[str] = Body(None, embed=True, description="项目名；不传不改"),
    project_location: Optional[str] = Body(None, embed=True, description="项目地点；传空串清空"),
    customer_name: Optional[str] = Body(None, embed=True, description="客户名；传空串清空"),
    vehicle_model: Optional[str] = Body(None, embed=True, description="车型；传空串清空"),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")

        # 项目id：唯一不可改。空串视为「没填/不改」；只在原为空时允许补填一次
        pid = _clean_info_value(project_id, "project_id")
        if pid:
            if q.project_id and q.project_id != pid:
                raise HTTPException(status_code=400, detail="项目id不可修改")
            if not q.project_id:
                _check_info_unique(db, "project_id", pid)
                q.project_id = pid

        if project_code is not None:
            code = _clean_info_value(project_code, "project_code")
            if not code:
                raise HTTPException(status_code=400, detail="项目编号不能为空")
            _check_info_unique(db, "project_code", code, exclude_id=q.id)
            q.project_code = code

        if project_name is not None:
            name = _clean_info_value(project_name, "project_name")
            if not name:
                raise HTTPException(status_code=400, detail="项目名不能为空")
            q.project_name = name
            q.name = name  # 码记录名跟随项目名

        if project_location is not None:
            q.project_location = _clean_info_value(project_location, "project_location")
        if customer_name is not None:
            q.customer_name = _clean_info_value(customer_name, "customer_name")
        if vehicle_model is not None:
            q.vehicle_model = _clean_info_value(vehicle_model, "vehicle_model")

        db.commit()
        db.refresh(q)
        return _dict_with_project(db, q)
    finally:
        db.close()


# ── 状态流转 ──

_STATUS_TRANSITIONS = {
    QrcodeStatus.INIT: [QrcodeStatus.ENTERING],
    QrcodeStatus.ENTERING: [QrcodeStatus.CONFIRMING, QrcodeStatus.INIT],   # 确认前可回退
    QrcodeStatus.CONFIRMING: [QrcodeStatus.PUBLISHED, QrcodeStatus.ENTERING],
    QrcodeStatus.PUBLISHED: [QrcodeStatus.DEPRECATED],
    QrcodeStatus.DEPRECATED: [],
}


def _transition(db: Session, q: WechatQrcode, target: str, actor: str) -> WechatQrcode:
    allowed = _STATUS_TRANSITIONS.get(q.status, [])
    if target not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"状态 {q.status} 无法转到 {target}，允许: {allowed}",
        )
    q.status = target
    if target == QrcodeStatus.PUBLISHED:
        q.published_by = actor
    if target == QrcodeStatus.DEPRECATED:
        q.deprecated_by = actor
    db.commit()
    db.refresh(q)
    return q


@router.post("/{qid}/confirm", summary="状态流转 → confirming")
async def confirm_qrcode(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        actor = current_user.get("username") if isinstance(current_user, dict) else str(current_user)
        return _dict_with_project(db, _transition(db, q, QrcodeStatus.CONFIRMING, actor))
    finally:
        db.close()


@router.post("/{qid}/publish", summary="状态流转 → published")
async def publish_qrcode(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        if not q.ticket:
            raise HTTPException(status_code=400, detail="未生成 ticket，无法发布")
        actor = current_user.get("username") if isinstance(current_user, dict) else str(current_user)
        return _dict_with_project(db, _transition(db, q, QrcodeStatus.PUBLISHED, actor))
    finally:
        db.close()


@router.post("/{qid}/deprecate", summary="状态流转 → deprecated")
async def deprecate_qrcode(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        actor = current_user.get("username") if isinstance(current_user, dict) else str(current_user)
        return _dict_with_project(db, _transition(db, q, QrcodeStatus.DEPRECATED, actor))
    finally:
        db.close()


# ── 删除 ──

@router.delete("/{qid}", summary="删除二维码记录")
async def delete_qrcode(qid: int, current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        q = db.query(WechatQrcode).filter(WechatQrcode.id == qid).first()
        if not q:
            raise HTTPException(status_code=404, detail="二维码不存在")
        if q.status not in (QrcodeStatus.INIT, QrcodeStatus.DEPRECATED):
            raise HTTPException(status_code=400, detail=f"状态 {q.status} 不可删除，请先弃用")
        db.delete(q)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ── 状态统计 ──

@router.get("/stats/summary", summary="各状态数量统计")
async def qrcode_stats(current_user=require_permission("frontend:admin:other:show")):
    db: Session = db_manager.get_db()
    try:
        rows = db.query(
            WechatQrcode.status,
            WechatQrcode.type,
            db.query(WechatQrcode).filter(
                WechatQrcode.status == WechatQrcode.status,
                WechatQrcode.type == WechatQrcode.type,
            ).correlate(WechatQrcode).count(),
        ).distinct().all()

        summary = {"status": {}, "type": {}, "total": db.query(WechatQrcode).count()}
        for s in [QrcodeStatus.INIT, QrcodeStatus.ENTERING, QrcodeStatus.CONFIRMING, QrcodeStatus.PUBLISHED, QrcodeStatus.DEPRECATED]:
            summary["status"][s] = db.query(WechatQrcode).filter(WechatQrcode.status == s).count()
        for t in [QrcodeType.TEMPORARY, QrcodeType.PERMANENT]:
            summary["type"][t] = db.query(WechatQrcode).filter(WechatQrcode.type == t).count()
        summary["permanent_quota_remaining"] = PERMANENT_QRCODE_MAX - summary["type"].get(QrcodeType.PERMANENT, 0)
        return summary
    finally:
        db.close()
