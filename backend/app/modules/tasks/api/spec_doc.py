"""工单「问题文档」API：读取 / 保存（md 在线编辑）/ 上传解析。

路由（挂在 /api/tasks 下）：
- GET  /{task_id}/spec-doc   读文档（无则 exists=false）
- PUT  /{task_id}/spec-doc   保存正文（乐观锁 revision，冲突返回 409）
- POST /spec-doc/parse       上传 .md/.doc/.docx 解析为 markdown（并保留原文件到 MinIO）

鉴权：GET 需登录；PUT 需登录 + 属主/接单人/管理员。
安全：上传走扩展名白名单 + 魔数 + 大小上限（见 spec_doc_parser）；
     原文件 object_path 用 uuid 目录 + 安全化文件名，规避路径穿越。
"""
import logging
import os
import re
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth_routes import get_current_active_user_from_token
from app.core.config import settings
from app.core.database import get_async_db as get_db
from app.core.user_identity import actor_username, is_admin_user, user_matches
from app.models.task import Task, TaskSpecDoc
from app.modules.tasks.schemas.spec_doc import (
    SpecDocParseResult,
    SpecDocResponse,
    SpecDocUpdate,
)
from app.utils.minio_client import minio_client
from app.utils.spec_doc_parser import (
    MAX_DOC_SIZE,
    SpecDocParseError,
    parse_spec_document,
)

router = APIRouter(tags=["task-spec-doc"])
logger = logging.getLogger(__name__)

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_filename(name: str) -> str:
    """安全化用户文件名（去目录、过滤危险字符），避免路径穿越。"""
    base = os.path.basename((name or "").strip()) or "document"
    base = _UNSAFE_FILENAME_RE.sub("_", base)
    base = base.lstrip(".") or "document"
    return base[:120]


async def _load_task_or_404(db: AsyncSession, task_id: int) -> Task:
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return task


async def _load_doc(db: AsyncSession, task_id: int):
    result = await db.execute(select(TaskSpecDoc).where(TaskSpecDoc.task_id == task_id))
    return result.scalar_one_or_none()


def _can_edit(current_user: Any, task: Task) -> bool:
    if is_admin_user(current_user):
        return True
    return user_matches(current_user, task.created_by, task.assigned_to, task.customer)


def _resolve_name(username: str) -> str:
    if not username:
        return ""
    try:
        from app.services.user_service import user_service
        umap = user_service.get_user_map() or {}
        return umap.get(username) or username
    except Exception:
        return username


def _to_response(task_id: int, doc) -> SpecDocResponse:
    if not doc:
        return SpecDocResponse(exists=False, task_id=int(task_id))
    return SpecDocResponse(
        exists=True,
        task_id=int(doc.task_id),
        content=doc.content or "",
        content_type=doc.content_type or "markdown",
        source=doc.source,
        source_files=doc.source_files or [],
        revision=int(doc.revision or 1),
        created_by=doc.created_by,
        updated_by=doc.updated_by,
        updated_by_name=_resolve_name(doc.updated_by or ""),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("/{task_id}/spec-doc", response_model=SpecDocResponse)
async def get_spec_doc(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """读取工单的问题文档（无文档返回 exists=false，前端据此显示空态）。"""
    await _load_task_or_404(db, task_id)
    doc = await _load_doc(db, task_id)
    return _to_response(task_id, doc)


@router.put("/{task_id}/spec-doc", response_model=SpecDocResponse)
async def upsert_spec_doc(
    task_id: int,
    payload: SpecDocUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """创建/更新问题文档正文。

    乐观锁：payload.revision 与库中不一致 → 409（说明他人已改过），前端提示刷新。
    不传 revision 则强制覆盖（用于首次创建/明确覆盖）。
    """
    task = await _load_task_or_404(db, task_id)
    if not _can_edit(current_user, task):
        raise HTTPException(status_code=403, detail="无权限编辑此工单文档")

    username = actor_username(current_user)
    doc = await _load_doc(db, task_id)

    if doc is None:
        doc = TaskSpecDoc(
            task_id=task_id,
            content=payload.content,
            content_type="markdown",
            source=payload.source or "inline",
            source_files=payload.source_files or [],
            revision=1,
            created_by=username,
            updated_by=username,
        )
        db.add(doc)
    else:
        if payload.revision is not None and int(payload.revision) != int(doc.revision or 1):
            raise HTTPException(
                status_code=409, detail="文档已被他人更新，请刷新后重试"
            )
        doc.content = payload.content
        if payload.source:
            doc.source = payload.source
        if payload.source_files is not None:
            doc.source_files = payload.source_files
        doc.revision = int(doc.revision or 1) + 1
        doc.updated_by = username

    await db.commit()
    await db.refresh(doc)
    return _to_response(task_id, doc)


@router.post("/spec-doc/parse", response_model=SpecDocParseResult)
async def parse_spec_doc(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """上传 .md/.markdown/.txt/.doc/.docx → 解析为 markdown；原文件保留到 MinIO。

    解析在成人心智：mammoth/soffice 为阻塞调用，走线程池避免卡事件循环。
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(raw) > MAX_DOC_SIZE:
        raise HTTPException(status_code=400, detail="文件过大，上限 5MB")

    filename = file.filename or "document"
    try:
        content = await run_in_threadpool(parse_spec_document, filename, raw)
    except SpecDocParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 原始文件落 MinIO（保留原件可下载）；失败降级为空 object_path，不阻塞解析结果
    object_path = ""
    try:
        safe_name = _safe_filename(filename)
        object_path = f"{settings.COMMENT_BUCKET}/spec-doc/{uuid.uuid4().hex}/{safe_name}"
        ok = await run_in_threadpool(
            minio_client.upload_bytes,
            raw,
            object_path,
            file.content_type or "application/octet-stream",
        )
        if not ok:
            logger.warning("[spec_doc] 原始文件上传 MinIO 返回失败: %s", object_path)
            object_path = ""
    except Exception as e:  # noqa: BLE001 - 存储失败不影响解析结果
        logger.warning("[spec_doc] 原始文件上传异常: %s", e)
        object_path = ""

    return SpecDocParseResult(
        content=content, filename=filename, size=len(raw), object_path=object_path
    )
