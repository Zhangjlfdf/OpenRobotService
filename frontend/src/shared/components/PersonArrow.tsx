// 人员流转「自适应流线箭头」（发起人 ——→ 处理人），列表卡片 / 详情页共用。
//
// 结构：细线（<i>，flex:1 撑满流线区剩余宽度）+ 小箭头头部（SVG，round 端点），
// 两段零间距相接，视觉上是一根完整的箭头。线身长度随可用空间自适应伸缩，
// 参与人头像堆叠由调用方置于 .task-card2__flow 内、绝对居中骑在线上。
//
// 为何不整根 SVG：线长需自适应，而 preserveAspectRatio="none" 拉伸会把箭头头部
// 一起拉变形；固定 viewBox 的整根箭头无法只伸长线身。故拆「CSS 线身 + SVG 头部」。
//
// 颜色用 currentColor 驱动（本组件根节点 .task-card2__person-arrow 的 color），
// 历史卡片 / 详情页的蓝色 token 覆盖自动生效。
type Props = {
  /** 线身粗细（= 头部描边宽，px）。默认 1.2，比旧图标（2）更细 */
  strokeWidth?: number;
};

export default function PersonArrow({ strokeWidth = 1.2 }: Props) {
  return (
    <span className="task-card2__person-arrow" aria-hidden="true">
      {/* 线身：flex:1 自适应撑满，粗细与描边一致 */}
      <i className="person-arrow__line" style={{ height: strokeWidth }} />
      {/* 箭头头部：小而尖的 round 端点折线，左端圆帽与线身零间距相接 */}
      <svg
        className="person-arrow__head"
        width={6}
        height={12}
        viewBox="0 0 6 12"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        focusable="false"
      >
        <path
          d="M0.6 2 L5.4 6 L0.6 10"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}
