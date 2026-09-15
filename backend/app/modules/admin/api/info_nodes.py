"""项目信息树节点 API（逐节点 CRUD + 树查询 + 批量导入）。

路由前缀 /info-nodes，挂载到 admin_router 后实际路径为
/api/admin/info-nodes/projects/{project_id}/...。
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from app.modules.admin.services.info_node_service import info_node_service


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


# ── 路由 ───────────────────────────────────────────────

info_node_router = APIRouter(prefix="/info-nodes", tags=["admin-info-nodes"])


@info_node_router.get("/projects/{project_id}", summary="获取项目信息树")
async def get_info_tree(project_id: str):
    """返回项目信息树的递归嵌套结构（每节点含 children 数组）。"""
    tree = info_node_service.get_tree(project_id)
    return tree


@info_node_router.post("/projects/{project_id}", summary="创建信息节点", status_code=201)
async def create_info_node(project_id: str, node: InfoNodeCreate):
    """创建单个信息节点。id 由客户端生成（UUID），供后续引用。"""
    return info_node_service.create_node(project_id, node.model_dump())


@info_node_router.put("/nodes/{node_id}", summary="更新信息节点")
async def update_info_node(node_id: str, update: InfoNodeUpdate):
    """更新节点可编辑字段。parent_id 变更请用 PATCH move。"""
    update_data = {k: v for k, v in update.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="无更新字段")
    result = info_node_service.update_node(node_id, update_data)
    if not result:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@info_node_router.patch("/nodes/{node_id}/move", summary="移动信息节点")
async def move_info_node(node_id: str, move: InfoNodeMove):
    """移动节点到新父节点下并设置排序位置（拖拽排序）。"""
    result = info_node_service.move_node(node_id, move.new_parent_id, move.new_sort_order)
    if not result:
        raise HTTPException(status_code=404, detail="节点不存在")
    return result


@info_node_router.delete("/nodes/{node_id}", summary="删除信息节点(含子树)")
async def delete_info_node(node_id: str):
    """删除节点及其全部子树（递归 CTE 找后代，批量删除）。"""
    deleted = info_node_service.delete_node(node_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"detail": "已删除节点及其子树"}


@info_node_router.post("/projects/{project_id}/import", summary="批量导入信息树")
async def import_info_tree(project_id: str, data: InfoNodeImport):
    """批量导入信息树（如从 a.json 的 info_nodes 导入）。
    先清空旧节点再导入。返回导入数量。
    """
    count = info_node_service.import_tree(project_id, data.nodes)
    return {"imported": count}


@info_node_router.post("/projects/{project_id}/import-template",
                       summary="按项目模板重建信息树")
async def import_info_template(project_id: str):
    """用后端模板（project_type → {type}.yaml，缺省 default.yaml）重建整棵信息树。

    与新建项目初始化走同一份模板定义，供存量空项目一键初始化；
    先清空旧节点再写入。模板为空时不做改动，返回 {"imported": 0}。
    """
    try:
        count = info_node_service.import_template(project_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"imported": count}
