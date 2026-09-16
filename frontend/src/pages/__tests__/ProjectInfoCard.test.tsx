import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ProjectInfoCard from '../admin/ProjectInfoCard';
import {
  fetchInfoNodeChangeSummaryApi, fetchInfoNodeMarksApi, fetchInfoTree, toggleInfoNodeMarkApi,
  type ApiInfoNode,
} from '@/api/infoNodes';

// tdesign 的 Toast 只在关注失败时调用：桩掉避免渲染真实弹层
vi.mock('tdesign-mobile-react', () => ({ Toast: vi.fn() }));

// 卡片的数据源是后端信息树接口：这里 mock 出固定的树，验证加载/渲染/筛选/失败重试
vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: vi.fn(),
  createInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  fetchInfoNodeMarksApi: vi.fn(),
  toggleInfoNodeMarkApi: vi.fn(),
  fetchProjectActivityApi: vi.fn(),
  fetchInfoNodeChangeSummaryApi: vi.fn(),
}));

// 红点水位按「项目 + 登录用户」存本机：固定登录用户，测出的水位 key 可预期
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { username: string }) => unknown) => selector({ username: 'admin' }),
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
    vi.mocked(fetchInfoNodeMarksApi).mockResolvedValue([]);
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({});
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

  it('标题与内容同行（同一 .mac-doc__row 内），根节点各占一个一级分组', async () => {
    renderCard('CODE-1');
    await screen.findByText('客户信息');
    const rows = Array.from(document.querySelectorAll('.mac-doc__row'));
    const rowOf = (title: string) =>
      rows.find((row) => row.querySelector('.mac-doc__label')?.textContent === title);

    // 标题与内容在同一行里，而不是标题一行、内容另起一行
    expect(rowOf('客户信息')?.querySelector('.mac-doc__value')?.textContent).toBe('中力');
    expect(rowOf('载具类型')?.querySelector('.mac-doc__value')?.textContent).toBe('托盘');
    // 分支节点只有标题、没有内容块
    expect(rowOf('基础信息')?.querySelector('.mac-doc__value')).toBeNull();

    // 每个根节点一个一级分组（相邻分组之间由 CSS 画浅灰横线）
    expect(document.querySelectorAll('.mac-doc__section--d1').length).toBe(2);
    // 二级节点整体缩进一级
    expect(document.querySelectorAll('.mac-doc__section--d2').length).toBe(3);
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

  // —— 关注星标（子节点右侧）：点它把节点订进「项目动态」 ——

  it('只有子节点（一级标签之下）带关注星标，一级标签本身没有', async () => {
    renderCard('CODE-1');
    await screen.findByText('客户信息');
    expect(screen.getByRole('button', { name: '关注客户信息' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '关注载具类型' })).toBeTruthy();
    // 根节点（基础信息 / 硬件）不出星标
    expect(screen.queryByRole('button', { name: /关注基础信息/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /关注硬件/ })).toBeNull();
  });

  it('后端标注列表点亮对应星标（aria-pressed），点星标调切换接口并翻转状态', async () => {
    vi.mocked(fetchInfoNodeMarksApi).mockResolvedValue(['c1']);
    vi.mocked(toggleInfoNodeMarkApi).mockResolvedValue(true);
    renderCard('CODE-1');

    const star = await screen.findByRole('button', { name: '取消关注客户信息' });
    expect(star.getAttribute('aria-pressed')).toBe('true');

    // 未标注的节点：点击 → 调接口 → 变为已关注
    const other = screen.getByRole('button', { name: '关注载具类型' });
    expect(other.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(other);
    expect(toggleInfoNodeMarkApi).toHaveBeenCalledWith('c3');
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '取消关注载具类型' })).toBeTruthy();
    });
  });

  it('关注变化后通知外层（「项目动态」卡刷新）；接口失败回滚星标', async () => {
    const onMarkChange = vi.fn();
    vi.mocked(toggleInfoNodeMarkApi)
      .mockResolvedValueOnce(true)
      .mockRejectedValueOnce(new Error('boom'));
    render(
      <MemoryRouter>
        <ProjectInfoCard projectId="CODE-1" canEdit onMarkChange={onMarkChange} />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '关注客户信息' }));
    await waitFor(() => expect(onMarkChange).toHaveBeenCalledTimes(1));

    // 第二次失败：乐观点亮后回滚为未关注
    fireEvent.click(screen.getByRole('button', { name: '关注载具类型' }));
    await waitFor(() => {
      expect(toggleInfoNodeMarkApi).toHaveBeenCalledWith('c3');
      expect(screen.getByRole('button', { name: '关注载具类型' }).getAttribute('aria-pressed')).toBe('false');
    });
    expect(onMarkChange).toHaveBeenCalledTimes(1);
  });

  // —— 标签池角标：红点 = 该标签下有本机没看过的操作记录（与编辑页行内红点同一套水位） ——

  const chipOf = (title: string) =>
    Array.from(document.querySelectorAll('.mac-tagpool__chip'))
      .find((chip) => chip.textContent?.startsWith(title)) as HTMLElement | undefined;

  it('子节点有未读变动时归到所在的一级标签：该标签右上角出红点，别的标签没有', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ c1: 'rec-1' });
    renderCard('CODE-1');
    await screen.findByText('客户信息');

    await waitFor(() => expect(chipOf('基础信息')?.querySelector('.mac-tagpool__dot')).toBeTruthy());
    expect(chipOf('硬件')?.querySelector('.mac-tagpool__dot')).toBeNull();
    expect(fetchInfoNodeChangeSummaryApi).toHaveBeenCalledWith('CODE-1');
    // 同类标签同时带感叹号（c2 未选择）：感叹号仍在标签角上，红点挪到感叹号右上角
    expect(chipOf('基础信息')?.querySelector('.mac-tagpool__warn')).toBeTruthy();
    expect(chipOf('基础信息')?.querySelector('.mac-tagpool__dot--on-warn')).toBeTruthy();
    // 只有红点的标签不带挪位修饰
    expect(chipOf('硬件')?.querySelector('.mac-tagpool__dot--on-warn')).toBeNull();
  });

  it('一级标签自身的未读记录也出红点；已读（水位=最新记录 id）的标签不出', async () => {
    vi.mocked(fetchInfoNodeChangeSummaryApi).mockResolvedValue({ r2: 'rec-r2', c1: 'rec-c1' });
    localStorage.setItem(
      'project-info-tree:history-seen:CODE-1:admin',
      JSON.stringify({ c1: 'rec-c1' }),
    );
    renderCard('CODE-1');
    await screen.findByText('客户信息');

    await waitFor(() => expect(chipOf('硬件')?.querySelector('.mac-tagpool__dot')).toBeTruthy());
    expect(chipOf('基础信息')?.querySelector('.mac-tagpool__dot')).toBeNull();
  });

  it('标签下方给出口径说明：感叹号=信息未填写，红点=有新变动（看过「历史」后消失）', async () => {
    renderCard('CODE-1');
    await screen.findByText('客户信息');
    expect(screen.getByText('标签下有信息未填写')).toBeTruthy();
    expect(screen.getByText('标签下有新变动，点开对应节点的「历史」后消失')).toBeTruthy();
  });
});
