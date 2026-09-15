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

/** 按后端模板重建信息树（服务端读 project_type → project_templates/*.yaml，先清空旧节点）；
 *  与新建项目初始化同一份模板，前端不再各自维护一份结构定义；模板为空时返回 0 且不改动数据 */
export async function importInfoTemplateApi(projectId: string): Promise<number> {
  const data = await request()<{ imported?: number }>(`/info-nodes/projects/${encodeURIComponent(projectId)}/import-template`, {
    method: 'POST',
  });
  return data?.imported ?? 0;
}

// —— 文件导入（AI 识别）：上传文档 → 后端调大模型识别 → 三类预览（不落库，确认后走上面的 CRUD） ——

/** 匹配到现有节点的识别条目（将填写 / 将覆盖共用） */
export interface ApiParseMatchedItem {
  node_id: string;
  /** 节点完整路径（如「基础信息 / 客户信息」），用于预览展示 */
  path: string;
  title: string;
  content_type: string;
  /** 节点当前内容（text 原值 / select 的 selected；空串=将填写） */
  current: string;
  /** 识别出的新内容（select 已对齐到可选项） */
  value: string;
}

/** 未匹配到节点的识别条目（确认后作为新节点创建） */
export interface ApiParseNewItem {
  title: string;
  value: string;
  /** 建议归属节点（后端已解析并校验层级）；null=前端用「导入信息」兜底 */
  suggested_parent_id: string | null;
  suggested_parent_path: string | null;
}

/** POST /info-nodes/projects/{id}/parse-file 返回 */
export interface ApiImportParseResult {
  file_name: string;
  /** 实际使用的模型（服务端 settings.LLM_MODEL_NAME，与摇人同一配置） */
  model: string;
  text_length: number;
  /** 正文超过服务端上限被截断时为 true */
  truncated: boolean;
  /** 大模型识别出的条目总数（含被去重的） */
  extracted: number;
  /** 当前系统内的项目名称 */
  project_name: string;
  /** 文件中识别到的项目名称；文件里没写则为 null */
  file_project_name: string | null;
  /** true = 两者确实不一致（后端判定），前端应提醒用户可能导错了文件 */
  name_mismatch: boolean;
  fill: ApiParseMatchedItem[];
  overwrite: ApiParseMatchedItem[];
  unmatched: ApiParseNewItem[];
}

/** 上传支持的文件（正文抽取与大模型识别都在后端完成），返回三类预览；本接口不写库 */
export async function parseImportFileApi(projectId: string, file: File): Promise<ApiImportParseResult> {
  const form = new FormData();
  form.append('file', file);
  const data = await request()<ApiImportParseResult>(
    `/info-nodes/projects/${encodeURIComponent(projectId)}/parse-file`,
    // 大模型识别耗时可能超过默认 30s，单独放宽超时（后端 LLM 调用上限 120s）
    { method: 'POST', body: form, timeout: 180000 },
  );
  return {
    file_name: data?.file_name ?? file.name,
    model: data?.model ?? '',
    text_length: data?.text_length ?? 0,
    truncated: !!data?.truncated,
    extracted: data?.extracted ?? 0,
    project_name: data?.project_name ?? '',
    file_project_name: typeof data?.file_project_name === 'string' && data.file_project_name
      ? data.file_project_name
      : null,
    name_mismatch: !!data?.name_mismatch,
    fill: Array.isArray(data?.fill) ? data.fill : [],
    overwrite: Array.isArray(data?.overwrite) ? data.overwrite : [],
    unmatched: Array.isArray(data?.unmatched) ? data.unmatched : [],
  };
}

// —— 项目详情模板（仅管理员）：编辑模板 → 保存并同步到所有项目的节点 ——

/** 模板节点（递归树；id 是模板侧稳定 UUID，即项目节点同步锚点） */
export interface ApiInfoTemplateNode {
  id: string;
  title: string;
  content_type: string;
  /** 仅 select 节点：可选项 */
  options?: string[];
  sort_order?: number;
  children?: ApiInfoTemplateNode[];
}

export interface ApiInfoTemplate {
  id: string;
  name: string;
  nodes: ApiInfoTemplateNode[];
  updated_at: string | null;
  /** 最近编辑人（首次补种为 system） */
  updated_by: string | null;
  /** 会受同步影响的项目数（未删除项目总数） */
  project_count: number;
  /** db=已入库；yaml=首次访问由默认模板补种 */
  source: string;
}

/** dry-run 预览 / 真实同步共用的统计与明细 */
export interface ApiInfoTemplateSyncResult {
  dry_run: boolean;
  /** 未删除项目总数 */
  projects: number;
  /** 实际会（或已）变更的项目数 */
  changed_projects: number;
  added: number;
  updated: number;
  deleted: number;
  /** 有变更项目的明细（后端最多给 20 条） */
  details?: Array<{ project_id: string; project_name: string; added: number; updated: number; deleted: number }>;
  /** 同步失败的项目（单个项目失败不拖垮整体） */
  failed_projects?: Array<{ project_id: string; error: string }>;
  updated_at?: string;
  updated_by?: string;
}

/** 获取项目详情模板（仅管理员；后端首次访问会用默认模板补种入库） */
export async function fetchInfoTemplateApi(): Promise<ApiInfoTemplate> {
  const data = await request()<ApiInfoTemplate>('/info-nodes/template');
  return {
    id: data?.id ?? 'default',
    name: data?.name ?? '项目详情模板',
    nodes: Array.isArray(data?.nodes) ? data.nodes : [],
    updated_at: data?.updated_at ?? null,
    updated_by: data?.updated_by ?? null,
    project_count: data?.project_count ?? 0,
    source: data?.source ?? 'db',
  };
}

/** 保存模板并同步到所有项目；dryRun=true 只预览影响不写库（校验失败后端返回 400） */
export async function saveInfoTemplateApi(
  nodes: ApiInfoTemplateNode[],
  dryRun: boolean,
): Promise<ApiInfoTemplateSyncResult> {
  const data = await request()<ApiInfoTemplateSyncResult>('/info-nodes/template', {
    method: 'POST',
    body: JSON.stringify({ nodes, dry_run: dryRun }),
  });
  return {
    dry_run: !!data?.dry_run,
    projects: data?.projects ?? 0,
    changed_projects: data?.changed_projects ?? 0,
    added: data?.added ?? 0,
    updated: data?.updated ?? 0,
    deleted: data?.deleted ?? 0,
    details: Array.isArray(data?.details) ? data.details : [],
    failed_projects: Array.isArray(data?.failed_projects) ? data.failed_projects : [],
    updated_at: data?.updated_at,
    updated_by: data?.updated_by,
  };
}
