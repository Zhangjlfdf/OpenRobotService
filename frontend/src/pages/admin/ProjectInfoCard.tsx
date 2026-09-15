// 项目信息管理卡（项目详细信息）—— 对照原型 components/project/ProjectDetailCard.tsx：
// 「显示内容」标签池 + 勾选筛选 + Markdown 文档式浏览态，右上角「编辑」跳转独立编辑页。
//
// 数据源：后端 /api/admin/info-nodes/*（经 shared/utils/projectInfoTree.ts 数据层），异步加载；
// 标签筛选/折叠是个人界面偏好，仍存本机。
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import API_CONFIG from '@/config/api';
import {
  MacChevronDown, MacChevronUp, MacDownload, MacFileText, MacImage, MacPencil,
} from '@/shared/components/macaronIcons';
import {
  computeInfoCompleteness,
  formatFileSize,
  loadCardCollapsed,
  loadInfoNodes,
  loadSelectedTags,
  saveCardCollapsed,
  saveSelectedTags,
  type ProjectInfoFileValue,
  type ProjectInfoNode,
  type ProjectInfoSelectValue,
} from '@/shared/utils/projectInfoTree';

export default function ProjectInfoCard({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const navigate = useNavigate();
  const [nodes, setNodes] = useState<ProjectInfoNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(() => loadSelectedTags(projectId));
  const [collapsed, setCollapsed] = useState<boolean>(() => loadCardCollapsed(projectId));

  // 信息树来自后端接口；切换项目、从编辑页返回（组件重新挂载）与手动重试时重新拉取
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    loadInfoNodes(projectId)
      .then((data) => { if (!cancelled) setNodes(data); })
      .catch(() => { if (!cancelled) { setNodes([]); setLoadError(true); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, reloadToken]);

  useEffect(() => { setSelected(loadSelectedTags(projectId)); }, [projectId]);

  useEffect(() => { saveSelectedTags(projectId, selected); }, [projectId, selected]);

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
  const visibleRoots = selected.size === 0 ? roots : roots.filter((node) => selected.has(node.id));
  const completeness = useMemo(() => computeInfoCompleteness(nodes), [nodes]);

  const toggleTag = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const toggleCard = () => setCollapsed((value) => {
    saveCardCollapsed(projectId, !value);
    return !value;
  });

  return (
    <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
      <div className="mac-info__head">
        <h3 className="mac-info__title">
          项目信息管理
        </h3>
        <div className="mac-info__actions">
          {canEdit && (
            <button
              type="button"
              className="mac-btn mac-btn--ghost mac-info__edit"
              onClick={() => navigate(`/admin/project-detail/${projectId}/edit`)}
            >
              <MacPencil size={13} />编辑
            </button>
          )}
          <button
            type="button"
            className="mac-btn mac-btn--ghost mac-info__collapse"
            onClick={toggleCard}
            aria-label={collapsed ? '展开' : '折叠'}
          >
            {collapsed ? <MacChevronDown size={16} /> : <MacChevronUp size={16} />}
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="mac-info__poolhead">
          <span className="mac-info__poolhead-label">显示内容</span>
          <div className="mac-info__poolhead-ops">
            <button type="button" className="mac-info__poolbtn" onClick={() => setSelected(new Set(roots.map((node) => node.id)))}>全选</button>
            <button type="button" className="mac-info__poolbtn" onClick={() => setSelected(new Set())}>清空</button>
          </div>
        </div>
      )}

      {/* 标签池（一级节点）：选中即只看该标签及其全部子内容；不选显示全部（对照原型） */}
      {roots.length > 0 && (
        <div className="mac-tagpool">
          {roots.map((node) => (
            <button
              key={node.id}
              type="button"
              className={`mac-tagpool__chip${selected.has(node.id) ? ' is-active' : ''}`}
              onClick={() => toggleTag(node.id)}
            >
              {node.title}
              {completeness.get(node.id)?.incomplete ? <span className="mac-tagpool__warn" aria-label="信息不全">!</span> : null}
            </button>
          ))}
        </div>
      )}

      {!collapsed && (
        <>
          <p className="mac-info__hint">不选择标签时显示全部内容</p>
          {loading ? (
            <div className="mac-info__state">正在加载信息节点…</div>
          ) : loadError ? (
            <div className="mac-info__state">
              信息节点加载失败
              <div className="mac-info__state-sub">请检查网络后重试</div>
              <button type="button" className="mac-btn mac-btn--outline" style={{ marginTop: 12 }} onClick={() => setReloadToken((value) => value + 1)}>
                重新加载
              </button>
            </div>
          ) : visibleRoots.length === 0 ? (
            <div className="mac-info__state">
              暂无内容，点击右上角「编辑」添加信息节点
              <div className="mac-info__state-sub">信息节点对所有协作者共享</div>
            </div>
          ) : (
            <article className="mac-doc">
              {visibleRoots.map((root) => <DocSection key={root.id} node={root} depth={1} byParent={byParent} />)}
            </article>
          )}
        </>
      )}
    </section>
  );
}

