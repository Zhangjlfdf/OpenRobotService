import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ProjectInfoEdit from '../admin/ProjectInfoEdit';
import {
  createInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoTree,
  importInfoTemplateApi,
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
  });

  it('打开页面读取后端信息树并渲染节点', async () => {
    renderEdit();
    expect(fetchInfoTree).toHaveBeenCalledWith('P1');
    expect(await screen.findByText('基础信息')).toBeTruthy();
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

  it('空树时「按预设模板初始化」调后端模板接口并重载树', async () => {
    vi.mocked(fetchInfoTree).mockResolvedValueOnce([]).mockResolvedValue(TREE);
    vi.mocked(importInfoTemplateApi).mockResolvedValue(120);
    renderEdit();

    fireEvent.click(await screen.findByRole('button', { name: '按预设模板初始化' }));

    await waitFor(() => expect(importInfoTemplateApi).toHaveBeenCalledWith('P1'));
    // 初始化成功后重新拉树（第二次 fetchInfoTree 返回 TREE），页面切换成树视图
    expect(await screen.findByText('基础信息')).toBeTruthy();
    expect(fetchInfoTree).toHaveBeenCalledTimes(2);
  });
});
