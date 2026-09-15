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
  fetchInfoTree,
  importInfoTreeApi,
  moveInfoNodeApi,
  updateInfoNodeApi,
  type ApiInfoNode,
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

// —— 预设信息树模板 ——
// 来源：用户提供的《项目信息树形图》思维导图（frontend/src/pages/项目信息树形图.png），
// 图中共 13 个一级分支，根节点顺序按需求指定（基础信息 → … → 项目定制）。
// 图中第 5 层的「可选值清单」不建节点，改为挂在末级节点上的「下拉选择」内容形式（选项即图中清单），
// 保证整棵树不超过 4 层；末级节点其余为「文字」。
// 用途：空树项目的「按预设模板」初始化（走 import 接口写入后端）。
// 注意：后端新建项目时按其模板目录（config/project_templates/default.yaml）初始化信息树，
// 与本模板不是同一份内容；两边对齐时应以后端 YAML 为准，本常量届时可删除。

/** 模板节点定义（仅本文件内部使用） */
interface TemplateSpec {
  title: string;
  /** 有值即该节点为「下拉选择」，选项来自图中列出的可选值清单 */
  options?: string[];
  children?: TemplateSpec[];
}

const YES_NO = ['是', '否'];

const PROJECT_INFO_TEMPLATE: TemplateSpec[] = [
  {
    title: '基础信息',
    children: [
      { title: '客户信息' },
      { title: '订单信息', children: [{ title: 'ERP' }] },
      { title: '评审信息' },
      {
        title: '项目区域/地点',
        options: ['大陆 China Mainland', '亚洲 Asia', '欧洲 Europe', '北美 North America', '南美 South America', '非洲 Africa'],
      },
      {
        title: '项目类型',
        options: ['受关注项目', '大客户项目', '展会/演示项目', '展厅项目', 'PK 项目', '试点项目', '试用项目', '内部/测试项目'],
      },
      { title: '进厂要求', children: [{ title: '着装' }, { title: '预约信息' }] },
    ],
  },
  {
    title: '硬件',
    children: [
      {
        title: '车辆',
        children: [
          { title: '车型1', children: [{ title: '数量' }] },
          { title: '车型2', children: [{ title: '数量' }] },
        ],
      },
      { title: '载具类型', options: ['托盘', '料笼', '料架', '料车'] },
    ],
  },
  {
    title: '车端软件',
    children: [
      { title: '控制器品牌', options: ['自研', '睿芯行', '利科钛', '海康', '华睿', '中兴', '科聪'] },
      { title: '软件版本' },
      { title: '数据同步方式' },
      { title: '是否已与 USP 对接过', options: YES_NO },
    ],
  },
  {
    title: '调度软件',
    children: [
      {
        title: '版本',
        children: [{ title: '子模块', children: [{ title: '调度配置' }, { title: '通用配置' }] }],
      },
      // 图中拼写即「lincense」，按原图保留
      { title: 'lincense', children: [{ title: '到期时间' }, { title: '续期记录' }] },
    ],
  },
  {
    title: '网络信息',
    children: [
      { title: '外网' },
      { title: '公网ip', children: [{ title: '服务器参数配置' }] },
      {
        title: '远程方式',
        children: [
          { title: 'SSH', children: [{ title: 'IP' }, { title: '端口' }] },
          { title: 'Todesk', children: [{ title: '远程码' }, { title: '密码', options: ['动态密码', '静态密码'] }] },
          { title: 'AngDek', children: [{ title: '远程码' }, { title: '密码', options: ['动态密码', '静态密码'] }] },
        ],
      },
    ],
  },
  {
    title: '服务器部署',
    children: [
      { title: '中力服务器', children: [{ title: '是否与其他系统共用', options: YES_NO }] },
      { title: '客户服务器', children: [{ title: '是否与其他系统共用', options: YES_NO }] },
      { title: '云服务器', children: [{ title: '是否与其他系统共用', options: YES_NO }] },
    ],
  },
  {
    title: '环境',
    children: [
      {
        title: '地图布局',
        children: [
          { title: 'CAD源文件', children: [{ title: '库位' }, { title: '库区形式/数量' }] },
          { title: 'AGV路线动线' },
          { title: '通道与托盘间距尺寸' },
        ],
      },
      {
        title: '外设',
        children: [
          { title: '电梯', children: [{ title: '厂家品牌（协议）', options: ['Modbus Tcp', 'ST', '中力PLC'] }] },
          { title: '自动门' },
          { title: '呼叫器' },
          { title: '输送线/辊筒线' },
          { title: '红绿灯' },
          { title: '机械臂' },
          { title: '码垛机/叠盘机' },
          { title: '缠膜机' },
          { title: '光电' },
          { title: '无外设声明' },
          { title: '其他（自定义）' },
        ],
      },
    ],
  },
  {
    title: '业务系统',
    children: [
      {
        title: '系统',
        children: [
          { title: 'DAS' },
          { title: '客户WMS' },
          { title: '客户MES/ERP' },
          { title: '客户系统', children: [{ title: '其他上层系统' }] },
          // 图中「接口协议」下还挂着「接口标准 / 是否对接」（第 5 层），受 4 层上限约束展开为同级末级节点
          {
            title: '数字孪生',
            children: [
              { title: '接口协议' },
              { title: '接口标准' },
              { title: '是否对接', options: YES_NO },
              { title: 'ip/url' },
            ],
          },
          { title: 'PDA' },
          { title: '平板' },
        ],
      },
    ],
  },
  {
    title: '业务流程',
    children: [
      {
        title: '搬运场景',
        children: [
          { title: '搬运类型', options: ['线边搬运', '仓储搬运', '电梯接驳', '室外搬运'] },
          { title: '装卸' },
          { title: '分拣' },
        ],
      },
      { title: '节拍', children: [{ title: '节拍' }, { title: '效率要求数值/无效率声明' }] },
      { title: '物料类型' },
    ],
  },
  {
    title: '人员信息',
    children: [
      { title: '客户对象' },
      { title: '实施' },
      { title: '车端' },
      { title: '调度' },
      { title: '业务' },
      { title: '项目经理' },
      { title: '销售' },
      { title: '售前' },
      { title: '集成商' },
    ],
  },
  {
    title: '项目特性',
    children: [
      {
        title: '风险点',
        options: ['数据同步错误', '公司评审不通过', '缺前置承接', '高风险承接', '中风险承接', '低风险承接'],
      },
      { title: '注意事项' },
      {
        title: '时间线',
        children: [
          {
            title: '大节点',
            options: ['售前方案', '签单洽谈', '已签合同', '出厂测试', '即将进场', '延期进场', '正在实施', '实施暂停', '实施运行', '试运行中', '验收运营', '项目结束'],
          },
          { title: '小节点' },
        ],
      },
    ],
  },
  { title: '项目配置', children: [{ title: '识别' }] },
  {
    title: '项目定制',
    children: [{ title: '接口' }, { title: '大屏', children: [{ title: '页面' }] }, { title: '功能' }],
  },
];