/** Markdown 文档式节点：标题层级对应节点层级（渲染后的 md 观感——按层级字号/颜色区分，不显示 # 记号） */
function DocSection({ node, depth, byParent }: { node: ProjectInfoNode; depth: number; byParent: Map<string | null, ProjectInfoNode[]> }) {
  const children = byParent.get(node.id) ?? [];
  const level = Math.min(depth, 4);
  return (
    <section className={`mac-doc__section mac-doc__section--d${level}`}>
      <div className={`mac-doc__head mac-doc__head--d${level}`}>
        <h4 className="mac-doc__title">{node.title}</h4>
      </div>
      {children.length === 0 && <DocContent node={node} />}
      {children.map((child) => <DocSection key={child.id} node={child} depth={depth + 1} byParent={byParent} />)}
    </section>
  );
}

function DocContent({ node }: { node: ProjectInfoNode }) {
  if (node.content_type === 'select') {
    const data = (node.value ?? {}) as Partial<ProjectInfoSelectValue>;
    return (
      <p className="mac-doc__select">
        <span className="mac-doc__pill">{data.selected || '未选择'}</span>
      </p>
    );
  }
  if (node.content_type === 'file' || node.content_type === 'image') {
    return <DocAttachment node={node} />;
  }
  const text = typeof node.value === 'string' ? node.value.trim() : '';
  if (!text) return <p className="mac-doc__empty">（未填写）</p>;
  return (
    <div className="mac-doc__text">
      {text.split('\n').map((line, index) => (
        line.trim() ? <p key={index}>{line}</p> : null
      ))}
    </div>
  );
}

function DocAttachment({ node }: { node: ProjectInfoNode }) {
  const file = (node.value ?? {}) as Partial<ProjectInfoFileValue>;
  const [imageBroken, setImageBroken] = useState(false);
  if (!file.name || file.resource_id == null) return <p className="mac-doc__empty">（未上传）</p>;
  // 资源管理服务下载接口（与项目文档同一接口）；图片节点顺带渲染缩略图，加载失败则只留附件行
  const url = `${API_CONFIG.ADMIN.BASE_URL}/resource-manager/resources/${file.resource_id}/download`;
  return (
    <div className="mac-doc__attach">
      {node.content_type === 'image' && !imageBroken && (
        <a href={url} target="_blank" rel="noreferrer">
          <img className="mac-doc__thumb" src={url} alt={file.name} onError={() => setImageBroken(true)} />
        </a>
      )}
      <div className="mac-doc__file">
        {node.content_type === 'image' ? <MacImage size={15} /> : <MacFileText size={15} />}
        <span className="mac-doc__file-name">{file.name}</span>
        {typeof file.size === 'number' && <span>{formatFileSize(file.size)}</span>}
        <a className="mac-doc__dl" href={url} download={file.name} aria-label="下载"><MacDownload size={15} /></a>
      </div>
    </div>
  );
}
