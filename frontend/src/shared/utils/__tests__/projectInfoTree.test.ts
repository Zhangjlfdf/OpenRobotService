import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  computeInfoCompleteness,
  createInfoNode,
  deleteInfoNode,
  encodeInfoValue,
  flattenInfoTree,
  importInfoTemplate,
  importInfoTree,
  loadHistoryLatest,
  loadHistorySeen,
  loadInfoNodeChanges,
  loadInfoNodeMarks,
  loadInfoNodes,
  loadProjectActivity,
  moveInfoNode,
  normalizeImportNodes,
  patchInfoNode,
  REGION_MAINLAND,
  removeInfoNode,
  saveHistorySeen,
  toggleInfoNodeMark,
  unseenHistoryNodes,
  unseenHistoryRoots,
  updateInfoNode,
  visibleInfoNodes,
  type ProjectInfoNode,
} from '../projectInfoTree';
import {
  createInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoNodeChangeSummaryApi,
  fetchInfoNodeChangesApi,
  fetchInfoNodeMarksApi,
  fetchInfoTree,
  fetchProjectActivityApi,
  importInfoTemplateApi,
  importInfoTreeApi,
  moveInfoNodeApi,
  toggleInfoNodeMarkApi,
  updateInfoNodeApi,
  type ApiInfoNode,
} from '@/api/infoNodes';

vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  importInfoTemplateApi: vi.fn(),
  fetchInfoNodeChangesApi: vi.fn(),
  fetchInfoNodeChangeSummaryApi: vi.fn(),
  fetchInfoNodeMarksApi: vi.fn(),
  toggleInfoNodeMarkApi: vi.fn(),
  fetchProjectActivityApi: vi.fn(),
}));

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

const parseValue = (node: { value?: string | null }) => JSON.parse(node.value ?? 'null');
const selectOf = (node: ProjectInfoNode) => node.value as { selected: string; options: string[] };

