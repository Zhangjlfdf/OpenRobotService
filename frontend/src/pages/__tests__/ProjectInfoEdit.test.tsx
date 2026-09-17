import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ProjectInfoEdit from '../admin/ProjectInfoEdit';
import {
  createInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoNodeChangeSummaryApi,
  fetchInfoNodeChangesApi,
  fetchInfoTree,
  importInfoTemplateApi,
  updateInfoNodeApi,
  type ApiInfoNode,
  type ApiInfoNodeChange,
} from '@/api/infoNodes';

vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  importInfoTemplateApi: vi.fn(),
  // 编辑历史：进页面会拉一次「各节点最新记录时间」算小红点，缺了页面会直接崩
  fetchInfoNodeChangesApi: vi.fn(),
  fetchInfoNodeChangeSummaryApi: vi.fn(),
}));

// 页头副标题会拉一次项目详情、上传走资源管理服务：统一给个空实现
vi.mock('@/api/client', () => ({
  createRequest: () => vi.fn(async () => ({ name: '演示项目' })),
  ApiError: class MockApiError extends Error { statusCode = 0; },
  clearCache: vi.fn(),
}));

// 权限可切换：默认管理员（「详情模板」入口可见），非管理员用例覆盖为 []
const authState = vi.hoisted(() => ({ permissions: ['admin'] as string[] }));
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { username: string; permissions: string[] }) => unknown) =>
    selector({ username: 'admin', permissions: authState.permissions }),
}));

vi.mock('tdesign-mobile-react', () => {
  const Navbar = ({ title }: { title?: ReactNode }) => <div>{title}</div>;
  const Popup = ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null;
  const Input = ({ value, onChange, placeholder }: { value?: string; onChange?: (v: string) => void; placeholder?: string }) => (
    <input value={value ?? ''} onChange={(e) => onChange?.(e.target.value)} placeholder={placeholder} />
  );
  // 一键回到顶部按钮：无交互逻辑可测，渲染占位即可
  const BackTop = () => <div data-testid="backtop" />;
  return { Navbar, Popup, Input, Toast: () => null, BackTop };
});

const TS = '2026-09-14 10:00:00';
const node = (partial: Partial<ApiInfoNode> & { id: string }): ApiInfoNode => ({
  project_id: 'P1',
  parent_id: null,
  title: '节点',
  content_type: 'text',
  value: null,
  sort_order: 0,
  created_at: TS,
  updated_at: TS,
  ...partial,
});

