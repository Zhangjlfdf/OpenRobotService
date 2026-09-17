// 项目详情模板编辑页（仅管理员）——编辑模板节点树，「保存并同步」后所有项目的节点跟随模板更新。
//
// 数据走后端 /api/admin/info-nodes/template（GET 读模板 / POST 保存并同步，均仅管理员可调）。
// 保存流程：本地编辑 → 点「保存并同步」先 dry-run（后端算出影响面）→ 确认弹窗展示
// 「新增 / 更新 / 删除多少节点、涉及多少项目」→ 确认后真正保存并同步，Toast 汇总结果。
// 同步语义（后端 info_template_service）：只变更节点本身——标题/层级/顺序/内容类型以模板为准，
// 各项目已填内容不被整体覆盖（文本保留；file ↔ image 互切保留已传附件；转下拉时旧值能对上选项就选中）；
// 下拉选项以模板为准；删除模板节点会连带删除各项目对应节点及其子树（确认弹窗已明示）。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Navbar, Popup, Toast } from 'tdesign-mobile-react';
import {
  fetchInfoTemplateApi,
  saveInfoTemplateApi,
  type ApiInfoTemplate,
  type ApiInfoTemplateNode,
  type ApiInfoTemplateSyncResult,
} from '@/api/infoNodes';
import { MacMoreHorizontal, MacPlus } from '@/shared/components/macaronIcons';
import {
  appendTemplateNode,
  countTemplateNodes,
  indentTemplateNode,
  INFO_TEMPLATE_MAX_DEPTH,
  moveTemplateSibling,
  newTemplateNode,
  outdentTemplateNode,
  removeTemplateNode,
  subtreeDepth,
  TEMPLATE_CONTENT_TYPE_NAMES,
  TEMPLATE_CONTENT_TYPES,
  templateNodeDepth,
  updateTemplateNode,
  type TemplateContentType,
} from '@/shared/utils/infoTemplateTree';
import { useAuthStore } from '@/stores/auth';

const errMsg = (err: unknown, fallback: string) =>
  err instanceof Error && err.message ? err.message : fallback;

