// 编辑项目信息 —— 项目信息树编辑页（对照原型 routes/projects.$id_.edit.tsx + components/tree/ProjectInformationTree.tsx）。
// 集中管理节点：新增 / 改名 / 改内容形式 / 删除 / 长按拖动调整从属 / 全部展开折叠 / 四种内容形式（文字、下拉、文件、图片）。
//
// 数据走后端 /api/admin/info-nodes/*（逐节点 CRUD，数据层见 shared/utils/projectInfoTree.ts）：变更先本地乐观更新，
// 接口失败时提示并整树重读回滚；文件/图片内容先上传资源管理服务（与项目文档同一接口）再写节点值。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Input, Navbar, Popup, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import { type ApiInfoTreeImportNode } from '@/api/infoNodes';
import {
  MacChevronDown, MacChevronRight, MacChevronsDownUp, MacChevronsUpDown, MacDownload, MacFileText,
  MacGripVertical, MacHistory, MacImage, MacMoreHorizontal, MacPencil, MacPlus, MacTrash2, MacUpload,
} from '@/shared/components/macaronIcons';
import {
  computeInfoCompleteness,
  createInfoNode,
  deleteInfoNode,
  formatFileSize,
  importInfoTemplate,
  importInfoTree,
  loadCollapsedIds,
  loadInfoNodes,
  isInfoNodeVisible,
  moveInfoNode,
  normalizeImportNodes,
  patchInfoNode,
  PROJECT_INFO_MAX_DEPTH,
  removeInfoNode,
  saveCollapsedIds,
  updateInfoNode,
  visibleInfoNodes,
  type ProjectInfoContentType,
  type ProjectInfoFileValue,
  type ProjectInfoNode,
  type ProjectInfoSelectValue,
} from '@/shared/utils/projectInfoTree';

type DropMode = 'child' | 'before';
const CONTENT_TYPE_NAMES: Record<ProjectInfoContentType, string> = {
  text: '文字输入',
  select: '下拉选择',
  file: '上传文件',
  image: '上传图片',
};