const TREE: ApiInfoNode[] = [
  node({
    id: 'r1',
    title: '基础信息',
    sort_order: 0,
    children: [node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力', sort_order: 0 })],
  }),
];

/** 构造一条后端操作记录（默认值可按需覆盖） */
const change = (partial: Partial<ApiInfoNodeChange> & { id: string }): ApiInfoNodeChange => ({
  node_id: 'r1',
  parent_id: null,
  node_title: '基础信息',
  action: 'update',
  operator: null,
  operator_name: null,
  detail: null,
  created_at: '2026-09-14 11:00:00',
  ...partial,
});

const renderEdit = () =>
  render(
    <MemoryRouter initialEntries={['/admin/project-detail/P1/edit']}>
      <Routes>
        <Route path="/admin/project-detail/:id/edit" element={<ProjectInfoEdit />} />
        <Route path="/admin/project-info-template" element={<div>模板页占位</div>} />
      </Routes>
    </MemoryRouter>
  );

describe('ProjectInfoEdit（信息树编辑页）', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    authState.permissions = ['admin'];
    vi.mocked(fetchInfoTree).mockResolvedValue(TREE);
    // 默认没有操作记录：不出小红点，历史弹层显示空态
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({});
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([]);
  });

  it('打开页面读取后端信息树并渲染节点', async () => {
    renderEdit();
    expect(fetchInfoTree).toHaveBeenCalledWith('P1');
    expect(await screen.findByText('基础信息')).toBeTruthy();
    // 有节点的项目不会被自动初始化（接口是替换式导入，误触发会重建整棵树）
    expect(importInfoTemplateApi).not.toHaveBeenCalled();
    expect(screen.getByText('客户信息')).toBeTruthy();
    expect((screen.getByLabelText('客户信息内容') as HTMLTextAreaElement).value).toBe('中力');
  });

  it('管理员可见「详情模板」入口，点击进入模板编辑页', async () => {
    renderEdit();
    await screen.findByText('基础信息');
    fireEvent.click(screen.getByRole('button', { name: '详情模板' }));
    expect(await screen.findByText('模板页占位')).toBeTruthy();
  });

  it('非管理员不显示「详情模板」入口', async () => {
    authState.permissions = [];
    renderEdit();
    await screen.findByText('基础信息');
    expect(screen.queryByRole('button', { name: '详情模板' })).toBeNull();
  });

  it('行内改名写回后端（PUT 节点）', async () => {
    vi.mocked(updateInfoNodeApi).mockResolvedValue(node({ id: 'r1', title: '基础信息2' }));
    renderEdit();
    fireEvent.click(await screen.findByLabelText('编辑基础信息'));

    const input = document.querySelector('.mac-info-row__input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '基础信息2' } });
    fireEvent.blur(input);

    await waitFor(() => {
      expect(updateInfoNodeApi).toHaveBeenCalledWith('r1', { title: '基础信息2' });
    });
    expect(await screen.findByText('基础信息2')).toBeTruthy();
  });

  it('车型节点标题直接给「选车型」下拉框（8 系列 50 款，选中写回节点名）', async () => {
    const vehicleTree: ApiInfoNode[] = [
      node({
        id: 'h1', title: '硬件', sort_order: 0,
        children: [
          node({
            id: 'v1', parent_id: 'h1', title: '车辆', sort_order: 0,
            children: [
              node({
                id: 'm1', parent_id: 'v1', title: '车型1', sort_order: 0,
                children: [node({ id: 'q1', parent_id: 'm1', title: '数量', value: '2', sort_order: 0 })],
              }),
            ],
          }),
        ],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(vehicleTree);
    vi.mocked(updateInfoNodeApi).mockImplementation(async (nodeId, updates) =>
      node({ id: nodeId, title: updates.title ?? '节点' }),
    );
    renderEdit();

    const select = (await screen.findByLabelText('选择车型')) as HTMLSelectElement;
    // 8 个系列分组、50 款可选；未选择时显示节点原名占位
    expect(select.querySelectorAll('optgroup')).toHaveLength(8);
    expect(select.querySelectorAll('option:not([value=""])')).toHaveLength(50);
    expect(select.value).toBe('');
    // 「数量」等子节点不受影响，仍是普通节点
    expect(screen.getAllByLabelText('选择车型')).toHaveLength(1);
    expect(screen.getByText('数量')).toBeTruthy();

    fireEvent.change(select, { target: { value: 'XC1051' } });
    await waitFor(() => expect(updateInfoNodeApi).toHaveBeenCalledWith('m1', { title: 'XC1051' }));
    // 保存后下拉框回显该车型
    expect((screen.getByLabelText('选择车型') as HTMLSelectElement).value).toBe('XC1051');
  });

  it('「新标签」先建后端节点再进入改名态', async () => {
    vi.mocked(createInfoNodeApi).mockImplementation(async (_projectId, payload) =>
      node({ id: payload.id, title: payload.title ?? '', parent_id: payload.parent_id ?? null }),
    );
    renderEdit();
    fireEvent.click(await screen.findByText('新标签'));

    await waitFor(() => {
      expect(createInfoNodeApi).toHaveBeenCalledWith('P1', expect.objectContaining({ sort_order: 1, parent_id: null }));
    });
    expect(document.querySelector('.mac-info-row__input')).toBeTruthy();
  });

  it('删除节点走 DELETE 接口（含子树提示）', async () => {
    vi.mocked(deleteInfoNodeApi).mockResolvedValue(undefined);
    renderEdit();
    // 树里每行都有「更多操作」按钮，这里取第一个（根节点）
    fireEvent.click((await screen.findAllByLabelText('更多操作'))[0]);
    fireEvent.click(screen.getByText('删除节点'));
    expect(screen.getByText(/及其所有子节点/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    await waitFor(() => {
      expect(deleteInfoNodeApi).toHaveBeenCalledWith('r1');
    });
    expect(screen.queryByText('基础信息')).toBeNull();
  });

  it('区域选项选大陆 → 出现省份/地区；改选其它区域 → 换成具体国家', async () => {
    const regionTree: ApiInfoNode[] = [
      node({
        id: 'r1', title: '基础信息', sort_order: 0,
        children: [
          node({
            id: 'p1', parent_id: 'r1', title: '项目区域/地点', sort_order: 0,
            children: [
              node({
                id: 'd1', parent_id: 'p1', title: '区域选项', content_type: 'select', sort_order: 0,
                value: JSON.stringify({ selected: '', options: ['大陆(China Mainland)', '亚洲Asia'] }),
              }),
              node({ id: 's1', parent_id: 'p1', title: '省份', value: '浙江省', sort_order: 1 }),
              node({ id: 'a1', parent_id: 'p1', title: '地区', value: '安吉县', sort_order: 2 }),
              node({ id: 'c1', parent_id: 'p1', title: '具体国家', value: '', sort_order: 3 }),
            ],
          }),
        ],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(regionTree);
    vi.mocked(updateInfoNodeApi).mockImplementation(async (nodeId, updates) =>
      node({ id: nodeId, value: typeof updates.value === 'string' ? updates.value : null }),
    );
    renderEdit();

    // 未选择区域：三个细分字段都不显示
    await screen.findByText('区域选项');
    expect(screen.queryByText('省份')).toBeNull();
    expect(screen.queryByText('具体国家')).toBeNull();

    const select = screen.getByLabelText('区域选项内容') as HTMLSelectElement;
    fireEvent.change(select, { target: { value: '大陆(China Mainland)' } });
    expect(await screen.findByText('省份')).toBeTruthy();
    expect(screen.getByText('地区')).toBeTruthy();
    expect(screen.queryByText('具体国家')).toBeNull();

    fireEvent.change(screen.getByLabelText('区域选项内容'), { target: { value: '亚洲Asia' } });
    expect(await screen.findByText('具体国家')).toBeTruthy();
    expect(screen.queryByText('省份')).toBeNull();
    expect(screen.queryByText('地区')).toBeNull();
  });

  it('加载失败时给出重试入口', async () => {
    vi.mocked(fetchInfoTree).mockRejectedValueOnce(new Error('network'));
    renderEdit();
    expect(await screen.findByText('信息节点加载失败')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByText('基础信息')).toBeTruthy();
  });

  it('空树项目进页自动调模板接口初始化（无需点按钮）并重载树', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValueOnce([]).mockResolvedValue(TREE);
    vi.mocked(importInfoTemplateApi).mockResolvedValue(120);
    renderEdit();

    await waitFor(() => expect(importInfoTemplateApi).toHaveBeenCalledWith('P1'));
    // 初始化成功后重新拉树（第二次 fetchInfoTree 返回 TREE），页面切换成树视图
    expect(await screen.findByText('基础信息')).toBeTruthy();
    expect(fetchInfoTree).toHaveBeenCalledTimes(2);
    // 「替换式导入」不能重复触发：只自动跑一次
    expect(importInfoTemplateApi).toHaveBeenCalledTimes(1);
  });

  it('自动初始化失败时停在空态，按钮保留可手动重试', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValueOnce([]).mockResolvedValue(TREE);
    vi.mocked(importInfoTemplateApi).mockRejectedValueOnce(new Error('network')).mockResolvedValue(120);
    renderEdit();

    await waitFor(() => expect(importInfoTemplateApi).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole('button', { name: '按预设模板初始化' }));
    expect(await screen.findByText('基础信息')).toBeTruthy();
  });

  it('历史弹层展示后端的操作记录：人员 / 变动 / 时间；子节点删除记录挂在父节点下', async () => {
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({
        id: 'h1',
        operator: 'zhangsan',
        operator_name: '张三',
        detail: '把标题从「基础」改为「基础信息」',
        created_at: '2026-09-14 11:00:00',
      }),
      // 子节点「客户信息」被删：记录在父节点「基础信息」的历史里
      change({
        id: 'h2',
        node_id: 'c1',
        parent_id: 'r1',
        node_title: '客户信息',
        action: 'delete',
        operator_name: '李四',
        detail: '删除节点「客户信息」',
        created_at: '2026-09-14 10:30:00',
      }),
    ]);
    renderEdit();

    fireEvent.click(await screen.findByLabelText('查看基础信息的编辑历史'));

    expect(fetchInfoNodeChangesApi).toHaveBeenCalledWith('P1', 'r1');
    expect(await screen.findByText('张三')).toBeTruthy();
    expect(screen.getByText('把标题从「基础」改为「基础信息」')).toBeTruthy();
    expect(screen.getByText('2026-09-14 11:00:00')).toBeTruthy();
    // 删除记录连同操作人与时间一起显示在父节点下
    expect(screen.getByText('删除')).toBeTruthy();
    expect(screen.getByText('删除节点「客户信息」')).toBeTruthy();
    expect(screen.getByText('李四')).toBeTruthy();
  });

  it('识别不到操作人时回退成「未知用户」，没有 detail 时按节点标题兜底', async () => {
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([change({ id: 'h1', action: 'create', detail: null })]);
    renderEdit();
    fireEvent.click(await screen.findByLabelText('查看基础信息的编辑历史'));

    expect(await screen.findByText('未知用户')).toBeTruthy();
    expect(screen.getByText('新增节点「基础信息」')).toBeTruthy();
  });

  it('该节点有新记录时历史按钮出小红点，打开看过之后消失（已读水位存本机）', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' });
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({ id: '01a0a8f3-9f64-7103-82df-90d24a472f2c', operator_name: '张三', detail: '把内容从「空」改为「中力」' }),
    ]);
    renderEdit();

    const historyBtn = await screen.findByLabelText('查看基础信息的编辑历史');
    await waitFor(() => expect(historyBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());
    // 没有记录的节点不出红点
    expect(screen.getByLabelText('查看客户信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();

    fireEvent.click(historyBtn);
    expect(await screen.findByText('张三')).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
    // 已读水位 = 该节点最新记录 id，按「项目 + 登录用户」存，互不影响
    expect(JSON.parse(localStorage.getItem('project-info-tree:history-seen:P1:admin') ?? '{}')).toEqual({
      r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c',
    });
  });

  it('已看过（水位就是最新记录）的节点不出小红点', async () => {
    localStorage.setItem(
      'project-info-tree:history-seen:P1:admin',
      JSON.stringify({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' }),
    );
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' });
    renderEdit();

    const historyBtn = await screen.findByLabelText('查看基础信息的编辑历史');
    await waitFor(() => expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalled());
    expect(historyBtn.querySelector('.mac-info-row__op-dot')).toBeNull();
  });

  it('保存成功后立即重算历史：自己刚改的节点也带上小红点（没点开过就一直带）', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi)
      .mockResolvedValueOnce({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' }) // 进入页面：r1 已读
      .mockResolvedValue({ r1: '01a0a8f3-9f64-7105-b1c2-3d4e5f607182' }); // 保存后：r1 有了新记录
    localStorage.setItem(
      'project-info-tree:history-seen:P1:admin',
      JSON.stringify({ r1: '01a0a8f3-9f64-7103-82df-90d24a472f2c' }),
    );
    vi.mocked(updateInfoNodeApi).mockResolvedValue(node({ id: 'r1', title: '基础信息2' }));
    renderEdit();

    const historyBtn = await screen.findByLabelText('查看基础信息的编辑历史');
    await waitFor(() => expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalledTimes(1));
    expect(historyBtn.querySelector('.mac-info-row__op-dot')).toBeNull();

    // 改名保存成功 → 立即再拉一次各节点最新记录 → 小红点出现
    fireEvent.click(await screen.findByLabelText('编辑基础信息'));
    const input = document.querySelector('.mac-info-row__input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '基础信息2' } });
    fireEvent.blur(input);

    await waitFor(() => expect(updateInfoNodeApi).toHaveBeenCalledWith('r1', { title: '基础信息2' }));
    await waitFor(() => {
      expect(screen.getByLabelText('查看基础信息2的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();
    });
    expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalledTimes(2); // 进页面 1 次 + 保存成功后 1 次
  });

  it('子节点有新变动时，所在的一级节点（根节点）同样出小红点', async () => {
    // 只有子节点 c1 有新记录，根节点 r1 自己没有
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ c1: 'h-c1' });
    renderEdit();

    const rootBtn = await screen.findByLabelText('查看基础信息的编辑历史');
    const childBtn = screen.getByLabelText('查看客户信息的编辑历史');
    await waitFor(() => expect(childBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());
    expect(rootBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy();
  });

  it('点开子节点历史后，子节点与根节点的红点一起消失（根节点的点只汇总未读的变动）', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ c1: 'h-c1' });
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({ id: 'h-c1', node_id: 'c1', parent_id: 'r1', node_title: '客户信息', detail: '把内容从「空」改为「中力」' }),
    ]);
    renderEdit();

    const childBtn = await screen.findByLabelText('查看客户信息的编辑历史');
    await waitFor(() => expect(childBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());
    expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();

    fireEvent.click(childBtn);
    expect(await screen.findByText('把内容从「空」改为「中力」')).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByLabelText('查看客户信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
      expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
  });

  it('根节点自身还有别的未读变动时，点开某一处后根节点的红点保留', async () => {
    // c1 与根节点 r1 都有未读记录
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ r1: 'h-r1', c1: 'h-c1' });
    vi.mocked(fetchInfoNodeChangesApi).mockImplementation(async (_projectId, nodeId) =>
      nodeId === 'c1'
        ? [change({ id: 'h-c1', node_id: 'c1', parent_id: 'r1', node_title: '客户信息', detail: '改了子节点' })]
        : [change({ id: 'h-r1', node_id: 'r1', node_title: '基础信息', detail: '改了根节点' })],
    );
    renderEdit();

    const childBtn = await screen.findByLabelText('查看客户信息的编辑历史');
    await waitFor(() => expect(childBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());

    fireEvent.click(childBtn);
    expect(await screen.findByText('改了子节点')).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByLabelText('查看客户信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
    // 根节点自己的记录还没看过 → 红点还在
    expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();
  });

  it('深层节点（第 4 层）有新变动时，红点一路汇总到最外层一级节点', async () => {
    const deepTree: ApiInfoNode[] = [
      node({
        id: 'h1', title: '硬件', sort_order: 0,
        children: [
          node({
            id: 'v1', parent_id: 'h1', title: '车辆', sort_order: 0,
            children: [
              node({
                id: 'm1', parent_id: 'v1', title: '车型1', sort_order: 0,
                children: [node({ id: 'q1', parent_id: 'm1', title: '数量', value: '2', sort_order: 0 })],
              }),
            ],
          }),
        ],
      }),
    ];
    vi.mocked(fetchInfoTree).mockResolvedValue(deepTree);
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ q1: 'h-q1' });
    renderEdit();

    const leafBtn = await screen.findByLabelText('查看数量的编辑历史');
    await waitFor(() => expect(leafBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());
    // 只汇总到一级节点：中间层「车辆」「车型1」不出点
    expect(screen.getByLabelText('查看硬件的编辑历史').querySelector('.mac-info-row__op-dot')).toBeTruthy();
    expect(screen.getByLabelText('查看车辆的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    expect(screen.getByLabelText('查看车型1的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
  });

  it('已删除节点的记录（树里没有这一行）不会把红点挂到别处', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ gone: 'h-gone', c1: 'h-c1' });
    vi.mocked(fetchInfoNodeChangesApi).mockResolvedValue([
      change({ id: 'h-c1', node_id: 'c1', parent_id: 'r1', node_title: '客户信息', detail: '改了子节点' }),
    ]);
    renderEdit();

    const childBtn = await screen.findByLabelText('查看客户信息的编辑历史');
    await waitFor(() => expect(childBtn.querySelector('.mac-info-row__op-dot')).toBeTruthy());

    fireEvent.click(childBtn);
    await waitFor(() => {
      expect(screen.getByLabelText('查看基础信息的编辑历史').querySelector('.mac-info-row__op-dot')).toBeNull();
    });
  });
});
