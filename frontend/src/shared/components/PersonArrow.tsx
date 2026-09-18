// 人员流转「线条箭头 →」（发起人 → 处理人），列表卡片 / 详情页共用。
//
// 为何不用 lucide ArrowRight：它是 24x24 正方形 viewBox 的图标，
// 图形四周留有 padding、线身较短（头部占比大），视觉偏「粗短」，
// 且线宽受图标几何约束、难以调细拉长。
// 故自绘扁长 viewBox（28x12）的 SVG：横线占满主体、箭头头部小而尖，
// 配合更细的 stroke（1.2）与圆形端点，得到「细长优雅」的线条箭头。
//
// 颜色用 currentColor 驱动，由父级 .task-card2__person-arrow 的 color 决定
// （历史卡片 / 详情页的蓝色 token 覆盖自动生效）。
type Props = {
  /** 箭头整体宽度（px）。默认 28，比旧图标更长 */
  width?: number;
  /** 描边粗细。默认 1.2，比旧图标（2）更细 */
  strokeWidth?: number;
};

export default function PersonArrow({ width = 28, strokeWidth = 1.2 }: Props) {
  // viewBox 宽高比 28:12，高度按比例随宽度缩放，保持箭头比例不失真
  const height = Math.round((width * 12) / 28);
  return (
    <svg
      className="person-arrow-svg"
      width={width}
      height={height}
      viewBox="0 0 28 12"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
    >
      {/* 横线：贯穿主体，留出右侧给箭头头部 */}
      <path
        d="M1 6 H25.5"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
      />
      {/* 箭头头部：两段短斜线收拢成一个尖角，小巧锐利 */}
      <path
        d="M21 2 L25.8 6 L21 10"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
