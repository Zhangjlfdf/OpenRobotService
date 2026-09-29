"""二维码管理路由 —— 带参数二维码的 CRUD、批量创建、状态流转。

生命周期：init → entering → confirming → published → deprecated
- init: 场景值已定义，尚未调微信接口
- entering: 已调微信创建 ticket
- confirming: 已核对 ticket/图片正确
- published: 对外使用中
- deprecated: 停止使用

永久码上限 10 万，批量生成有速率限制。
"""
import time
import uuid
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Body
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import db_manager
from app.models.wechat_qrcode import WechatQrcode, QrcodeStatus, QrcodeType
from app.modules.admin.api.auth import require_permission
from app.wechat.services.wechat_service import wechat_service as get_wechat_service

router = APIRouter(prefix="/qrcodes", tags=["admin-qrcodes"])

logger = logging.getLogger(__name__)


# ── 请求/响应 Schema（用 dict，避免额外 schemas 文件） ──

def _to_dict(q: WechatQrcode) -> dict:
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
        "redirect_url": q.redirect_url,
        "created_by": q.created_by,
        "published_by": q.published_by,
        "deprecated_by": q.deprecated_by,
        "ticket_created_at": q.ticket_created_at.isoformat() if q.ticket_created_at else None,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "updated_at": q.updated_at.isoformat() if q.updated_at else None,
    }


# ── 列表 ──

@router.get("/", summary="获取二维码列表")
async def list_qrcodes(
    status: Optional[str] = Query(None, description="按状态过滤"),
    qrcode_type: Optional[str] = Query(None, description="按类型过滤: temporary/permanent"),
    keyword: Optional[str] = Query(None, description="按 scene_str / name 模糊搜索"),
    batch_id: Optional[str] = Query(None, description="按批次过滤"),
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
        if keyword:
            kw = f"%{keyword}%"
            query = query.filter(or_(
                WechatQrcode.scene_str.like(kw),
                WechatQrcode.name.like(kw),
            ))

        total = query.count()
        items = query.order_by(WechatQrcode.created_at.desc()).offset(skip).limit(limit).all()

        return {
            "total": total,
            "items": [_to_dict(q) for q in items],
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
        return _to_dict(q)
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
            created_by=current_user.get("username") if isinstance(current_user, dict) else str(current_user),
        )
        db.add(q)
        db.commit()
        db.refresh(q)
        return _to_dict(q)
    finally:
        db.close()


# ── 批量创建（只落库，不调微信） ──

@router.post("/batch", summary="批量创建二维码记录（init 状态）")
async def batch_create_qrcodes(
    scene_list: List[str] = Body(..., embed=True),
    name_prefix: str = Body("", embed=True),
    qrcode_type: str = Body(QrcodeType.PERMANENT, embed=True),
    redirect_url: Optional[str] = Body(None, embed=True),
    current_user=require_permission("frontend:admin:other:show"),
):
    db: Session = db_manager.get_db()
    batch_id = f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    created_by = current_user.get("username") if isinstance(current_user, dict) else str(current_user)

    results = {"batch_id": batch_id, "created": [], "skipped": []}

    try:
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
                batch_id=batch_id,
                created_by=created_by,
            )
            db.add(q)
            results["created"].append(scene)

        db.commit()
        results["created_count"] = len(results["created"])
        results["skipped_count"] = len(results["skipped"])
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
        return _to_dict(q)
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

        db.commit()
        db.refresh(q)
        return _to_dict(q)
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
        return _to_dict(_transition(db, q, QrcodeStatus.CONFIRMING, actor))
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
        return _to_dict(_transition(db, q, QrcodeStatus.PUBLISHED, actor))
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
        return _to_dict(_transition(db, q, QrcodeStatus.DEPRECATED, actor))
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
