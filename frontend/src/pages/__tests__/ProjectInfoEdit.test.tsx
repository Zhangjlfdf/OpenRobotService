import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ProjectInfoEdit from '../admin/ProjectInfoEdit';
import {
  createInfoNodeApi,
  deleteInfoNodeApi,
  fetchInfoTree,
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
}));

// 页头副标题会拉一次项目详情、上传走资源管理服务：统一给个空实现
vi.mock('@/api/client', () => ({
  createRequest: () => vi.fn(async () => ({ name: '演示项目' })),
  ApiError: class MockApiError extends Error { statusCode = 0; },
  clearCache: vi.fn(),
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { username: string }) => unknown) => selector({ username: 'admin' }),
}));

vi.mock('tdesign-mobile-react', () => {
  const Navbar = ({ title }: { title?: ReactNode }) => <div>{title}</div>;
  const Popup = ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null;
  const Input = ({ value, onChange, placeholder }: { value?: string; onChange?: (v: string) => void; placeholder?: string }) => (
    <input value={value ?? ''} onChange={(e) => onChange?.(e.target.value)} placeholder={placeholder} />
  );
  return { Navbar, Popup, Input, Toast: () => null };
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
      </Routes>
    </MemoryRouter>
  );

describe('ProjectInfoEdit（信息树编辑页）', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    vi.mocked(fetchInfoTree).mockResolvedValue(TREE);
  });

  it('打开页面读取后端信息树并渲染节点', async () => {
    renderEdit();
    expect(fetchInfoTree).toHaveBeenCalledWith('P1');
    expect(await screen.findByText('基础信息')).toBeTruthy();
    expect(screen.getByText('客户信息')).toBeTruthy();
    expect((screen.getByLabelText('客户信息内容') as HTMLTextAreaElement).value).toBe('中力');
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

  it('加载失败时给出重试入口', async () => {
    vi.mocked(fetchInfoTree).mockRejectedValueOnce(new Error('network'));
    renderEdit();
    expect(await screen.findByText('信息节点加载失败')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByText('基础信息')).toBeTruthy();
  });
});