export default function ProjectInfoTemplate() {
  const navigate = useNavigate();
  const permissions = useAuthStore((s) => s.permissions);
  const isAdmin = Array.isArray(permissions) && permissions.includes('admin');

  const [template, setTemplate] = useState<ApiInfoTemplate | null>(null);
  const [nodes, setNodes] = useState<ApiInfoTemplateNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [menuNodeId, setMenuNodeId] = useState<string | null>(null);
  const [deleteNodeId, setDeleteNodeId] = useState<string | null>(null);
  const [leaveConfirm, setLeaveConfirm] = useState(false);
  const [preview, setPreview] = useState<ApiInfoTemplateSyncResult | null>(null);

  const totalCount = useMemo(() => countTemplateNodes(nodes), [nodes]);
  const menuNode = useMemo(
    () => (menuNodeId ? findNodeById(nodes, menuNodeId) : null),
    [nodes, menuNodeId],
  );
  const deleteNode = useMemo(
    () => (deleteNodeId ? findNodeById(nodes, deleteNodeId) : null),
    [nodes, deleteNodeId],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const data = await fetchInfoTemplateApi();
      setTemplate(data);
      setNodes(data.nodes);
      setDirty(false);
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  // 非管理员不请求（接口本身也要求管理员，避免注定 403 的空请求）
  useEffect(() => { if (isAdmin) void load(); }, [isAdmin, load]);

  const mutate = (next: ApiInfoTemplateNode[]) => {
    setNodes(next);
    setDirty(true);
  };

  const commitTitle = (node: ApiInfoTemplateNode, title: string) => {
    setEditingId(null);
    const trimmed = title.trim();
    if (!trimmed || trimmed === node.title) return;
    mutate(updateTemplateNode(nodes, node.id, { title: trimmed }));
  };

  const commitOptions = (node: ApiInfoTemplateNode, raw: string) => {
    const options = raw.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
    if (JSON.stringify(options) === JSON.stringify(node.options ?? [])) return;
    mutate(updateTemplateNode(nodes, node.id, { options }));
  };

  const changeContentType = (node: ApiInfoTemplateNode, type: TemplateContentType) => {
    if (type === node.content_type) return;
    if (type === 'select' && (node.children ?? []).length) {
      Toast({ message: '下拉选择必须是末级：请先删除或移走该节点的子节点', theme: 'warning' });
      return;
    }
    mutate(updateTemplateNode(nodes, node.id, type === 'select'
      ? { content_type: type, options: node.options ?? [] }
      : { content_type: type, options: [] }));
  };

  const addChild = (parent: ApiInfoTemplateNode) => {
    const depth = templateNodeDepth(nodes, parent.id);
    if (depth >= INFO_TEMPLATE_MAX_DEPTH) {
      Toast({ message: `模板最多 ${INFO_TEMPLATE_MAX_DEPTH} 层，不能再往下加`, theme: 'warning' });
      return;
    }
    if (parent.content_type === 'select') {
      Toast({ message: '下拉选择必须是末级，不能给它加子节点', theme: 'warning' });
      return;
    }
    const child = newTemplateNode();
    mutate(appendTemplateNode(nodes, parent.id, child));
    setEditingId(child.id);
  };

  const addRoot = () => {
    const child = newTemplateNode();
    mutate(appendTemplateNode(nodes, null, child));
    setEditingId(child.id);
  };

  const moveUpDown = (node: ApiInfoTemplateNode, delta: -1 | 1) => {
    const next = moveTemplateSibling(nodes, node.id, delta);
    if (next === nodes) return;
    mutate(next);
  };

  const indent = (node: ApiInfoTemplateNode) => {
    const depth = templateNodeDepth(nodes, node.id);
    if (depth + subtreeDepth(node) - 1 >= INFO_TEMPLATE_MAX_DEPTH) {
      Toast({ message: `嵌套后超过 ${INFO_TEMPLATE_MAX_DEPTH} 层，不能再降级`, theme: 'warning' });
      return;
    }
    const next = indentTemplateNode(nodes, node.id);
    if (next === nodes) {
      Toast({ message: '同级第一个节点没有可归入的上级，先用「下移」调整顺序', theme: 'warning' });
      return;
    }
    mutate(next);
  };

  const outdent = (node: ApiInfoTemplateNode) => {
    const next = outdentTemplateNode(nodes, node.id);
    if (next === nodes) return;
    mutate(next);
  };

  const confirmDelete = () => {
    if (!deleteNode) return;
    mutate(removeTemplateNode(nodes, deleteNode.id));
    setDeleteNodeId(null);
    Toast({ message: `已删除「${deleteNode.title}」（保存并同步后各项目才会生效）` });
  };

  // 保存：先 dry-run 预览影响面，确认弹窗里明示新增/更新/删除，再真正保存并同步
  const startSave = async () => {
    if (!nodes.length) {
      Toast({ message: '模板不能为空，至少保留一个节点', theme: 'warning' });
      return;
    }
    setSaving(true);
    try {
      setPreview(await saveInfoTemplateApi(nodes, true));
    } catch (err) {
      Toast({ message: `保存失败：${errMsg(err, '请检查模板后重试')}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const applySave = async () => {
    setSaving(true);
    try {
      const result = await saveInfoTemplateApi(nodes, false);
      setPreview(null);
      setDirty(false);
      const failed = result.failed_projects?.length ?? 0;
      Toast({
        message: failed
          ? `已保存；${failed} 个项目同步失败，请重试`
          : `已保存并同步：影响 ${result.changed_projects} 个项目（新增 ${result.added} · 更新 ${result.updated} · 删除 ${result.deleted}）`,
        theme: failed ? 'warning' : 'success',
      });
      await load();
    } catch (err) {
      Toast({ message: `同步失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const goBack = () => {
    if (dirty) setLeaveConfirm(true);
    else navigate(-1);
  };

  const rowProps = {
    editingId, menuNodeId,
    onStartEdit: setEditingId,
    onCommitTitle: commitTitle,
    onChangeType: changeContentType,
    onCommitOptions: commitOptions,
    onAddChild: addChild,
    onMenu: setMenuNodeId,
  };

  return (
    <div>
      <Navbar title="详情模板" leftArrow onLeftClick={goBack} fixed />
      <div className="mac-page">
        <section className="mac-card mac-card--pad">
          <div className="mac-info__head">
            <div className="mac-info__title-wrap">
              <h3 className="mac-info__title">项目详情模板{dirty ? ' · 有未保存的修改' : ''}</h3>
              <p className="mac-info__subtitle">
                {template
                  ? <>最近更新：{template.updated_at || '—'} · {template.updated_by || '—'} · 共 {totalCount} 个节点 · 涉及 {template.project_count} 个项目</>
                  : '模板编辑后，所有项目的节点会跟随更新'}
              </p>
            </div>
          </div>

          {!isAdmin ? (
            <div className="mac-info__state">
              仅管理员可编辑详情模板
              <div className="mac-info__state-sub">如需调整，请联系管理员</div>
            </div>
          ) : loading ? (
            <div className="mac-info__state">正在加载模板…</div>
          ) : loadError ? (
            <div className="mac-info__state">
              模板加载失败
              <div className="mac-info__state-sub">请检查网络后重试</div>
              <button type="button" className="mac-btn mac-btn--outline" style={{ marginTop: 12 }} onClick={() => void load()}>
                重新加载
              </button>
            </div>
          ) : (
            <>
              <p className="mac-tpl__hint">
                点节点名可改名；「层次」在行末的 ⋯ 菜单里调整（上移/下移/降级/升级）。
                「保存并同步」会把变更应用到这个系统里所有项目的节点（只改节点，各项目已填内容保留），
                删除模板节点会同时删除各项目对应节点及其已填内容（保存前会先给你预览影响面）。
              </p>
              <div className="mac-info__actions mac-tpl__actions">
                <button type="button" className="mac-btn mac-btn--outline mac-info__act" onClick={addRoot}>
                  <MacPlus size={13} />新标签
                </button>
                <button
                  type="button"
                  className="mac-btn mac-btn--primary mac-info__act"
                  disabled={saving}
                  onClick={() => void startSave()}
                >
                  {saving ? '处理中…' : '保存并同步'}
                </button>
              </div>

              <div className="mac-info__tree">
                {nodes.map((node) => (
                  <TemplateRow key={node.id} {...rowProps} node={node} depth={1} />
                ))}
                {!nodes.length && (
                  <div className="mac-info__state">模板还没有节点，点「新标签」添加</div>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      {/* 行内 ⋯ 菜单：层级调整 + 删除 */}
      <Popup visible={!!menuNode} onClose={() => setMenuNodeId(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">节点操作{menuNode ? ` · ${menuNode.title}` : ''}</h4>
          <button type="button" className="mac-choice" onClick={() => { if (menuNode) moveUpDown(menuNode, -1); setMenuNodeId(null); }}>
            <span className="mac-choice__label">同级上移</span>
          </button>
          <button type="button" className="mac-choice" onClick={() => { if (menuNode) moveUpDown(menuNode, 1); setMenuNodeId(null); }}>
            <span className="mac-choice__label">同级下移</span>
          </button>
          <button type="button" className="mac-choice" onClick={() => { if (menuNode) indent(menuNode); setMenuNodeId(null); }}>
            <span className="mac-choice__label">降一级（归入上一个同级节点）</span>
          </button>
          <button type="button" className="mac-choice" onClick={() => { if (menuNode) outdent(menuNode); setMenuNodeId(null); }}>
            <span className="mac-choice__label">升一级（成为上一级的同级）</span>
          </button>
          <button type="button" className="mac-choice" onClick={() => { setDeleteNodeId(menuNodeId); setMenuNodeId(null); }}>
            <span className="mac-choice__label mac-info__danger-text">删除节点</span>
          </button>
        </div>
      </Popup>

      {/* 删除确认：连带子树 + 同步到所有项目的后果 */}
      <Popup visible={!!deleteNode} onClose={() => setDeleteNodeId(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">删除模板节点</h4>
          <p className="mac-info__confirm">
            删除「{deleteNode?.title}」
            {(deleteNode?.children ?? []).length ? `及其 ${countTemplateNodes(deleteNode?.children ?? [])} 个子节点` : ''}？
            保存并同步后，所有项目里的对应节点也会被删除，已填内容一并清除。
          </p>
          <div className="mac-info__confirm-actions">
            <button type="button" className="mac-btn mac-btn--outline" onClick={() => setDeleteNodeId(null)}>取消</button>
            <button type="button" className="mac-btn mac-info__danger" onClick={confirmDelete}>删除</button>
          </div>
        </div>
      </Popup>

      {/* 保存前的影响面预览（后端 dry-run 返回）：删除数会红字提示 */}
      <Popup visible={!!preview} onClose={() => setPreview(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">同步到所有项目</h4>
          {preview && (
            <p className="mac-info__confirm">
              将影响 {preview.changed_projects} / {preview.projects} 个项目：
              新增 {preview.added} 个节点 · 更新 {preview.updated} 个节点
              {preview.deleted > 0 && <span className="mac-info__danger-text"> · 删除 {preview.deleted} 个节点（含已填内容）</span>}
              。未匹配模板的用户自建节点不受影响。
            </p>
          )}
          {(preview?.details ?? []).length > 0 && (
            <div className="mac-tpl__preview-list">
              {(preview?.details ?? []).map((item) => (
                <div key={item.project_id} className="mac-tpl__preview-row">
                  <span className="mac-tpl__preview-name">{item.project_name}</span>
                  <span className="mac-tpl__preview-stat">
                    新增 {item.added} · 更新 {item.updated} · 删除 {item.deleted}
                  </span>
                </div>
              ))}
            </div>
          )}
          <div className="mac-info__confirm-actions">
            <button type="button" className="mac-btn mac-btn--outline" disabled={saving} onClick={() => setPreview(null)}>取消</button>
            <button type="button" className="mac-btn mac-btn--primary" disabled={saving} onClick={() => void applySave()}>
              {saving ? '正在同步…' : '确认保存并同步'}
            </button>
          </div>
        </div>
      </Popup>

      {/* 离开确认：有未保存修改时返回要二次确认 */}
      <Popup visible={leaveConfirm} onClose={() => setLeaveConfirm(false)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">离开页面</h4>
          <p className="mac-info__confirm">有未保存的修改，直接返回将丢弃这些改动。</p>
          <div className="mac-info__confirm-actions">
            <button type="button" className="mac-btn mac-btn--outline" onClick={() => setLeaveConfirm(false)}>继续编辑</button>
            <button type="button" className="mac-btn mac-info__danger" onClick={() => navigate(-1)}>放弃修改并返回</button>
          </div>
        </div>
      </Popup>
    </div>
  );
}

function findNodeById(nodes: ApiInfoTemplateNode[], id: string): ApiInfoTemplateNode | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    const hit = findNodeById(node.children ?? [], id);
    if (hit) return hit;
  }
  return null;
}

// —— 模板树行（递归） ——

interface TemplateRowProps {
  node: ApiInfoTemplateNode;
  depth: number;
  editingId: string | null;
  menuNodeId: string | null;
  onStartEdit: (id: string | null) => void;
  onCommitTitle: (node: ApiInfoTemplateNode, title: string) => void;
  onChangeType: (node: ApiInfoTemplateNode, type: TemplateContentType) => void;
  onCommitOptions: (node: ApiInfoTemplateNode, raw: string) => void;
  onAddChild: (node: ApiInfoTemplateNode) => void;
  onMenu: (id: string | null) => void;
}

function TemplateRow(props: TemplateRowProps) {
  const { node, depth } = props;
  const children = node.children ?? [];
  return (
    <div className={depth > 1 ? 'mac-info-subtree' : undefined}>
      <div className={`mac-tpl-row mac-tpl-row--d${Math.min(depth, INFO_TEMPLATE_MAX_DEPTH)}`}>
        <div className="mac-tpl-row__head">
          {props.editingId === node.id ? (
            <input
              className="mac-info-row__input"
              autoFocus
              defaultValue={node.title}
              aria-label="节点名称"
              onBlur={(event) => props.onCommitTitle(node, event.target.value)}
              onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
            />
          ) : (
            <button
              type="button"
              className="mac-tpl-row__title"
              onClick={() => props.onStartEdit(node.id)}
              aria-label={`编辑${node.title}`}
              title="点按改名"
            >
              {node.title}
            </button>
          )}
          <select
            className="mac-tpl-row__type"
            value={node.content_type}
            aria-label={`${node.title}内容类型`}
            onChange={(event) => props.onChangeType(node, event.target.value as TemplateContentType)}
          >
            {TEMPLATE_CONTENT_TYPES.map((type) => (
              <option key={type} value={type}>{TEMPLATE_CONTENT_TYPE_NAMES[type]}</option>
            ))}
          </select>
          <button
            type="button"
            className="mac-info-row__op"
            aria-label={`在${node.title}下新增`}
            title="新增子节点"
            onClick={() => props.onAddChild(node)}
          >
            <MacPlus size={15} />
          </button>
          <button
            type="button"
            className="mac-info-row__op"
            aria-label={`${node.title}更多操作`}
            onClick={() => props.onMenu(node.id)}
          >
            <MacMoreHorizontal size={15} />
          </button>
        </div>
        {node.content_type === 'select' && (
          <div className="mac-tpl-row__options">
            <span className="mac-tpl-row__options-label">下拉选项</span>
            <input
              className="mac-tpl-row__options-input"
              defaultValue={(node.options ?? []).join('，')}
              placeholder="用逗号分隔，如：试点项目，PK项目"
              aria-label={`${node.title}下拉选项`}
              onBlur={(event) => props.onCommitOptions(node, event.target.value)}
            />
          </div>
        )}
      </div>
      {children.map((child) => (
        <TemplateRow key={child.id} {...props} node={child} depth={depth + 1} />
      ))}
    </div>
  );
}
