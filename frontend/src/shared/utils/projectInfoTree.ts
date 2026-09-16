// 项目信息树（项目详细信息）——前端数据层。
//
// 后端接口已接入（backend/app/modules/admin/api/info_nodes.py，契约见
// backend/docs/project_ext_info_info_nodes_api.md 第五节）：
// - 树的读写全部走 /api/admin/info-nodes/*，逐节点 CRUD（不做整树读改写）；
// - 本文件把「后端行（value 为 TEXT 字符串）」与「页面用的结构化节点」互相转换：
//   text 存原始字符串，select / file / image 存 JSON 字符串，读取时按 content_type 解码；
// - 仍存本机的只剩个人界面偏好（标签筛选、折叠状态、卡片折叠），与设计稿一致，不属于共享数据。

import {
  createInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoNodeChangeSummaryApi,
  fetchInfoNodeChangesApi,
  fetchInfoTree,
  importInfoTemplateApi,
  importInfoTreeApi,
  moveInfoNodeApi,
  updateInfoNodeApi,
  type ApiInfoNode,
  type ApiInfoNodeChange,
  type ApiInfoNodeUpdate,
  type ApiInfoTreeImportNode,
} from '@/api/infoNodes';

export type ProjectInfoContentType = 'text' | 'select' | 'file' | 'image';

/** 下拉选择节点的内容值（选项由用户自行增删） */
export interface ProjectInfoSelectValue {
  selected: string;
  options: string[];
}

/** 文件/图片节点的内容值（resource_id 指向资源管理服务的真实文件） */
export interface ProjectInfoFileValue {
  name: string;
  resource_id: number;
  size?: number;
}

export interface ProjectInfoNode {
  id: string;
  project_id: string;
  parent_id: string | null;
  title: string;
  content_type: ProjectInfoContentType;
  /** 结构化值：text → string；select → {selected, options}；file/image → {name, resource_id, size} */
  value: unknown;
  sort_order: number;
  created_at: string;
  /** 后端最后更新时间（字符串时间戳），本地乐观更新时为最近一次成功写入的值 */
  updated_at?: string;
}

/** 信息树最大层级（与设计稿一致：第 4 层不可再新增/下挂） */
export const PROJECT_INFO_MAX_DEPTH = 4;

// —— 预设信息树模板（已下沉到后端，前端不再维护副本） ——
// 唯一来源：backend/app/config/project_templates/{project_type}.yaml（缺省 default.yaml）。
// 后端在「新建项目」与 POST /info-nodes/projects/{id}/import-template（按模板初始化）时实例化，
// 前端只负责触发，避免前后端两套模板各自漂移。

const selectedKey = (code: string) => `project-info-tree:selected:${code}`;
const collapsedKey = (code: string) => `project-info-tree:collapsed:${code}`;
const cardCollapsedKey = (code: string) => `project-info-tree:card-collapsed:${code}`;
const historySeenKey = (code: string, user: string) => `project-info-tree:history-seen:${code}:${user}`;

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage 不可用（隐私模式等）时静默降级为仅内存态，不阻断交互
  }
}

