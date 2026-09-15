import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  PROJECT_INFO_MAX_DEPTH,
  buildTemplateImportTree,
  computeInfoCompleteness,
  createInfoNode,
  deleteInfoNode,
  encodeInfoValue,
  flattenInfoTree,
  importInfoTree,
  loadInfoNodes,
  moveInfoNode,
  normalizeImportNodes,
  patchInfoNode,
  removeInfoNode,
  updateInfoNode,
  type ProjectInfoNode,
} from '../projectInfoTree';
import {
  createInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoTree,
  importInfoTreeApi,
  moveInfoNodeApi,
  updateInfoNodeApi,
  type ApiInfoNode,
  type ApiInfoTreeImportNode,
} from '@/api/infoNodes';

vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
}));

/** 需求指定的根节点顺序（来自《项目信息树形图》） */
const ROOT_ORDER = [
  '基础信息', '硬件', '车端软件', '调度软件', '网络信息', '服务器部署',
  '环境', '业务系统', '业务流程', '人员信息', '项目特性', '项目配置', '项目定制',
];

const TS = '2026-09-14 10:00:00';

/** 构造后端行（默认值可按需覆盖） */
function apiNode(partial: Partial<ApiInfoNode> & { id: string }): ApiInfoNode {
  return {
    project_id: 'CODE-A',
    parent_id: null,
    title: '未命名节点',
    content_type: 'text',
    value: null,
    sort_order: 0,
    created_at: TS,
    updated_at: TS,
    ...partial,
  };
}

/** 展平 import 用的递归模板节点，记录父子关系 */
function flattenImport(
  list: ApiInfoTreeImportNode[],
  parent: ApiInfoTreeImportNode | null = null,
  out: Array<{ node: ApiInfoTreeImportNode; parent: ApiInfoTreeImportNode | null }> = [],
) {
  list.forEach((node) => {
    out.push({ node, parent });
    if (node.children?.length) flattenImport(node.children, node, out);
  });
  return out;
}

function depthInImport(entry: { node: ApiInfoTreeImportNode; parent: ApiInfoTreeImportNode | null }, all: ReturnType<typeof flattenImport>): number {
  let depth = 1;
  let current = entry.parent;
  while (current) {
    depth += 1;
    current = all.find((item) => item.node === current)?.parent ?? null;
  }
  return depth;
}

const parseValue = (node: { value?: string | null }) => JSON.parse(node.value ?? 'null');
const selectOf = (node: ProjectInfoNode) => node.value as { selected: string; options: string[] };

describe('预设信息树模板（import 用）', () => {
  it('13 个一级节点，顺序与需求一致', () => {
    const tree = buildTemplateImportTree();
    expect(tree.map((node) => node.title)).toEqual(ROOT_ORDER);
  });

  it('整棵树不超过 4 层，每个节点都有 id', () => {
    const all = flattenImport(buildTemplateImportTree());
    const ids = all.map((entry) => entry.node.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every(Boolean)).toBe(true);
    all.forEach((entry) => {
      expect(depthInImport(entry, all), `节点「${entry.node.title}」`).toBeLessThanOrEqual(PROJECT_INFO_MAX_DEPTH);
    });
  });

  it('下拉节点的选项即图中可选值清单，初始未选择', () => {
    const all = flattenImport(buildTemplateImportTree());
    const find = (title: string) => all.find((entry) => entry.node.title === title)!.node;

    const region = find('项目区域/地点');
    expect(region.content_type).toBe('select');
    expect(parseValue(region)).toEqual({
      selected: '',
      options: ['大陆 China Mainland', '亚洲 Asia', '欧洲 Europe', '北美 North America', '南美 South America', '非洲 Africa'],
    });
    expect(parseValue(find('项目类型')).options).toContain('PK 项目');
    expect(parseValue(find('是否与其他系统共用')).options).toEqual(['是', '否']);

    all.forEach(({ node }) => {
      if (node.content_type === 'select') {
        expect(parseValue(node).options.length, `节点「${node.title}」`).toBeGreaterThan(0);
      }
    });
  });
});