/** 按模板深度优先展开成 import 接口的递归节点（每节点生成新 UUID，多次导入不撞主键） */
function buildTemplateImportNodes(specs: TemplateSpec[]): ApiInfoTreeImportNode[] {
  return specs.map((spec, index) => {
    const node: ApiInfoTreeImportNode = {
      id: genId(),
      title: spec.title,
      content_type: spec.options ? 'select' : 'text',
      value: spec.options ? encodeInfoValue({ options: [...spec.options], selected: '' }) : null,
      sort_order: index,
    };
    if (spec.children?.length) node.children = buildTemplateImportNodes(spec.children);
    return node;
  });
}

/** 整棵预设信息树（供空树项目「按预设模板」初始化，走 import 接口写入后端） */
export function buildTemplateImportTree(): ApiInfoTreeImportNode[] {
  return buildTemplateImportNodes(PROJECT_INFO_TEMPLATE);
}

const selectedKey = (code: string) => `project-info-tree:selected:${code}`;
const collapsedKey = (code: string) => `project-info-tree:collapsed:${code}`;
const cardCollapsedKey = (code: string) => `project-info-tree:card-collapsed:${code}`;

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

/** 批量替换整树（文件导入 / 按预设模板初始化）；返回写入的节点数 */
export async function importInfoTree(projectId: string, input: unknown): Promise<number> {
  return importInfoTreeApi(projectId, normalizeImportNodes(input));
}

/** 归一化导入内容：接受数组、{nodes:[…]} 或 {info_nodes:[…]}；补 id、序号与缺省字段 */
export function normalizeImportNodes(input: unknown): ApiInfoTreeImportNode[] {
  const list = Array.isArray(input)
    ? input
    : Array.isArray((input as { nodes?: unknown } | null)?.nodes)
      ? (input as { nodes: unknown[] }).nodes
      : Array.isArray((input as { info_nodes?: unknown } | null)?.info_nodes)
        ? (input as { info_nodes: unknown[] }).info_nodes
        : null;
  if (!list) throw new Error('导入内容需要是信息树数组（或含 nodes / info_nodes 字段的对象）');
  return normalizeImportLevel(list as Record<string, unknown>[]);
}

function normalizeImportLevel(items: Record<string, unknown>[]): ApiInfoTreeImportNode[] {
  return items.map((item, index) => {
    const contentType = typeof item.content_type === 'string' ? item.content_type : 'text';
    const node: ApiInfoTreeImportNode = {
      id: typeof item.id === 'string' && item.id ? item.id : genId(),
      title: typeof item.title === 'string' && item.title ? item.title : '未命名节点',
      content_type: contentType,
      value: normalizeImportValue(item.value),
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