export default function ProjectInfoEdit() {
  const { id = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const username = useAuthStore((s) => s.username);
  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);

  const [nodes, setNodes] = useState<ProjectInfoNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [importing, setImporting] = useState(false);
  const [pendingImport, setPendingImport] = useState<{ nodes: ApiInfoTreeImportNode[]; count: number } | null>(null);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(() => loadCollapsedIds(id));
  const [editingId, setEditingId] = useState<string | null>(null);
  const [menuNode, setMenuNode] = useState<ProjectInfoNode | null>(null);
  const [historyNode, setHistoryNode] = useState<ProjectInfoNode | null>(null);
  const [deleteNode, setDeleteNode] = useState<ProjectInfoNode | null>(null);
  const [selectNode, setSelectNode] = useState<ProjectInfoNode | null>(null);
  const [titleOptionsNode, setTitleOptionsNode] = useState<ProjectInfoNode | null>(null);
  const [draftOption, setDraftOption] = useState('');
  const [draftTitleOptions, setDraftTitleOptions] = useState('');
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<{ id: string; mode: DropMode } | null>(null);
  const [uploadingNodeId, setUploadingNodeId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState('');
  const holdTimer = useRef<number | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);

  // 项目名称仅用于页头副标题（真实数据；失败静默降级为项目编号）
  useEffect(() => {
    if (!id) return;
    request<{ name?: string }>(`/projects/${id}`)
      .then((data) => setProjectName(data.name || ''))
      .catch(() => setProjectName(''));
  }, [id, request]);

  // 信息树真实数据：打开页面读取；保存失败需回滚时整树重读
  const reload = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setLoadError(false);
    try {
      setNodes(await loadInfoNodes(id));
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => { saveCollapsedIds(id, collapsedIds); }, [id, collapsedIds]);

  const byParent = useMemo(() => {
    const map = new Map<string | null, ProjectInfoNode[]>();
    nodes.forEach((node) => {
      const siblings = map.get(node.parent_id) ?? [];
      siblings.push(node);
      map.set(node.parent_id, siblings);
    });
    map.forEach((items) => items.sort((a, b) => a.sort_order - b.sort_order));
    return map;
  }, [nodes]);

  const roots = byParent.get(null) ?? [];
  // 完整度只统计当前看得见的字段（区域联动隐藏的字段不该计入「缺 N」）
  const completeness = useMemo(() => computeInfoCompleteness(visibleInfoNodes(nodes)), [nodes]);

  const errMsg = (err: unknown) => (err instanceof Error && err.message ? err.message : '请稍后重试');

  // 写入后端：先本地乐观更新（界面即时反馈），成功后提示；失败时提示原因并整树重读回滚
  const applyMutation = async (optimistic: ProjectInfoNode[], action: () => Promise<unknown>, successMsg: string) => {
    setNodes(optimistic);
    try {
      await action();
      Toast({ message: successMsg, theme: 'success' });
    } catch (err) {
      Toast({ message: `保存失败：${errMsg(err)}`, theme: 'error' });
      void reload();
    }
  };

  const depthOf = (node: ProjectInfoNode): number => {
    let depth = 1;
    let parent = nodes.find((item) => item.id === node.parent_id);
    while (parent && depth < PROJECT_INFO_MAX_DEPTH + 1) {
      depth += 1;
      parent = nodes.find((item) => item.id === parent?.parent_id);
    }
    return depth;
  };

  const descendantsOf = (nodeId: string): Set<string> => {
    const result = new Set<string>();
    const walk = (parentId: string) => (byParent.get(parentId) ?? []).forEach((child) => {
      result.add(child.id);
      walk(child.id);
    });
    walk(nodeId);
    return result;
  };

  const addNode = async (parent: ProjectInfoNode | null) => {
    if (parent && depthOf(parent) >= PROJECT_INFO_MAX_DEPTH) {
      Toast({ message: '信息维度过深，建议拆分或合并', theme: 'warning' });
      return;
    }
    const siblings = byParent.get(parent?.id ?? null) ?? [];
    try {
      // 新节点由前端生成 id、后端落库后返回，直接追加（无需乐观占位）
      const node = await createInfoNode(id, parent?.id ?? null, siblings.length);
      setNodes((prev) => [...prev, node]);
      if (parent) {
        setCollapsedIds((prev) => { const next = new Set(prev); next.delete(parent.id); return next; });
      }
      setEditingId(node.id);
    } catch (err) {
      Toast({ message: `新增失败：${errMsg(err)}`, theme: 'error' });
    }
  };

  const renameNode = (node: ProjectInfoNode, title: string) =>
    void applyMutation(patchInfoNode(nodes, node.id, { title }), () => updateInfoNode(node, { title }), '名称已保存');
  const saveValue = (node: ProjectInfoNode, value: unknown) =>
    void applyMutation(patchInfoNode(nodes, node.id, { value }), () => updateInfoNode(node, { value }), '内容已保存');

  const changeContentType = (node: ProjectInfoNode, type: ProjectInfoContentType) => {
    const value = type === 'select' ? { selected: '', options: [] } : type === 'text' ? '' : {};
    void applyMutation(
      patchInfoNode(nodes, node.id, { content_type: type, value }),
      () => updateInfoNode(node, { content_type: type, value }),
      '内容形式已切换',
    );
    setMenuNode(null);
  };

  const confirmDelete = () => {
    if (!deleteNode) return;
    void applyMutation(removeInfoNode(nodes, deleteNode.id), () => deleteInfoNode(deleteNode.id), '节点及其子节点已删除');
    setDeleteNode(null);
  };

  const uploadNodeFile = async (node: ProjectInfoNode, file: File) => {
    setUploadingNodeId(node.id);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('owner_id', username || 'admin');
      form.append('resource_type', 'document');
      form.append('category', '项目信息');
      form.append('description', `项目 ${id} 信息节点「${node.title}」`);
      const resource = await request<{ id: number; resource_name: string }>('/resource-manager/resources/', {
        method: 'POST',
        body: form,
      });
      const value = { name: file.name, resource_id: resource.id, size: file.size };
      await updateInfoNode(node, { value });
      setNodes((prev) => patchInfoNode(prev, node.id, { value }));
      Toast({ message: '文件已上传并保存', theme: 'success' });
    } catch (err) {
      Toast({ message: `上传失败：${errMsg(err)}`, theme: 'error' });
    } finally {
      setUploadingNodeId(null);
    }
  };

  const removeNodeFile = (node: ProjectInfoNode) => {
    void applyMutation(patchInfoNode(nodes, node.id, { value: {} }), () => updateInfoNode(node, { value: {} }), '已移除文件引用');
  };

  // —— 长按拖动调整从属（原生 Pointer 事件，不引入依赖；与设计稿一致 400ms 长按） ——

  const clearHold = () => {
    if (holdTimer.current != null) { window.clearTimeout(holdTimer.current); holdTimer.current = null; }
  };

  const startHold = (nodeId: string) => {
    clearHold();
    holdTimer.current = window.setTimeout(() => {
      setDraggingId(nodeId);
      navigator.vibrate?.(35);
    }, 400);
  };

  const pointerTarget = (clientX: number, clientY: number): { target: ProjectInfoNode; mode: DropMode } | null => {
    const element = document.elementFromPoint(clientX, clientY)?.closest<HTMLElement>('[data-info-node]');
    if (!element) return null;
    const target = nodes.find((node) => node.id === element.dataset.infoNode);
    if (!target) return null;
    const rect = element.getBoundingClientRect();
    return { target, mode: clientY < rect.top + rect.height * 0.27 ? 'before' : 'child' };
  };

  const dragMove = (event: React.PointerEvent) => {
    if (!draggingId) return;
    const hit = pointerTarget(event.clientX, event.clientY);
    if (hit && hit.target.id !== draggingId) setDropTarget({ id: hit.target.id, mode: hit.mode });
  };

  const drop = (event: React.PointerEvent) => {
    clearHold();
    if (!draggingId) return;
    const hit = pointerTarget(event.clientX, event.clientY);
    if (hit) moveNode(draggingId, hit.target, hit.mode);
    setDraggingId(null);
    setDropTarget(null);
  };

  const moveNode = (draggedId: string, target: ProjectInfoNode, mode: DropMode) => {
    if (draggedId === target.id || descendantsOf(draggedId).has(target.id)) return;
    const dragged = nodes.find((node) => node.id === draggedId);
    if (!dragged) return;
    const parentId = mode === 'child' ? target.id : target.parent_id;
    const targetDepth = mode === 'child' ? depthOf(target) + 1 : depthOf(target);
    const subtreeDepth = Math.max(0, ...[...descendantsOf(dragged.id)].map((descendantId) => {
      const item = nodes.find((node) => node.id === descendantId);
      return item ? depthOf(item) - depthOf(dragged) : 0;
    }));
    if (targetDepth + subtreeDepth > PROJECT_INFO_MAX_DEPTH) {
      Toast({ message: '信息维度过深，建议拆分或合并', theme: 'warning' });
      return;
    }
    const targetSiblings = byParent.get(parentId) ?? [];
    const sortOrder = mode === 'child' ? targetSiblings.length : target.sort_order;
    void applyMutation(
      patchInfoNode(nodes, dragged.id, { parent_id: parentId, sort_order: sortOrder }),
      () => moveInfoNode(dragged, parentId, sortOrder),
      '从属关系已调整',
    );
  };

  const expandAll = () => setCollapsedIds(new Set());
  const collapseAll = () => setCollapsedIds(new Set(
    nodes.filter((node) => (byParent.get(node.id) ?? []).length > 0).map((node) => node.id),
  ));

  // —— 下拉选项管理（选项由用户自行增删） ——

  const selectValue = (selectNode?.value ?? {}) as Partial<ProjectInfoSelectValue>;
  const saveSelectValue = (nextNode: ProjectInfoNode | null, value: Partial<ProjectInfoSelectValue>) => {
    if (!nextNode) return;
    const merged = { selected: value.selected ?? '', options: value.options ?? [] };
    void applyMutation(
      patchInfoNode(nodes, nextNode.id, { value: merged }),
      () => updateInfoNode(nextNode, { value: merged }),
      '选项已保存',
    );
    setSelectNode((prev) => (prev && prev.id === nextNode.id ? { ...prev, value: merged } : prev));
  };

  const openTitleOptions = (node: ProjectInfoNode) => {
    const options = ((node.value ?? {}) as { titleOptions?: string[] }).titleOptions ?? [];
    setDraftTitleOptions(options.join('，'));
    setTitleOptionsNode(node);
    setMenuNode(null);
  };

  const saveTitleOptions = (options: string[]) => {
    if (!titleOptionsNode) return;
    const value = options.length ? { titleOptions: options } : {};
    void applyMutation(
      patchInfoNode(nodes, titleOptionsNode.id, { value }),
      () => updateInfoNode(titleOptionsNode, { value }),
      options.length ? '标题备选项已保存' : '已改回手动输入标题',
    );
    setTitleOptionsNode(null);
  };

  // —— 整树导入 / 预设模板初始化（import 接口：先清空旧树再写入，需二次确认） ——

  const countImportNodes = (list: ApiInfoTreeImportNode[]): number =>
    list.reduce((sum, item) => sum + 1 + (item.children ? countImportNodes(item.children) : 0), 0);

  const pickImportFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const nodes = normalizeImportNodes(JSON.parse(String(reader.result ?? '')));
        if (!nodes.length) {
          Toast({ message: '文件中没有信息节点', theme: 'warning' });
          return;
        }
        setPendingImport({ nodes, count: countImportNodes(nodes) });
      } catch (err) {
        Toast({ message: `文件解析失败：${errMsg(err)}`, theme: 'error' });
      }
    };
    reader.readAsText(file);
  };

  const importTree = async (input: ApiInfoTreeImportNode[], successMsg: (imported: number) => string) => {
    if (!id) return;
    setImporting(true);
    try {
      const imported = await importInfoTree(id, input);
      setNodes(await loadInfoNodes(id));
      setCollapsedIds(new Set());
      Toast({ message: successMsg(imported), theme: 'success' });
      setPendingImport(null);
    } catch (err) {
      Toast({ message: `导入失败：${errMsg(err)}`, theme: 'error' });
    } finally {
      setImporting(false);
    }
  };

  /** 空树项目「按预设模板初始化」：模板在后端（project_type → project_templates/*.yaml），
   *  与新建项目同一份定义，前端只触发，不再自带一份结构副本 */
  const initFromTemplate = async () => {
    if (!id) return;
    setImporting(true);
    try {
      const imported = await importInfoTemplate(id);
      if (!imported) {
        Toast({ message: '后端模板为空，未写入节点', theme: 'warning' });
        return;
      }
      setNodes(await loadInfoNodes(id));
      setCollapsedIds(new Set());
      Toast({ message: `已按预设模板初始化 ${imported} 个节点`, theme: 'success' });
    } catch (err) {
      Toast({ message: `初始化失败：${errMsg(err)}`, theme: 'error' });
    } finally {
      setImporting(false);
    }
  };

  const rowProps = {
    byParent, collapsedIds, editingId, draggingId, dropTarget, uploadingNodeId,
    onToggle: (nodeId: string) => setCollapsedIds((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) next.delete(nodeId); else next.add(nodeId);
      return next;
    }),
    onEdit: setEditingId,
    onAdd: addNode,
    onRename: renameNode,
    onMenu: setMenuNode,
    onHistory: setHistoryNode,
    onSaveValue: saveValue,
    onUpload: uploadNodeFile,
    onRemoveFile: removeNodeFile,
    onOpenSelectEditor: (node: ProjectInfoNode) => { setDraftOption(''); setSelectNode(node); },
    onHoldStart: startHold,
    onHoldEnd: clearHold,
    onDragMove: dragMove,
    onDrop: drop,
  };

  return (
    <div>
      <Navbar title="编辑项目信息" leftArrow onLeftClick={() => navigate(-1)} fixed />
      <div style={{ padding: 16, paddingTop: 64 }}>
        <section className="mac-card mac-card--pad">
          <div className="mac-info__head">
            <div className="mac-info__title-wrap">
              <h3 className="mac-info__title">信息节点</h3>
              <p className="mac-info__subtitle">
                {projectName || `项目 ${id}`} · 长按节点可拖动调整从属
              </p>
            </div>
            <div className="mac-info__actions">
              <button type="button" className="mac-btn mac-btn--ghost mac-info__iconbtn" onClick={expandAll} title="全部展开" aria-label="全部展开"><MacChevronsUpDown size={15} /></button>
              <button type="button" className="mac-btn mac-btn--ghost mac-info__iconbtn" onClick={collapseAll} title="全部折叠" aria-label="全部折叠"><MacChevronsDownUp size={15} /></button>
              <button
                type="button"
                className="mac-btn mac-btn--outline mac-info__act"
                disabled={importing}
                onClick={() => importInputRef.current?.click()}
              >
                <MacUpload size={13} />{importing ? '导入中…' : '文件导入'}
              </button>
              <input
                ref={importInputRef}
                type="file"
                accept=".json,application/json"
                hidden
                onChange={(event) => {
                  const selected = event.target.files?.[0];
                  if (selected) pickImportFile(selected);
                  event.target.value = '';
                }}
              />
              <button type="button" className="mac-btn mac-btn--primary mac-info__act" onClick={() => void addNode(null)}>
                <MacPlus size={13} />新标签
              </button>
            </div>
          </div>

          {loading ? (
            <div className="mac-info__state">正在加载信息节点…</div>
          ) : loadError ? (
            <div className="mac-info__state">
              信息节点加载失败
              <div className="mac-info__state-sub">请检查网络后重试</div>
              <button type="button" className="mac-btn mac-btn--outline" style={{ marginTop: 12 }} onClick={() => void reload()}>
                重新加载
              </button>
            </div>
          ) : roots.length === 0 ? (
            <div className="mac-info__state">
              还没有信息节点，点击右上角「新标签」创建，或按预设模板初始化
              <div className="mac-info__state-sub">保存后对所有协作者可见</div>
              <button
                type="button"
                className="mac-btn mac-btn--outline"
                style={{ marginTop: 12 }}
                disabled={importing}
                onClick={() => void initFromTemplate()}
              >
                {importing ? '初始化中…' : '按预设模板初始化'}
              </button>
            </div>
          ) : (
            <div className="mac-info__tree">
              {roots.map((root) => (
                <InfoRow key={root.id} {...rowProps} node={root} depth={1} missingCount={completeness.get(root.id)?.empty} />
              ))}
            </div>
          )}
        </section>
      </div>

      {/* 节点操作菜单：内容形式（仅末级）/ 标题备选项（非末级）/ 删除；改名走行内的铅笔按钮 */}
      <Popup visible={!!menuNode} onClose={() => setMenuNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">节点操作{menuNode ? ` · ${menuNode.title}` : ''}</h4>
          {menuNode && (byParent.get(menuNode.id) ?? []).length === 0
            ? (Object.keys(CONTENT_TYPE_NAMES) as ProjectInfoContentType[]).map((type) => (
                <button key={type} type="button" className="mac-choice" onClick={() => menuNode && changeContentType(menuNode, type)}>
                  <span className="mac-choice__label">
                    {CONTENT_TYPE_NAMES[type]}{menuNode.content_type === type ? ' · 当前' : ''}
                  </span>
                </button>
              ))
            : (
              <button type="button" className="mac-choice" onClick={() => menuNode && openTitleOptions(menuNode)}>
                <span className="mac-choice__label">标题改为下拉选择</span>
              </button>
            )}
          <button type="button" className="mac-choice" onClick={() => { setDeleteNode(menuNode); setMenuNode(null); }}>
            <span className="mac-choice__label mac-info__danger-text"><MacTrash2 size={14} />删除节点</span>
          </button>
        </div>
      </Popup>

      {/* 删除确认（删除会连带整棵子树，且对所有协作者生效） */}
      <Popup visible={!!deleteNode} onClose={() => setDeleteNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">删除节点</h4>
          <p className="mac-info__confirm">删除「{deleteNode?.title}」及其所有子节点？删除后所有协作者都将不再看到这些节点。</p>
          <div className="mac-info__confirm-actions">
            <button type="button" className="mac-btn mac-btn--outline" onClick={() => setDeleteNode(null)}>取消</button>
            <button type="button" className="mac-btn mac-info__danger" onClick={confirmDelete}>删除</button>
          </div>
        </div>
      </Popup>

      {/* 文件导入确认（import 接口先清空旧树再写入，需二次确认） */}
      <Popup visible={!!pendingImport} onClose={() => setPendingImport(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">导入信息树</h4>
          <p className="mac-info__confirm">
            将用文件中的 {pendingImport?.count ?? 0} 个节点替换该项目全部现有节点，原内容不可恢复。
          </p>
          <div className="mac-info__confirm-actions">
            <button type="button" className="mac-btn mac-btn--outline" disabled={importing} onClick={() => setPendingImport(null)}>取消</button>
            <button
              type="button"
              className="mac-btn mac-btn--primary"
              disabled={importing}
              onClick={() => pendingImport && void importTree(pendingImport.nodes, (imported) => `已导入 ${imported} 个节点`)}
            >
              {importing ? '导入中…' : '确认导入'}
            </button>
          </div>
        </div>
      </Popup>

      {/* 编辑历史：后端无接口，先占位说明（不虚构真实操作记录） */}
      <Popup visible={!!historyNode} onClose={() => setHistoryNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">编辑历史{historyNode ? ` · ${historyNode.title}` : ''}</h4>
          <div className="mac-info__state">
            编辑历史接口未接入
            <div className="mac-info__state-sub">接入后将显示「谁 · 什么时候 · 改了什么」</div>
          </div>
        </div>
      </Popup>

      {/* 下拉选项管理：选项由用户自行增删 */}
      <Popup visible={!!selectNode} onClose={() => setSelectNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">下拉选项{selectNode ? ` · ${selectNode.title}` : ''}</h4>
          {(selectValue.options ?? []).length === 0 && (
            <p className="mac-info__state-sub" style={{ padding: '10px 0' }}>还没有选项，先添加一个</p>
          )}
          {(selectValue.options ?? []).map((option) => (
            <div key={option} className="mac-opt-row">
              <span className="mac-opt-row__label">{option}</span>
              <button
                type="button"
                className="mac-info__iconbtn"
                aria-label={`删除选项 ${option}`}
                onClick={() => selectNode && saveSelectValue(selectNode, { selected: selectValue.selected, options: (selectValue.options ?? []).filter((item) => item !== option) })}
              >
                <MacTrash2 size={14} />
              </button>
            </div>
          ))}
          <div className="mac-opt-add">
            <Input value={draftOption} onChange={(v: string | number) => setDraftOption(String(v))} placeholder="输入新选项" />
            <button
              type="button"
              className="mac-btn mac-btn--primary"
              onClick={() => {
                const option = draftOption.trim();
                if (!option || !selectNode) return;
                saveSelectValue(selectNode, { selected: selectValue.selected, options: [...(selectValue.options ?? []), option] });
                setDraftOption('');
              }}
            >
              添加
            </button>
          </div>
          <div className="mac-sheet__actions">
            <button type="button" className="mac-btn mac-btn--primary mac-btn--block" onClick={() => setSelectNode(null)}>完成</button>
          </div>
        </div>
      </Popup>

      {/* 标题备选项（非末级节点标题可在候选中选择；留空改回手动输入） */}
      <Popup visible={!!titleOptionsNode} onClose={() => setTitleOptionsNode(null)} placement="bottom" showOverlay>
        <div className="mac-sheet">
          <h4 className="mac-sheet__title">标题备选项{titleOptionsNode ? ` · ${titleOptionsNode.title}` : ''}</h4>
          <Input value={draftTitleOptions} onChange={(v: string | number) => setDraftTitleOptions(String(v))} placeholder="用逗号分隔，留空则改回手动输入" />
          <div className="mac-info__confirm-actions" style={{ marginTop: 16 }}>
            <button type="button" className="mac-btn mac-btn--outline" onClick={() => saveTitleOptions([])}>清除</button>
            <button type="button" className="mac-btn mac-btn--primary" onClick={() => saveTitleOptions(draftTitleOptions.split(/[,，]/).map((item) => item.trim()).filter(Boolean))}>确定</button>
          </div>
        </div>
      </Popup>
    </div>
  );
}

// —— 树行（递归） ——

interface InfoRowProps {
  node: ProjectInfoNode;
  depth: number;
  missingCount?: number | undefined;
  byParent: Map<string | null, ProjectInfoNode[]>;
  collapsedIds: Set<string>;
  editingId: string | null;
  draggingId: string | null;
  dropTarget: { id: string; mode: DropMode } | null;
  uploadingNodeId: string | null;
  onToggle: (id: string) => void;
  onEdit: (id: string | null) => void;
  onAdd: (parent: ProjectInfoNode) => void;
  onRename: (node: ProjectInfoNode, title: string) => void;
  onMenu: (node: ProjectInfoNode) => void;
  onHistory: (node: ProjectInfoNode) => void;
  onSaveValue: (node: ProjectInfoNode, value: unknown) => void;
  onUpload: (node: ProjectInfoNode, file: File) => void;
  onRemoveFile: (node: ProjectInfoNode) => void;
  onOpenSelectEditor: (node: ProjectInfoNode) => void;
  onHoldStart: (id: string) => void;
  onHoldEnd: () => void;
  onDragMove: (event: React.PointerEvent) => void;
  onDrop: (event: React.PointerEvent) => void;
}

function InfoRow(props: InfoRowProps) {
  const { node, depth } = props;
  const allChildren = props.byParent.get(node.id) ?? [];
  // 区域联动字段按所选区域显隐（节点仍在，只是不渲染）；是否存在子节点按完整列表判断
  const children = allChildren.filter((child) => isInfoNodeVisible(child, allChildren));
  const isLeaf = allChildren.length === 0;
  const level = Math.min(depth, PROJECT_INFO_MAX_DEPTH);
  const isCollapsed = props.collapsedIds.has(node.id);
  const activeDrop = props.dropTarget?.id === node.id;
  const titleOptions = ((node.value ?? {}) as { titleOptions?: string[] }).titleOptions ?? [];
  const classNames = [
    'mac-info-row',
    `mac-info-row--d${level}`,
    props.draggingId === node.id ? 'is-dragging' : '',
    activeDrop && props.dropTarget?.mode === 'child' ? 'is-drop-child' : '',
    activeDrop && props.dropTarget?.mode === 'before' ? 'is-drop-before' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={depth > 1 ? 'mac-info-subtree' : undefined}>
      <div data-info-node={node.id} className={classNames}>
        <div className="mac-info-row__main">
          <span
            className="mac-info-row__grip"
            aria-label="长按拖动调整从属"
            onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); props.onHoldStart(node.id); }}
            onPointerMove={props.onDragMove}
            onPointerUp={props.onDrop}
            onPointerCancel={props.onHoldEnd}
          >
            <MacGripVertical size={14} />
          </span>
          <button
            type="button"
            className="mac-info-row__toggle"
            disabled={isLeaf}
            onClick={() => props.onToggle(node.id)}
            aria-label={isCollapsed ? '展开' : '收起'}
          >
            {children.length ? (isCollapsed ? <MacChevronRight size={15} /> : <MacChevronDown size={15} />) : <span className="mac-info-row__toggle-ghost" />}
          </button>
          {props.editingId === node.id ? (
            <input
              className="mac-info-row__input"
              autoFocus
              defaultValue={node.title}
              onBlur={(event) => {
                const title = event.target.value.trim();
                if (title && title !== node.title) props.onRename(node, title);
                props.onEdit(null);
              }}
              onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur(); }}
            />
          ) : !isLeaf && titleOptions.length ? (
            <select
              className="mac-info-row__select"
              value={titleOptions.includes(node.title) ? node.title : ''}
              aria-label={`${node.title}标题`}
              onChange={(event) => event.target.value && props.onRename(node, event.target.value)}
            >
              <option value="" disabled>{node.title}</option>
              {titleOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          ) : (
            <span className="mac-info-row__title">{node.title}</span>
          )}
          {level === 1 && (props.missingCount ?? 0) > 0 && (
            <span className="mac-info-row__missing" title={`${props.missingCount} 项信息未填写`}>缺 {props.missingCount}</span>
          )}
          <div className="mac-info-row__ops">
            {/* 行内编辑按钮：一步进入改名，不必展开「⋯」菜单（叶子节点的内容本来就已是行内直接编辑） */}
            <button type="button" className="mac-info-row__op" onClick={() => props.onEdit(node.id)} aria-label={`编辑${node.title}`} title="编辑节点"><MacPencil size={15} /></button>
            <button type="button" className="mac-info-row__op" onClick={() => props.onAdd(node)} aria-label={`在${node.title}下新增`}><MacPlus size={15} /></button>
            <button type="button" className="mac-info-row__op" onClick={() => props.onHistory(node)} aria-label={`查看${node.title}的编辑历史`}><MacHistory size={15} /></button>
            <button type="button" className="mac-info-row__op" onClick={() => props.onMenu(node)} aria-label="更多操作"><MacMoreHorizontal size={15} /></button>
          </div>
        </div>
        {isLeaf && <NodeContent {...props} />}
      </div>
      {!isCollapsed && children.map((child) => (
        <InfoRow key={child.id} {...props} node={child} depth={depth + 1} missingCount={undefined} />
      ))}
    </div>
  );
}

// —— 末级节点内容编辑（四种内容形式） ——

function NodeContent(props: InfoRowProps) {
  const { node } = props;
  if (node.content_type === 'select') {
    const data = (node.value ?? {}) as Partial<ProjectInfoSelectValue>;
    return (
      <div className="mac-info-node__select">
        <select
          value={data.selected ?? ''}
          aria-label={`${node.title}内容`}
          onChange={(event) => props.onSaveValue(node, { selected: event.target.value, options: data.options ?? [] })}
        >
          <option value="">请选择</option>
          {(data.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
        <button type="button" className="mac-btn mac-btn--outline mac-info-node__manage" onClick={() => props.onOpenSelectEditor(node)}>
          管理
        </button>
      </div>
    );
  }
  if (node.content_type === 'file' || node.content_type === 'image') {
    return <FileContent node={node} uploading={props.uploadingNodeId === node.id} onUpload={props.onUpload} onRemove={props.onRemoveFile} />;
  }
  return (
    <textarea
      className="mac-info-node__text"
      aria-label={`${node.title}内容`}
      defaultValue={typeof node.value === 'string' ? node.value : ''}
      placeholder="填写内容"
      rows={1}
      onBlur={(event) => { if (event.target.value !== (typeof node.value === 'string' ? node.value : '')) props.onSaveValue(node, event.target.value); }}
    />
  );
}

function FileContent({ node, uploading, onUpload, onRemove }: {
  node: ProjectInfoNode;
  uploading: boolean;
  onUpload: (node: ProjectInfoNode, file: File) => void;
  onRemove: (node: ProjectInfoNode) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [imageBroken, setImageBroken] = useState(false);
  const file = (node.value ?? {}) as Partial<ProjectInfoFileValue>;
  const hasFile = !!file.name && file.resource_id != null;
  const downloadUrl = hasFile ? `${API_CONFIG.ADMIN.BASE_URL}/resource-manager/resources/${file.resource_id}/download` : '';
  return (
    <div className="mac-info-node__file">
      <input
        ref={inputRef}
        type="file"
        hidden
        accept={node.content_type === 'image' ? 'image/*' : undefined}
        onChange={(event) => {
          const selected = event.target.files?.[0];
          if (selected) onUpload(node, selected);
          event.target.value = '';
        }}
      />
      {hasFile ? (
        <div className="mac-info-node__filebox">
          {/* 图片节点带缩略图（真实资源服务地址；加载失败降级为普通附件行） */}
          {node.content_type === 'image' && !imageBroken && (
            <a href={downloadUrl} target="_blank" rel="noreferrer" className="mac-info-node__thumblink">
              <img className="mac-doc__thumb" src={downloadUrl} alt={file.name} onError={() => setImageBroken(true)} />
            </a>
          )}
          <div className="mac-info-node__filerow">
            {node.content_type === 'image' ? <MacImage size={15} /> : <MacFileText size={15} />}
            <span className="mac-info-node__filename">{file.name}</span>
            {typeof file.size === 'number' && <span className="mac-info-node__filesize">{formatFileSize(file.size)}</span>}
            <a
              className="mac-doc__dl"
              href={downloadUrl}
              download={file.name}
              aria-label="下载"
            >
              <MacDownload size={15} />
            </a>
            <button type="button" className="mac-info-row__op mac-info__danger-text" onClick={() => onRemove(node)} aria-label="移除文件"><MacTrash2 size={14} /></button>
          </div>
        </div>
      ) : (
        <button type="button" className="mac-btn mac-btn--outline mac-info-node__choose" disabled={uploading} onClick={() => inputRef.current?.click()}>
          {node.content_type === 'image' ? <MacImage size={13} /> : <MacFileText size={13} />}
          {uploading ? '上传中…' : `选择${node.content_type === 'image' ? '图片' : '文件'}`}
        </button>
      )}
    </div>
  );
}