// 预设信息树模板已下沉到后端（project_templates/*.yaml），前端只触发重建；
// 模板结构本身（13 个一级节点、≤4 层、下拉选项）由后端实例化，另有校验。
describe('按后端模板初始化', () => {
  beforeEach(() => vi.clearAllMocks());

  it('调用 import-template 接口并返回写入的节点数', async () => {
    vi.mocked(importInfoTemplateApi).mockResolvedValue(120);
    await expect(importInfoTemplate('P1')).resolves.toBe(120);
    expect(importInfoTemplateApi).toHaveBeenCalledWith('P1');
  });

  it('模板为空（后端返回 0）时不抛错，交由页面提示', async () => {
    vi.mocked(importInfoTemplateApi).mockResolvedValue(0);
    await expect(importInfoTemplate('P1')).resolves.toBe(0);
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

  it('「标题: 内容」紧凑映射：文字 / 数组(下拉) / 对象(子节点) 三种写法可混用', () => {
    const tree = normalizeImportNodes({
      info_nodes: {
        基础信息: {
          客户信息: '',
          订单信息: { ERP: '' },
          项目类型: ['试点项目', '大客户项目'],
        },
      },
    });

    expect(tree.map((node) => node.title)).toEqual(['基础信息']);
    const children = tree[0].children!;
    expect(children.map((node) => node.title)).toEqual(['客户信息', '订单信息', '项目类型']);

    expect(children[0].content_type).toBe('text');
    expect(children[0].value).toBe('');

    // 对象 → 子节点（递归）
    expect(children[1].children!.map((node) => node.title)).toEqual(['ERP']);

    // 数组 → 下拉节点，值编码成后端存的 TEXT
    expect(children[2].content_type).toBe('select');
    expect(parseValue(children[2])).toEqual({ selected: '', options: ['试点项目', '大客户项目'] });
  });

  it('options 清单（后端 YAML 模板写法）自动补成下拉值', () => {
    const [node] = normalizeImportNodes([{ title: '载具类型', options: ['托盘', '料笼'] }]);
    expect(node.content_type).toBe('select');
    expect(parseValue(node)).toEqual({ selected: '', options: ['托盘', '料笼'] });
  });
});

describe('区域细分字段联动（区域选项 → 省份/地区 | 具体国家）', () => {
  /** 项目区域/地点 下：区域选项(下拉) + 省份 + 地区 + 具体国家 + 用户自建字段 */
  const regionNodes = (selected: string, options = [REGION_MAINLAND, '亚洲Asia']): ProjectInfoNode[] => [
    { id: 'p', project_id: 'P1', parent_id: null, title: '项目区域/地点', content_type: 'text', value: '', sort_order: 0, created_at: TS },
    { id: 'd', project_id: 'P1', parent_id: 'p', title: '区域选项', content_type: 'select', value: { selected, options }, sort_order: 0, created_at: TS },
    { id: 's', project_id: 'P1', parent_id: 'p', title: '省份', content_type: 'text', value: '浙江省', sort_order: 1, created_at: TS },
    { id: 'a', project_id: 'P1', parent_id: 'p', title: '地区', content_type: 'text', value: '安吉县', sort_order: 2, created_at: TS },
    { id: 'c', project_id: 'P1', parent_id: 'p', title: '具体国家', content_type: 'text', value: '', sort_order: 3, created_at: TS },
    { id: 'x', project_id: 'P1', parent_id: 'p', title: '自建字段', content_type: 'text', value: '', sort_order: 4, created_at: TS },
  ];
  const titlesOf = (nodes: ProjectInfoNode[]) => visibleInfoNodes(nodes).map((node) => node.title);

  it('未选择区域：省份/地区与具体国家都不显示', () => {
    expect(titlesOf(regionNodes(''))).toEqual(['项目区域/地点', '区域选项', '自建字段']);
  });

  it('选中大陆：显示省份/地区，隐藏具体国家（原值保留在数据里）', () => {
    const nodes = regionNodes(REGION_MAINLAND);
    expect(titlesOf(nodes)).toEqual(['项目区域/地点', '区域选项', '省份', '地区', '自建字段']);
    expect(nodes.find((node) => node.id === 's')?.value).toBe('浙江省');
  });

  it('选中其它区域：显示具体国家，隐藏省份/地区', () => {
    expect(titlesOf(regionNodes('亚洲Asia'))).toEqual(['项目区域/地点', '区域选项', '具体国家', '自建字段']);
  });

  it('换成普通下拉（选项里没有大陆）：不做联动，细分字段照常显示', () => {
    expect(titlesOf(regionNodes('甲', ['甲', '乙']))).toContain('省份');
    expect(titlesOf(regionNodes('甲', ['甲', '乙']))).toContain('具体国家');
  });
});

describe('编辑历史（操作记录读接口 + 本机已读水位）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('loadInfoNodeChanges / loadHistoryLatest 按项目与节点查后端', async () => {
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      {
        id: 'h1', node_id: 'n1', parent_id: null, node_title: '客户信息', action: 'update',
        operator: 'zhangsan', operator_name: '张三', detail: '把内容从「空」改为「中力」', created_at: TS,
      },
    ]);
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ n1: 'h1' });

    const records = await loadInfoNodeChanges('P1', 'n1');
    expect(fetchInfoNodeChangesApi).toHaveBeenCalledWith('P1', 'n1');
    expect(records[0].operator_name).toBe('张三');
    expect(records[0].detail).toBe('把内容从「空」改为「中力」');

    await expect(loadHistoryLatest('P1')).resolves.toEqual({ n1: 'h1' });
    expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalledWith('P1');
  });

  it('已读水位按「项目 + 用户」分别存本机，坏数据回退空表', () => {
    expect(loadHistorySeen('CODE-A', 'zhang')).toEqual({});
    saveHistorySeen('CODE-A', { n1: 'h1' }, 'zhang');
    expect(loadHistorySeen('CODE-A', 'zhang')).toEqual({ n1: 'h1' });
    expect(loadHistorySeen('CODE-B', 'zhang')).toEqual({}); // 别的项目不受影响
    // 别人的已读状态与本机用户无关：换个登录用户，未看过的照样出红点
    expect(loadHistorySeen('CODE-A', 'li')).toEqual({});

    localStorage.setItem('project-info-tree:history-seen:CODE-A:zhang', 'not-json');
    expect(loadHistorySeen('CODE-A', 'zhang')).toEqual({});
  });

  it('unseenHistoryNodes：最新记录 id 与已读水位不一致（或从没看过）即出新红点', () => {
    // 记录 id 是时间有序的 UUIDv7：同秒内的新记录 id 也不同，不会漏
    const latest = { n1: 'a-2', n2: 'b-2', n3: 'c-1' };
    const seen = { n1: 'a-2', n2: 'b-1', n3: '' };
    // n1 看过的就是最新那条 → 不冒红点；n2 看过之后又有新记录 → 冒；n3 本机没水位 → 冒
    expect([...unseenHistoryNodes(latest, seen)].sort()).toEqual(['n2', 'n3']);
    // 没有任何记录的节点不参与
    expect(unseenHistoryNodes({}, {})).toEqual(new Set());
    expect(unseenHistoryNodes({ n9: '' }, {})).toEqual(new Set());
  });

  it('unseenHistoryRoots：未读变动归到所在的一级标签（多层上溯），删除的节点不归', () => {
    const nodes = [
      { id: 'r1', parent_id: null },
      { id: 'c1', parent_id: 'r1' },
      { id: 'g1', parent_id: 'c1' },
      { id: 'r2', parent_id: null },
    ];
    // 孙节点归到 r1，根自身未读归自己；无关的根不出现
    expect(unseenHistoryRoots(nodes, new Set(['g1', 'r2']))).toEqual(new Set(['r1', 'r2']));
    // 一个标签下多个未读子节点只出一个根
    expect(unseenHistoryRoots(nodes, new Set(['c1', 'g1']))).toEqual(new Set(['r1']));
    // 树里已删除（只剩记录）的节点不往上归
    expect(unseenHistoryRoots(nodes, new Set(['missing']))).toEqual(new Set());
    expect(unseenHistoryRoots(nodes, new Set())).toEqual(new Set());
  });
});

