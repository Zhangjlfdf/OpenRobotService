"""项目信息树节点 API（逐节点 CRUD + 树查询 + 批量导入 + 文件 AI 识别导入预览 + 详情模板 + 编辑历史 + 关注/项目动态）。

路由前缀 /info-nodes，挂载到 admin_router 后实际路径为
/api/admin/info-nodes/projects/{project_id}/...。
详情模板接口（/info-nodes/template）仅管理员可用（require get_current_admin_user）。

写接口不强制鉴权（沿用网关管控），但会尽力识别操作人，把「谁做的」记进
project_info_node_change（编辑历史，见 services/info_node_change_service.py）。

性能约定：除 parse-file（需 await 大模型调用）外，本组路由均为同步 def——
Service 层是同步 SQLAlchemy，async def 里跑同步 DB 会阻塞事件循环、拖慢全部并发请求；
同步 def 由 FastAPI 自动放入线程池执行（默认 40 线程），互不阻塞。
"""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from app.modules.admin.api.auth import get_request_actor_optional
from app.modules.admin.api.permissions import get_current_admin_user
from app.modules.admin.services.info_node_service import info_node_service
from app.modules.admin.services.info_node_change_service import info_node_change_service
from app.modules.admin.services.info_node_mark_service import info_node_mark_service
from app.modules.admin.services import info_node_import_service
from app.modules.admin.services.info_template_service import info_template_service


# ── 请求模型 ───────────────────────────────────────────

class InfoNodeCreate(BaseModel):
    id: str = Field(..., description="节点UUID(客户端生成)")
    parent_id: Optional[str] = Field(None, description="父节点ID, NULL=根节点")
    title: str = Field("未命名节点", description="节点标题")
    content_type: str = Field("text", description="内容类型: text/image/file/...")
    value: Optional[str] = Field(None, description="节点值")
    sort_order: int = Field(0, description="同级排序")


class InfoNodeUpdate(BaseModel):
    title: Optional[str] = None
    content_type: Optional[str] = None
    value: Optional[str] = None
    sort_order: Optional[int] = None


class InfoNodeMove(BaseModel):
    new_parent_id: Optional[str] = Field(None, description="目标父节点ID, NULL=移到根")
    new_sort_order: int = Field(0, description="目标排序位置")


class InfoNodeImport(BaseModel):
    nodes: List[Dict[str, Any]] = Field(..., description="信息树(递归嵌套, 含children)")


class InfoTemplateSave(BaseModel):
    nodes: List[Dict[str, Any]] = Field(..., description="详情模板节点树(递归嵌套, 含children)")
    dry_run: bool = Field(False, description="为 True 时只预览同步影响，不保存、不写项目")


# ── 路由 ───────────────────────────────────────────────

info_node_router = APIRouter(prefix="/info-nodes", tags=["admin-info-nodes"])


@info_node_router.get("/projects/{project_id}", summary="获取项目信息树")
def get_info_tree(project_id: str):
    """返回项目信息树的递归嵌套结构（每节点含 children 数组）。"""
    tree = info_node_service.get_tree(project_id)
    return tree


