// 详情模板编辑页测试：加载渲染 / 行内改名 / 内容类型切换 / 层级调整 /
// 保存流程（dry-run 预览 → 确认 → 真实同步）/ 删除确认 / 非管理员只读提示。
import type { ReactNode } from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';
import ProjectInfoTemplate from '../admin/ProjectInfoTemplate';
import { fetchInfoTemplateApi, saveInfoTemplateApi, type ApiInfoTemplate } from '@/api/infoNodes';

vi.mock('@/api/infoNodes', () => ({
  fetchInfoTemplateApi: vi.fn(),
  saveInfoTemplateApi: vi.fn(),
}));

// 权限可切换：默认管理员，非管理员用 authState.permissions = [] 覆盖
const authState = vi.hoisted(() => ({ permissions: ['admin'] as string[] }));
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { username: string; permissions: string[] }) => unknown) =>
    selector({ username: 'admin', permissions: authState.permissions }),
}));

vi.mock('tdesign-mobile-react', () => {
  const Navbar = ({ title }: { title?: ReactNode }) => <div>{title}</div>;
  const Popup = ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null;
  return { Navbar, Popup, Toast: vi.fn() };
});

const TEMPLATE: ApiInfoTemplate = {
  id: 'default',
  name: '项目详情模板',
  nodes: [
    {
      id: 't-base', title: '基础信息', content_type: 'text', children: [
        { id: 't-cust', title: '客户信息', content_type: 'text', children: [] },
        { id: 't-type', title: '项目类型', content_type: 'select', options: ['试点项目', 'PK项目'], children: [] },
      ],
    },
    {
      id: 't-hw', title: '硬件', content_type: 'text', children: [
        {
          id: 't-veh', title: '车辆', content_type: 'text', children: [
            { id: 't-m1', title: '车型1', content_type: 'text', children: [] },
          ],
        },
      ],
    },
  ],
  updated_at: '2026-09-15 10:00:00',
  updated_by: 'admin',
  project_count: 3,
  source: 'db',
};

const renderTemplate = () =>
  render(
    <MemoryRouter initialEntries={['/admin/project-info-template']}>
      <ProjectInfoTemplate />
    </MemoryRouter>
  );

const rowTitles = () =>
  Array.from(document.querySelectorAll('.mac-tpl-row__title')).map((el) => el.textContent);

describe('ProjectInfoTemplate（详情模板编辑页）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.permissions = ['admin'];
    vi.mocked(fetchInfoTemplateApi).mockResolvedValue(TEMPLATE);
  });

  it('加载模板：渲染节点树、元信息与下拉节点的选项行', async () => {
    renderTemplate();
    expect(await screen.findByText('基础信息')).toBeTruthy();
    expect(screen.getByText('硬件')).toBeTruthy();
    expect(screen.getByText('车型1')).toBeTruthy();
    // 元信息：更新时间 / 编辑人 / 节点总数 / 涉及项目数
    expect(screen.getByText(/共 6 个节点/)).toBeTruthy();
    expect(screen.getByText(/涉及 3 个项目/)).toBeTruthy();
    // 下拉节点的选项以逗号拼接展示，内容类型下拉回显 select
    expect((screen.getByLabelText('项目类型下拉选项') as HTMLInputElement).value).toBe('试点项目，PK项目');
    expect((screen.getByLabelText('项目类型内容类型') as HTMLSelectElement).value).toBe('select');
  });

  it('点节点名行内改名，失焦后更新本地树', async () => {
    renderTemplate();
    fireEvent.click(await screen.findByLabelText('编辑客户信息'));
    const input = screen.getByLabelText('节点名称') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '客户资料' } });
    fireEvent.blur(input);
    expect(await screen.findByText('客户资料')).toBeTruthy();
    expect(screen.queryByText('客户信息')).toBeNull();
  });

  it('内容类型切到下拉后出现选项编辑行（仅末级可改）', async () => {
    renderTemplate();
    await screen.findByText('基础信息');
    fireEvent.change(screen.getByLabelText('客户信息内容类型'), { target: { value: 'select' } });
    expect((screen.getByLabelText('客户信息下拉选项') as HTMLInputElement).value).toBe('');
    // 有子节点的「基础信息」切到下拉会被拒绝（保持 text）
    fireEvent.change(screen.getByLabelText('基础信息内容类型'), { target: { value: 'select' } });
    expect((screen.getByLabelText('基础信息内容类型') as HTMLSelectElement).value).toBe('text');
  });

  it('⋯ 菜单「同级下移」调整根节点顺序', async () => {
    renderTemplate();
    await screen.findByText('基础信息');
    expect(rowTitles()[0]).toBe('基础信息');

    fireEvent.click(screen.getByLabelText('基础信息更多操作'));
    fireEvent.click(screen.getByText('同级下移'));
    expect(rowTitles()[0]).toBe('硬件');
    expect(rowTitles()[3]).toBe('基础信息');
  });

  it('保存流程：dry-run 预览影响面 → 确认后真实同步并刷新', async () => {
    vi.mocked(saveInfoTemplateApi)
      .mockResolvedValueOnce({
        dry_run: true, projects: 3, changed_projects: 2, added: 1, updated: 2, deleted: 1,
        details: [{ project_id: 'P1', project_name: '演示项目', added: 1, updated: 2, deleted: 1 }],
      })
      .mockResolvedValueOnce({
        dry_run: false, projects: 3, changed_projects: 2, added: 1, updated: 2, deleted: 1,
        updated_at: '2026-09-15 11:00:00', updated_by: 'admin',
      });
    renderTemplate();
    await screen.findByText('基础信息');

    fireEvent.click(screen.getByRole('button', { name: '保存并同步' }));
    await waitFor(() => expect(saveInfoTemplateApi).toHaveBeenNthCalledWith(1, TEMPLATE.nodes, true));

    // 确认弹窗：统计 + 明细 + 删除警示
    expect(await screen.findByText(/将影响 2 \/ 3 个项目/)).toBeTruthy();
    expect(screen.getByText(/删除 1 个节点（含已填内容）/)).toBeTruthy();
    expect(screen.getByText('演示项目')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '确认保存并同步' }));
    await waitFor(() => expect(saveInfoTemplateApi).toHaveBeenNthCalledWith(2, TEMPLATE.nodes, false));
    await waitFor(() => expect(vi.mocked(Toast)).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('已保存并同步：影响 2 个项目') }),
    ));
    // 保存后重新拉取模板（updated_at/by 刷新）
    await waitFor(() => expect(fetchInfoTemplateApi).toHaveBeenCalledTimes(2));
  });

  it('删除节点需确认：提示连带子节点数与同步后果，确认后本地移除', async () => {
    renderTemplate();
    await screen.findByText('基础信息');
    fireEvent.click(screen.getByLabelText('基础信息更多操作'));
    fireEvent.click(screen.getByText('删除节点'));

    expect(await screen.findByText(/删除「基础信息」及其 2 个子节点/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    await waitFor(() => expect(screen.queryByText('基础信息')).toBeNull());
    expect(screen.queryByText('客户信息')).toBeNull();
    expect(vi.mocked(Toast)).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('已删除「基础信息」') }),
    );
  });

  it('非管理员：提示不可编辑，且不请求模板接口', async () => {
    authState.permissions = [];
    renderTemplate();
    expect(await screen.findByText('仅管理员可编辑详情模板')).toBeTruthy();
    expect(screen.queryByRole('button', { name: '保存并同步' })).toBeNull();
    expect(screen.queryByText('基础信息')).toBeNull();
    expect(fetchInfoTemplateApi).not.toHaveBeenCalled();
  });
});