function genId(): string {
  try {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  } catch {
    /* ignore */
  }
  return `n-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

// —— 后端行 <-> 页面节点 ——

function tryParseJson(raw: string | null): unknown {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** 后端 TEXT → 结构化值：text 原样；select 归一为 {selected, options}；file/image 归一为对象 */
function decodeInfoValue(contentType: string, raw: string | null): unknown {
  if (contentType === 'select') {
    const parsed = tryParseJson(raw);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const data = parsed as Partial<ProjectInfoSelectValue>;
      return {
        selected: typeof data.selected === 'string' ? data.selected : '',
        options: Array.isArray(data.options) ? data.options.filter((item): item is string => typeof item === 'string') : [],
      };
    }
    return { selected: '', options: [] };
  }
  if (contentType === 'file' || contentType === 'image') {
    const parsed = tryParseJson(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  }
  // text 及后端未来扩展的其它内容形式：按字符串透传
  return raw ?? '';
}

/** 结构化值 → 后端 TEXT：字符串原样存取（可读），其余（下拉/文件/标题备选项）存 JSON 字符串 */
export function encodeInfoValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return null;
  }
}

function decodeInfoNode(raw: ApiInfoNode, parentId: string | null): ProjectInfoNode {
  return {
    id: raw.id,
    project_id: raw.project_id,
    parent_id: parentId,
    title: raw.title,
    content_type: (raw.content_type || 'text') as ProjectInfoContentType,
    value: decodeInfoValue(raw.content_type || 'text', raw.value),
    sort_order: raw.sort_order,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  };
}

/** 接口返回的递归树 → 扁平节点（父 id 以树的层级为准，避免后端脏 parent_id 影响渲染） */
export function flattenInfoTree(roots: ApiInfoNode[]): ProjectInfoNode[] {
  const flat: ProjectInfoNode[] = [];
  const walk = (items: ApiInfoNode[], parentId: string | null) => {
    items.forEach((item) => {
      flat.push(decodeInfoNode(item, parentId));
      if (item.children?.length) walk(item.children, item.id);
    });
  };
  walk(roots, null);
  return flat;
}

// —— 节点读写（真实后端 /api/admin/info-nodes/*，逐节点 CRUD） ——

/** 读取某项目的全部信息节点（扁平，按 sort_order 升序） */
export async function loadInfoNodes(projectId: string): Promise<ProjectInfoNode[]> {
  const tree = await fetchInfoTree(projectId);
  return flattenInfoTree(tree).sort((a, b) => a.sort_order - b.sort_order);
}

/** 在 parentId 下新增节点（id 由前端生成；title 先占位，进入编辑态由用户改名） */
export async function createInfoNode(
  projectId: string,
  parentId: string | null,
  sortOrder: number,
  title = '未命名节点',
): Promise<ProjectInfoNode> {
  const raw = await createInfoNodeApi(projectId, {
    id: genId(),
    parent_id: parentId,
    title,
    content_type: 'text',
    value: '',
    sort_order: sortOrder,
  });
  return decodeInfoNode(raw, parentId);
}

/** 更新节点（title / content_type / value / sort_order） */
export async function updateInfoNode(
  node: ProjectInfoNode,
  updates: { title?: string; content_type?: ProjectInfoContentType; value?: unknown; sort_order?: number },
): Promise<ProjectInfoNode> {
  const payload: ApiInfoNodeUpdate = {};
  if (updates.title !== undefined) payload.title = updates.title;
  if (updates.content_type !== undefined) payload.content_type = updates.content_type;
  if (updates.value !== undefined) payload.value = encodeInfoValue(updates.value);
  if (updates.sort_order !== undefined) payload.sort_order = updates.sort_order;
  const raw = await updateInfoNodeApi(node.id, payload);
  return decodeInfoNode(raw, node.parent_id);
}

/** 移动节点（换父 + 同级排序） */
export async function moveInfoNode(
  node: ProjectInfoNode,
  parentId: string | null,
  sortOrder: number,
): Promise<ProjectInfoNode> {
  const raw = await moveInfoNodeApi(node.id, parentId, sortOrder);
  return decodeInfoNode(raw, parentId);
}

/** 删除节点及其整棵子树 */
export async function deleteInfoNode(nodeId: string): Promise<void> {
  await deleteInfoNodeApi(nodeId);
}

/** 批量替换整树（文件导入）；返回写入的节点数 */
export async function importInfoTree(projectId: string, input: unknown): Promise<number> {
  return importInfoTreeApi(projectId, normalizeImportNodes(input));
}

/** 按后端模板重建整树（空树项目的「按预设模板初始化」）；
 *  模板来自 project_type → project_templates/*.yaml，与新建项目同一份定义 */
export async function importInfoTemplate(projectId: string): Promise<number> {
  return importInfoTemplateApi(projectId);
}

// —— 编辑历史（节点操作记录）：后端全量落库，「已读水位」存本机（每个人各自的未读状态） ——

/** 某节点的编辑历史：自身操作 + 其直接子节点的删除记录（最新在前） */
export async function loadInfoNodeChanges(projectId: string, nodeId: string): Promise<ApiInfoNodeChange[]> {
  return fetchInfoNodeChangesApi(projectId, nodeId);
}

/** 各节点最新记录的 id {节点id: 记录id}（删除记录计入其上级节点） */
export async function loadHistoryLatest(projectId: string): Promise<Record<string, string>> {
  return fetchInfoNodeChangeSummaryApi(projectId);
}

/** 已读水位（该节点看过的最后一条记录 id）按「项目 + 登录用户」存本机：
 *  每个没点开过历史的用户，自己看到小红点 */
export function loadHistorySeen(projectCode: string, username = ''): Record<string, string> {
  return readJson<Record<string, string>>(historySeenKey(projectCode, username), {});
}

export function saveHistorySeen(projectCode: string, seen: Record<string, string>, username = '') {
  writeJson(historySeenKey(projectCode, username), seen);
}

/**
 * 有「新变动」的节点（历史按钮上的小红点）：
 * 该节点最新记录 id 与本机已读水位不一致（或从没点开过）即未读；没有记录的节点不出红点。
 * 只有点开过该节点的历史才会推进水位——包括自己刚保存的改动。
 * 记录 id 是后端时间有序的 UUIDv7：只比相等，同秒内的新记录也不会漏（时间戳只到秒）。
 */
export function unseenHistoryNodes(
  latest: Record<string, string>,
  seen: Record<string, string>,
): Set<string> {
  const result = new Set<string>();
  Object.entries(latest ?? {}).forEach(([nodeId, latestId]) => {
    if (!latestId) return;
    if (seen?.[nodeId] !== latestId) result.add(nodeId);
  });
  return result;
}

/** 归一化导入内容：接受节点数组、{nodes:[…]}、{info_nodes:[…]}，
 *  或「标题 → 内容」紧凑映射（backend/app/config/project_templates/tmp.json 的写法）。
 *  统一补 id、序号与缺省字段，并把 options 清单转成 select 节点。 */
export function normalizeImportNodes(input: unknown): ApiInfoTreeImportNode[] {
  if (Array.isArray(input)) return normalizeImportLevel(input as Record<string, unknown>[]);
  const container = input as { nodes?: unknown; info_nodes?: unknown } | null;
  if (Array.isArray(container?.nodes)) return normalizeImportLevel(container.nodes as Record<string, unknown>[]);
  const infoNodes = container?.info_nodes;
  if (Array.isArray(infoNodes)) return normalizeImportLevel(infoNodes as Record<string, unknown>[]);
  if (infoNodes && typeof infoNodes === 'object') {
    return normalizeImportLevel(mapFormToLevel(infoNodes as Record<string, unknown>));
  }
  throw new Error('导入内容需要是信息树数组（或含 nodes / info_nodes 字段的对象、标题:内容 映射）');
}

/** 紧凑映射 → 节点数组：""/文字 → 文字节点；[选项…] → 下拉节点；{…} → 子节点 */
function mapFormToLevel(map: Record<string, unknown>): Record<string, unknown>[] {
  return Object.entries(map).map(([title, value], index) => {
    if (Array.isArray(value)) {
      return { title, sort_order: index, content_type: 'select', options: value };
    }
    if (value && typeof value === 'object') {
      return { title, sort_order: index, children: mapFormToLevel(value as Record<string, unknown>) };
    }
    return { title, sort_order: index, value: typeof value === 'string' ? value : '' };
  });
}

function normalizeImportLevel(items: Record<string, unknown>[]): ApiInfoTreeImportNode[] {
  return items.map((item, index) => {
    const options = Array.isArray(item.options)
      ? (item.options as unknown[]).filter((option): option is string => typeof option === 'string')
      : [];
    const contentType =
      typeof item.content_type === 'string' ? item.content_type : options.length ? 'select' : 'text';
    const node: ApiInfoTreeImportNode = {
      id: typeof item.id === 'string' && item.id ? item.id : genId(),
      title: typeof item.title === 'string' && item.title ? item.title : '未命名节点',
      content_type: contentType,
      value: normalizeImportValue(item.value ?? (options.length ? { selected: '', options } : null)),
      sort_order: typeof item.sort_order === 'number' ? item.sort_order : index,
    };
    const children = Array.isArray(item.children) ? (item.children as Record<string, unknown>[]) : [];
    if (children.length) node.children = normalizeImportLevel(children);
    return node;
  });
}

/** 导入值：字符串按后端 TEXT 原样保留，结构化值（{selected, options} 等）转 JSON 字符串 */
function normalizeImportValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'string') return value;
  return encodeInfoValue(value);
}

/** 乐观更新：本地替换单个节点的字段（不落盘，调用方随后发 API，失败时回滚） */
export function patchInfoNode(
  nodes: ProjectInfoNode[],
  id: string,
  updates: Partial<Pick<ProjectInfoNode, 'title' | 'content_type' | 'value' | 'parent_id' | 'sort_order'>>,
): ProjectInfoNode[] {
  return nodes.map((node) => (node.id === id ? { ...node, ...updates } : node));
}

/** 删除节点及其全部后代节点 */
export function removeInfoNode(nodes: ProjectInfoNode[], id: string): ProjectInfoNode[] {
  const doomed = new Set<string>([id]);
  let grew = true;
  while (grew) {
    grew = false;
    nodes.forEach((node) => {
      if (node.parent_id && doomed.has(node.parent_id) && !doomed.has(node.id)) {
        doomed.add(node.id);
        grew = true;
      }
    });
  }
  return nodes.filter((node) => !doomed.has(node.id));
}

// —— 标签筛选 / 折叠偏好（个人偏好，存本机；与设计稿一致） ——

export function loadSelectedTags(projectCode: string): Set<string> {
  return new Set(readJson<string[]>(selectedKey(projectCode), []));
}

export function saveSelectedTags(projectCode: string, ids: Set<string>) {
  writeJson(selectedKey(projectCode), [...ids]);
}

export function loadCollapsedIds(projectCode: string): Set<string> {
  return new Set(readJson<string[]>(collapsedKey(projectCode), []));
}

export function saveCollapsedIds(projectCode: string, ids: Set<string>) {
  writeJson(collapsedKey(projectCode), [...ids]);
}

export function loadCardCollapsed(projectCode: string): boolean {
  return localStorage.getItem(cardCollapsedKey(projectCode)) === '1';
}

export function saveCardCollapsed(projectCode: string, collapsed: boolean) {
  try {
    localStorage.setItem(cardCollapsedKey(projectCode), collapsed ? '1' : '0');
  } catch {
    /* ignore */
  }
}

/** 附件大小展示（B / KB / MB） */
export function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

// —— 区域细分字段联动（项目区域/地点 下的三个候选字段按所选区域显隐） ——

/** 「大陆(China Mainland)」选项：选中它才显示省份/地区 */
export const REGION_MAINLAND = '大陆(China Mainland)';
/** 大陆下的细分字段 */
const MAINLAND_DETAIL_TITLES = ['省份', '地区'];
/** 其它区域下的细分字段 */
const OVERSEAS_DETAIL_TITLE = '具体国家';

/** 同级的「区域」下拉：按选项里是否含大陆判定，避免改标题后联动失效 */
function regionDriver(siblings: ProjectInfoNode[]): ProjectInfoNode | undefined {
  return siblings.find((item) => {
    if (item.content_type !== 'select') return false;
    const options = (item.value as Partial<ProjectInfoSelectValue> | null)?.options;
    return Array.isArray(options) && options.includes(REGION_MAINLAND);
  });
}

/**
 * 区域细分字段当前是否显示（节点本身仍在数据里，只是不渲染——切回大陆时原值还在）：
 * - 未选择区域 → 省份/地区/具体国家都不显示；
 * - 选中大陆 → 只显示省份/地区；
 * - 选中其它区域 → 只显示具体国家；
 * - 与区域无关的节点（含用户自建字段）一律显示。
 */
export function isInfoNodeVisible(node: ProjectInfoNode, siblings: ProjectInfoNode[]): boolean {
  const driver = regionDriver(siblings);
  if (!driver || driver.id === node.id) return true;
  const selected = (driver.value as Partial<ProjectInfoSelectValue> | null)?.selected ?? '';
  if (MAINLAND_DETAIL_TITLES.includes(node.title)) return selected === REGION_MAINLAND;
  if (node.title === OVERSEAS_DETAIL_TITLE) return !!selected && selected !== REGION_MAINLAND;
  return true;
}

/** 过滤掉当前不该显示的节点（完整度统计等按「看得见的字段」算） */
export function visibleInfoNodes(nodes: ProjectInfoNode[]): ProjectInfoNode[] {
  const byParent = new Map<string | null, ProjectInfoNode[]>();
  nodes.forEach((node) => {
    const list = byParent.get(node.parent_id) ?? [];
    list.push(node);
    byParent.set(node.parent_id, list);
  });
  return nodes.filter((node) => isInfoNodeVisible(node, byParent.get(node.parent_id) ?? []));
}

// —— 信息完整度（对照原型 node-completeness：统计每个一级标签下末级节点的填写情况） ——

export interface TagCompleteness {
  total: number;
  empty: number;
  incomplete: boolean;
}

function isEmptyLeaf(node: ProjectInfoNode): boolean {
  if (node.content_type === 'select') {
    return !(node.value as ProjectInfoSelectValue | null)?.selected;
  }
  if (node.content_type === 'file' || node.content_type === 'image') {
    return !(node.value as ProjectInfoFileValue | null)?.name;
  }
  return !(typeof node.value === 'string' && node.value.trim());
}

/** 只要一级标签下存在空的末级节点即视为信息不全（卡片标签上显示「!」角标） */
export function computeInfoCompleteness(nodes: ProjectInfoNode[]): Map<string, TagCompleteness> {
  const byParent = new Map<string | null, ProjectInfoNode[]>();
  nodes.forEach((node) => {
    const list = byParent.get(node.parent_id) ?? [];
    list.push(node);
    byParent.set(node.parent_id, list);
  });

  const result = new Map<string, TagCompleteness>();
  const walk = (node: ProjectInfoNode, acc: { total: number; empty: number }) => {
    const children = byParent.get(node.id) ?? [];
    if (children.length === 0) {
      acc.total += 1;
      if (isEmptyLeaf(node)) acc.empty += 1;
      return;
    }
    children.forEach((child) => walk(child, acc));
  };

  (byParent.get(null) ?? []).forEach((root) => {
    const acc = { total: 0, empty: 0 };
    walk(root, acc);
    result.set(root.id, { total: acc.total, empty: acc.empty, incomplete: acc.total > 0 && acc.empty > 0 });
  });

  return result;
}