describe('关注（星标）与项目动态', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('loadInfoNodeMarks / toggleInfoNodeMark 直通关注接口', async () => {
    vi.mocked(fetchInfoNodeMarksApi).mockResolvedValue(['n1', 'n2']);
    vi.mocked(toggleInfoNodeMarkApi).mockResolvedValueOnce(true).mockResolvedValueOnce(false);

    await expect(loadInfoNodeMarks('P1')).resolves.toEqual(['n1', 'n2']);
    expect(fetchInfoNodeMarksApi).toHaveBeenCalledWith('P1');

    await expect(toggleInfoNodeMark('n1')).resolves.toBe(true);
    await expect(toggleInfoNodeMark('n1')).resolves.toBe(false); // 再点即取消
    expect(toggleInfoNodeMarkApi).toHaveBeenCalledWith('n1');
  });

  it('loadProjectActivity 返回被关注节点的最新变动（只含变动内容所需字段）', async () => {
    vi.mocked(fetchProjectActivityApi).mockResolvedValue([
      {
        node_id: 'n1', node_title: '客户信息', root_title: '基础信息', action: 'update',
        detail: '把内容从「空」改为「中力」', created_at: TS,
      },
    ]);
    const list = await loadProjectActivity('P1');
    expect(fetchProjectActivityApi).toHaveBeenCalledWith('P1');
    expect(list[0].detail).toBe('把内容从「空」改为「中力」');
    expect(list[0].root_title).toBe('基础信息');
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
