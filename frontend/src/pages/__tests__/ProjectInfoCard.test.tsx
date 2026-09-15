import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ProjectInfoCard from '../admin/ProjectInfoCard';
import { fetchInfoTree, type ApiInfoNode } from '@/api/infoNodes';

// 卡片的数据源是后端信息树接口：这里 mock 出固定的树，验证加载/渲染/筛选/失败重试
vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
}));

const TS = '2026-09-14 10:00:00';
const node = (partial: Partial<ApiInfoNode> & { id: string }): ApiInfoNode => ({
  project_id: 'CODE-1',
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
    children: [
      node({ id: 'c1', parent_id: 'r1', title: '客户信息', value: '中力', sort_order: 0 }),
      node({
        id: 'c2', parent_id: 'r1', title: '项目类型', content_type: 'select', sort_order: 1,
        value: JSON.stringify({ selected: '', options: ['PK 项目', '试点项目'] }),
      }),
    ],
  }),
  node({
    id: 'r2',
    title: '硬件',
    sort_order: 1,
    children: [
      node({
        id: 'c3', parent_id: 'r2', title: '载具类型', content_type: 'select', sort_order: 0,
        value: JSON.stringify({ selected: '托盘', options: ['托盘', '料笼'] }),
      }),
    ],
  }),
];

const renderCard = (projectId: string, canEdit = true) =>
  render(
    <MemoryRouter>
      <ProjectInfoCard projectId={projectId} canEdit={canEdit} />
    </MemoryRouter>
  );

describe('ProjectInfoCard（项目信息管理卡）', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(fetchInfoTree).mockResolvedValue(TREE);
  });

  it('加载后显示后端返回的一级标签', async () => {
    renderCard('CODE-1');
    expect(fetchInfoTree).toHaveBeenCalledWith('CODE-1');
    expect(await screen.findByRole('button', { name: /基础信息/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /硬件/ })).toBeTruthy();
  });

  it('文档式渲染：文字内容与已选项直接展示，未选择显示提示', async () => {
    renderCard('CODE-1');
    expect(await screen.findByText('客户信息')).toBeTruthy();
    expect(screen.getByText('中力')).toBeTruthy();
    // c2 未选择、c3 已选「托盘」
    expect(screen.getAllByText('未选择').length).toBe(1);
    expect(screen.getByText('托盘')).toBeTruthy();
  });

  it('点选一级标签只显示该标签下的内容', async () => {
    renderCard('CODE-1');
    fireEvent.click(await screen.findByRole('button', { name: /^硬件/ }));
    const doc = document.querySelector('.mac-doc') as HTMLElement;
    expect(within(doc).getByText('载具类型')).toBeTruthy();
    expect(within(doc).queryByText('项目类型')).toBeNull();
    expect(within(doc).queryByText('客户信息')).toBeNull();
  });

  it('加载失败时显示重试，点击后重新拉取', async () => {
    vi.mocked(fetchInfoTree).mockRejectedValueOnce(new Error('boom'));
    renderCard('CODE-1');
    expect(await screen.findByText('信息节点加载失败')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByRole('button', { name: /基础信息/ })).toBeTruthy();
    await waitFor(() => expect(fetchInfoTree).toHaveBeenCalledTimes(2));
  });

  it('无编辑权限（新建项目）时不显示「编辑」入口', async () => {
    renderCard('new', false);
    await screen.findByRole('button', { name: /基础信息/ });
    expect(screen.queryByText('编辑')).toBeNull();
  });
});