@info_node_router.post("/projects/{project_id}", summary="创建信息节点", status_code=201)
def create_info_node(project_id: str, node: InfoNodeCreate,
                           actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """创建单个信息节点。id 由客户端生成（UUID），供后续引用。"""
    return info_node_service.create_node(
        project_id, node.model_dump(),
        operator=actor.get("username"), operator_name=actor.get("name"),
    )


@info_node_router.put("/nodes/{node_id}", summary="更新信息节点")
def update_info_node(node_id: str, update: InfoNodeUpdate,
                           actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """更新节点可编辑字段。parent_id 变更请用 PATCH move。"""
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="无更新字段")
    result = info_node_service.update_node(
        node_id, update_data,
        operator=actor.get("username"), operator_name=actor.get("name"),
    )
    if not result:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@info_node_router.patch("/nodes/{node_id}/move", summary="移动信息节点")
def move_info_node(node_id: str, move: InfoNodeMove,
                         actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """移动节点到新父节点下并设置排序位置（拖拽排序）。"""
    result = info_node_service.move_node(
        node_id, move.new_parent_id, move.new_sort_order,
        operator=actor.get("username"), operator_name=actor.get("name"),
    )
    if not result:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@info_node_router.delete("/nodes/{node_id}", summary="删除信息节点(含子树)")
def delete_info_node(node_id: str,
                           actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """删除节点及其全部子树（递归 CTE 找后代，批量删除）。

    删除记录只有一条，挂在被删节点的上级节点上（见 info_node_change_service）。
    """
    deleted = info_node_service.delete_node(
        node_id, operator=actor.get("username"), operator_name=actor.get("name"),
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"detail": "已删除节点及其子树"}


@info_node_router.post("/projects/{project_id}/import", summary="批量导入信息树")
def import_info_tree(project_id: str, data: InfoNodeImport,
                           actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """批量导入信息树（如从 a.json 的 info_nodes 导入）。
    先清空旧节点再导入。返回导入数量。
    """
    count = info_node_service.import_tree(
        project_id, data.nodes,
        operator=actor.get("username"), operator_name=actor.get("name"),
    )
    return {"imported": count}


@info_node_router.post("/projects/{project_id}/import-template",
                       summary="按项目模板重建信息树")
def import_info_template(project_id: str,
                               actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """用后端模板（project_type → {type}.yaml，缺省 default.yaml）重建整棵信息树。

    与新建项目初始化走同一份模板定义，供存量空项目一键初始化；
    先清空旧节点再写入。模板为空时不做改动，返回 {"imported": 0}。
    """
    try:
        count = info_node_service.import_template(
            project_id,
            operator=actor.get("username"), operator_name=actor.get("name"),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"imported": count}


# ── 编辑历史（节点操作记录） ──────────────────────────

@info_node_router.get("/projects/{project_id}/changes", summary="获取节点操作记录")
def get_info_node_changes(
    project_id: str,
    node_id: Optional[str] = Query(None, description="节点ID；给了则只返回该节点的历史（含其直接子节点的删除记录）"),
    limit: int = Query(100, ge=1, le=500, description="最多返回条数（最新在前）"),
):
    """节点编辑历史。传 node_id 返回该节点的记录（自身操作 + 其子节点的删除记录）；
    不传则返回项目全部记录（含整树级导入/模板同步）。"""
    if node_id:
        changes = info_node_change_service.list_for_node(project_id, node_id, limit)
    else:
        changes = info_node_change_service.list_project_changes(project_id, limit)
    return {"changes": changes}


@info_node_router.get("/projects/{project_id}/changes/summary",
                      summary="各节点最新记录 id（小红点）")
def get_info_node_change_summary(project_id: str):
    """返回 {节点id: 最新记录 id}，用于前端判断哪些节点的「历史」有新变动（小红点）：
    与本机已读水位（也是记录 id）不一致即未读。

    记录 id 时间有序，只做相等比较；不用时间戳是因为它只到秒，同秒内的新记录会漏。
    与 changes 接口同口径：子节点的删除记录计入其上级节点。
    """
    return {"latest": info_node_change_service.latest_by_node(project_id)}


# ── 关注（标注）与项目动态（按当前登录人隔离） ────────────

def _require_operator(actor: Dict[str, Optional[str]]) -> str:
    """关注列表是「每人一份」，识别不到操作人就无法读写个人列表 → 401。

    正常前端请求带 Bearer token（JWT sub 即登录名）；401 会触发前端的
    刷新重试链路，token 过期场景可自愈。
    """
    username = actor.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="无法识别当前用户，请重新登录后再关注")
    return username


@info_node_router.get("/projects/{project_id}/marks", summary="获取当前用户关注的节点ID列表")
def get_info_node_marks(project_id: str,
                              actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """返回当前登录人在该项目关注的节点 id（「项目信息管理」卡据它点亮星标）。

    关注按人隔离：自己关注的自己才能看到，各人的星标互不影响。
    """
    operator = _require_operator(actor)
    return {"node_ids": info_node_mark_service.list_for_project(project_id, operator)}


@info_node_router.post("/nodes/{node_id}/mark", summary="切换当前用户的节点关注状态")
def toggle_info_node_mark(node_id: str,
                                actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """点星标：未关注→关注，已关注→取消。返回 {"marked": 切换后是否被关注}。

    只动当前登录人自己的关注，别人对同一节点的关注不受影响。
    """
    operator = _require_operator(actor)
    try:
        marked = info_node_mark_service.toggle(
            node_id, operator=operator, operator_name=actor.get("name"),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"marked": marked}


@info_node_router.get("/projects/{project_id}/activity",
                      summary="项目动态（当前用户被关注节点的最新变动）")
def get_project_activity(project_id: str,
                               actor: Dict[str, Optional[str]] = Depends(get_request_actor_optional)):
    """当前登录人关注的每个节点只返回其**最新一条**变动（整体最新在前）。

    与前端「项目动态」卡一致：只给变动内容（detail，服务端拼好的人话描述），
    时间/人员字段存在但前端不展示（用户要求动态里不出现修改时间与人员）。
    没记过任何操作的被关注节点不出现（无变动可展示）。
    """
    operator = _require_operator(actor)
    return {"activity": info_node_mark_service.marked_activity(project_id, operator)}


@info_node_router.post("/projects/{project_id}/parse-file",
                       summary="AI 识别导入文件（预览，不落库）")
async def parse_import_file(project_id: str, file: UploadFile = File(...)):
    """上传 Word/Markdown/Excel/文本，由大模型（摇人同款，默认 DeepSeek flash）识别其中
    的项目信息，与现有节点匹配后按「将填写 / 将覆盖 / 未匹配到节点」三类返回预览。

    本接口只读不写：用户在前端勾选确认后，由前端逐节点调用既有 CRUD 接口落库。
    未识别到信息时三个数组均为空。错误约定：400=文件/状态问题，503=AI 未配置或调用失败。
    """
    data = await file.read()
    try:
        return await info_node_import_service.analyze_import_file(project_id, file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ── 详情模板（管理员可编辑；保存后同步到所有项目的节点） ──

@info_node_router.get("/template", summary="获取项目详情模板（仅管理员）")
def get_info_template(current_user: Dict[str, Any] = Depends(get_current_admin_user)):
    """返回详情模板（数据库模板；首次访问用 YAML 默认模板补种）与项目数量。

    返回字段：nodes（节点树，含稳定 id）/ name / updated_at / updated_by /
    project_count（将受同步影响的项目数）/ source（db=已入库，yaml=本次由默认模板补种）。
    """
    return info_template_service.get_template()


@info_node_router.post("/template", summary="保存项目详情模板并同步所有项目（仅管理员）")
def save_info_template(
    payload: InfoTemplateSave,
    current_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """保存模板并把变更同步到所有项目的信息节点。

    dry_run=true：只预览影响（新增/更新/删除的节点数、涉及项目数），不写库；
    否则：校验 → 保存模板 → 按同步锚点（template_node_id）对齐每个项目的节点，
    存量节点首次按标题路径回填锚点，用户自建节点不受影响。
    模板节点校验失败返回 400（层级过深 / 下拉带子节点 / 标题为空等）。
    """
    try:
        if payload.dry_run:
            return info_template_service.preview_sync(payload.nodes)
        return info_template_service.save_and_sync(
            payload.nodes,
            current_user.get("username") or "",
            current_user.get("name") or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