describe('后端行 <-> 页面节点（编解码）', () => {
  it('text 原样透传（含看起来像 JSON 的内容），select / file 按 content_type 解码', () => {
    const tree: ApiInfoNode[] = [
      apiNode({
        id: 'r1',
        title: '基础信息',
        sort_order: 0,
        children: [
          apiNode({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '{"a":1}', sort_order: 0 }),
          apiNode({
            id: 'c2', parent_id: 'r1', title: '项目类型', content_type: 'select', sort_order: 1,
            value: JSON.stringify({ selected: 'PK 项目', options: ['PK 项目', '试点项目'] }),
          }),
          apiNode({ id: 'c3', parent_id: 'r1', title: '坏数据', content_type: 'select', value: 'not-json', sort_order: 2 }),
          apiNode({ id: 'c4', parent_id: 'r1', title: '附件', content_type: 'file', sort_order: 3, value: JSON.stringify({ name: 'a.pdf', resource_id: 7, size: 100 }) }),
        ],
      }),
    ];
    const flat = flattenInfoTree(tree);
    const byId = new Map(flat.map((node) => [node.id, node]));

    expect(byId.get('c1')!.value).toBe('{"a":1}');
    expect(selectOf(byId.get('c2')!)).toEqual({ selected: 'PK 项目', options: ['PK 项目', '试点项目'] });
    expect(selectOf(byId.get('c3')!)).toEqual({ selected: '', options: [] });
    expect(byId.get('c4')!.value).toEqual({ name: 'a.pdf', resource_id: 7, size: 100 });
    // 空 value 的 text 节点按空字符串处理
    expect(byId.get('r1')!.value).toBe('');
    // parent_id 以树的层级为准
    expect(byId.get('c2')!.parent_id).toBe('r1');
    expect(byId.get('r1')!.parent_id).toBeNull();
  });

  it('encodeInfoValue：字符串原样，null 归 null，结构化值存 JSON 字符串', () => {
    expect(encodeInfoValue('abc')).toBe('abc');
    expect(encodeInfoValue(null)).toBeNull();
    expect(encodeInfoValue({ selected: '是', options: ['是', '否'] })).toBe('{"selected":"是","options":["是","否"]}');
  });
});

describe('节点 CRUD（走 /api/admin/info-nodes）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('loadInfoNodes 读取整树并展平、按 sort_order 升序', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValue([
      apiNode({
        id: 'r1', title: '基础信息', sort_order: 0,
        children: [
          apiNode({ id: 'c2', parent_id: 'r1', title: '第二', sort_order: 1 }),
          apiNode({ id: 'c1', parent_id: 'r1', title: '第一', sort_order: 0 }),
        ],
      }),
    ]);
    const nodes = await loadInfoNodes('P1');
    expect(fetchInfoTree).toHaveBeenCalledWith('P1');
    expect(nodes.map((node) => node.id)).toEqual(['r1', 'c1', 'c2']);
  });

  it('createInfoNode 由前端生成 id 并调用创建接口', async () => {
    vi.mocked(createInfoNodeApi).mockImplementation(async (_projectId, payload) =>
      apiNode({ id: payload.id, title: payload.title ?? '', parent_id: payload.parent_id ?? null, sort_order: payload.sort_order ?? 0 }),
    );
    const created = await createInfoNode('P1', 'parent-1', 3, '新节点');
    expect(createInfoNodeApi).toHaveBeenCalledWith('P1', expect.objectContaining({
      id: expect.any(String), parent_id: 'parent-1', title: '新节点', content_type: 'text', sort_order: 3,
    }));
    expect(created.parent_id).toBe('parent-1');
    expect(created.title).toBe('新节点');
  });

  it('updateInfoNode 把结构化值编码成 TEXT 字符串提交', async () => {
    const node = { id: 'n1', project_id: 'P1', parent_id: null, title: '项目类型', content_type: 'select' as const, value: { selected: '', options: [] }, sort_order: 0, created_at: TS };
    vi.mocked(updateInfoNodeApi).mockResolvedValue(
      apiNode({ id: 'n1', content_type: 'select', value: JSON.stringify({ selected: '试点项目', options: ['试点项目'] }) }),
    );
    const updated = await updateInfoNode(node, { value: { selected: '试点项目', options: ['试点项目'] } });
    expect(updateInfoNodeApi).toHaveBeenCalledWith('n1', { value: '{"selected":"试点项目","options":["试点项目"]}' });
    expect(selectOf(updated).selected).toBe('试点项目');
  });

  it('moveInfoNode / deleteInfoNode 调用对应接口', async () => {
    const node = { id: 'n1', project_id: 'P1', parent_id: null, title: 'x', content_type: 'text' as const, value: '', sort_order: 0, created_at: TS };
    vi.mocked(moveInfoNodeApi).mockResolvedValue(apiNode({ id: 'n1', parent_id: 'p2', sort_order: 1 }));
    vi.mocked(deleteInfoNodeApi).mockResolvedValue(undefined);

    const moved = await moveInfoNode(node, 'p2', 1);
    expect(moveInfoNodeApi).toHaveBeenCalledWith('n1', 'p2', 1);
    expect(moved.parent_id).toBe('p2');

    await deleteInfoNode('n1');
    expect(deleteInfoNodeApi).toHaveBeenCalledWith('n1');
  });

  it('importInfoTree 归一化后调用导入接口并返回写入数量', async () => {
    vi.mocked(importInfoTreeApi).mockResolvedValue(2);
    const imported = await importInfoTree('P1', { nodes: [{ title: '父', children: [{ title: '子' }] }] });
    expect(importInfoTreeApi).toHaveBeenCalledWith('P1', [
      expect.objectContaining({ title: '父', children: [expect.objectContaining({ title: '子' })] }),
    ]);
    expect(imported).toBe(2);
  });
});

