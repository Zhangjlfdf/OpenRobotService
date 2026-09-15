// 项目信息树节点 API —— 对接 admin 模块 /api/admin/info-nodes/*
// 后端实现：backend/app/modules/admin/api/info_nodes.py（路由前缀 /info-nodes，挂在 /api/admin 下）
// 契约要点（见 backend/docs/project_ext_info_info_nodes_api.md 第五节）：
//   - 逐节点 CRUD，不做整树读改写；换父/排序走 move，批量替换整树走 import；
//   - value 在库里是 TEXT，结构化内容由调用方自行编码（本项目用 JSON 字符串）；
//   - 创建节点的 id 由客户端生成（UUID），服务端原样入库，供后续稳定引用；
//   - 删除节点会连带删除整棵子树；import 会先清空该项目全部旧节点。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

/** 后端原始节点（value 为 TEXT 字符串；树查询时每节点含 children） */
export interface ApiInfoNode {
  id: string;
  project_id: string;
  parent_id: string | null;
  title: string;
  content_type: string;
  value: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
  children?: ApiInfoNode[];
}

export interface ApiInfoNodeCreate {
  id: string;
  parent_id?: string | null;
  title?: string;
  content_type?: string;
  value?: string | null;
  sort_order?: number;
}

/** 可更新字段；parent_id 不在此列，换父走 moveInfoNodeApi */
export interface ApiInfoNodeUpdate {
  title?: string;
  content_type?: string;
  value?: string | null;
  sort_order?: number;
}

/** import 接口的递归节点结构（children 递归嵌套） */
export interface ApiInfoTreeImportNode {
  id: string;
  title: string;
  content_type?: string;
  value?: string | null;
  sort_order?: number;
  children?: ApiInfoTreeImportNode[];
}

// 与其他 api 模块一致：调用时再建 requester，不在模块顶层求值（便于测试 mock @/api/client）
const request = () => createRequest(API_CONFIG.ADMIN.BASE_URL, '信息树服务');

/** 获取项目完整信息树（递归嵌套；空树返回 []） */
export async function fetchInfoTree(projectId: string): Promise<ApiInfoNode[]> {
  const data = await request()<ApiInfoNode[]>(`/info-nodes/projects/${encodeURIComponent(projectId)}`);
  return Array.isArray(data) ? data : [];
}

/** 创建节点（id 由调用方生成） */
export async function createInfoNodeApi(projectId: string, payload: ApiInfoNodeCreate): Promise<ApiInfoNode> {
  return request()<ApiInfoNode>(`/info-nodes/projects/${encodeURIComponent(projectId)}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/** 更新节点（title / content_type / value / sort_order；空对象会被后端 400 拒绝） */
export async function updateInfoNodeApi(nodeId: string, updates: ApiInfoNodeUpdate): Promise<ApiInfoNode> {
  return request()<ApiInfoNode>(`/info-nodes/nodes/${encodeURIComponent(nodeId)}`, {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
}

/** 移动节点（换父 + 排序位置）；newParentId 为 null 表示移到根 */
export async function moveInfoNodeApi(nodeId: string, newParentId: string | null, newSortOrder: number): Promise<ApiInfoNode> {
  return request()<ApiInfoNode>(`/info-nodes/nodes/${encodeURIComponent(nodeId)}/move`, {
    method: 'PATCH',
    body: JSON.stringify({ new_parent_id: newParentId, new_sort_order: newSortOrder }),
  });
}

/** 删除节点及其整棵子树（404 = 节点不存在） */
export async function deleteInfoNodeApi(nodeId: string): Promise<void> {
  await request()<{ detail?: string }>(`/info-nodes/nodes/${encodeURIComponent(nodeId)}`, { method: 'DELETE' });
}

/** 批量导入信息树（先清空该项目旧节点再写入）；返回写入节点数 */
export async function importInfoTreeApi(projectId: string, nodes: ApiInfoTreeImportNode[]): Promise<number> {
  const data = await request()<{ imported?: number }>(`/info-nodes/projects/${encodeURIComponent(projectId)}/import`, {
    method: 'POST',
    body: JSON.stringify({ nodes }),
  });
  return data?.imported ?? 0;
}