describe('导入内容归一化', () => {
  it('接受数组 / {nodes} / {info_nodes} 三种格式', () => {
    const raw = [{ title: 'A' }];
    expect(normalizeImportNodes(raw)[0].title).toBe('A');
    expect(normalizeImportNodes({ nodes: raw })[0].title).toBe('A');
    expect(normalizeImportNodes({ info_nodes: raw })[0].title).toBe('A');
  });

  it('补 id / 序号 / 缺省字段，保留字符串值，结构化值转 JSON 字符串', () => {
    const [node] = normalizeImportNodes([
      { title: '下拉', content_type: 'select', value: { selected: '是', options: ['是'] } },
      { title: '文字', value: '原文' },
    ]);
    expect(node.id).toBeTruthy();
    expect(node.sort_order).toBe(0);
    expect(node.value).toBe('{"selected":"是","options":["是"]}');
  });

  it('无法识别的格式直接抛错', () => {
    expect(() => normalizeImportNodes({ foo: 1 })).toThrow();
  });
});

describe('本地纯函数', () => {
  const base: ProjectInfoNode[] = [
    { id: 'a', project_id: 'P1', parent_id: null, title: 'A', content_type: 'text', value: 'x', sort_order: 0, created_at: TS },
    { id: 'b', project_id: 'P1', parent_id: 'a', title: 'B', content_type: 'text', value: '', sort_order: 0, created_at: TS },
    { id: 'c', project_id: 'P1', parent_id: 'b', title: 'C', content_type: 'text', value: '', sort_order: 0, created_at: TS },
    { id: 'd', project_id: 'P1', parent_id: null, title: 'D', content_type: 'text', value: '', sort_order: 1, created_at: TS },
  ];

  it('patchInfoNode 只改目标节点', () => {
    const next = patchInfoNode(base, 'b', { title: 'B2' });
    expect(next.find((node) => node.id === 'b')!.title).toBe('B2');
    expect(next.find((node) => node.id === 'a')).toEqual(base[0]);
  });

  it('removeInfoNode 连带删除整棵子树', () => {
    expect(removeInfoNode(base, 'b').map((node) => node.id)).toEqual(['a', 'd']);
  });

  it('computeInfoCompleteness 按一级标签统计末级填写情况', () => {
    const completeness = computeInfoCompleteness(base);
    expect(completeness.get('a')).toEqual({ total: 1, empty: 1, incomplete: true }); // a → b → c，末级只有 C 且未填写
    expect(completeness.get('d')).toEqual({ total: 1, empty: 1, incomplete: true });
  });
});
