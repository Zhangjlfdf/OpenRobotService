// AGV 车型目录（8 大系列 50 款）——「硬件 / 车辆」下车型节点（车型1/车型2…）的命名来源。
//
// 用途：车型节点的标题不再是随手输入，而是按系列分组的下拉框直接选（型号写入节点名）；
// 目录里没有的车型用行内编辑输入框手动输入兜底。
// 与后端 backend/app/modules/admin/services/info_node_import_service.py 的 VEHICLE_MODEL_SERIES
// 保持一致（文件导入的 AI 提示词用同一份清单规范车型写法），改动时两边同步。

export interface VehicleModel {
  /** 车型型号（选入节点名的就是这个） */
  code: string;
  /** 车型全称（含结构 / 载重说明，仅展示用） */
  name: string;
}

export interface VehicleModelSeries {
  /** 系列名（下拉框分组标题） */
  series: string;
  models: VehicleModel[];
}

export const VEHICLE_MODEL_SERIES: VehicleModelSeries[] = [
  {
    series: '潜伏小车系列',
    models: [
      { code: 'XC1051', name: '背负式搬运机器人 500 kg' },
      { code: 'XC1061', name: '跟随潜伏式机器人 600 kg' },
      { code: 'XCD031', name: '潜伏顶升搬运机器人 300 kg' },
      { code: 'XCD061', name: '潜伏顶升搬运机器人 600 kg' },
      { code: 'XCD101', name: '潜伏顶升搬运机器人 1000 kg' },
      { code: 'XCD151', name: '潜伏顶升搬运机器人 1500 kg' },
      { code: 'XCD301', name: '全向潜伏顶升式机器人 3000 kg' },
      { code: 'XCD501', name: '重载潜伏顶升式机器人 5000 kg' },
    ],
  },
  {
    series: '自动搬运车系列',
    models: [
      { code: 'EXP15', name: '极简自动搬运车 1500 kg' },
      { code: 'RPG201', name: '踏板式自动搬运车 2000 kg' },
      { code: 'XPC151', name: '极简智能搬运车 1500 kg' },
      { code: 'XPG151', name: '步行式自动搬运车 1500 kg' },
      { code: 'XSG121', name: '堆高式自动搬运车 1200 kg' },
    ],
  },
  {
    series: '智能搬运车系列',
    models: [
      { code: 'XCF101', name: '潜伏式叉车机器人 1000 kg' },
      { code: 'XP1151', name: '点对点智能搬运机器人 1500 kg' },
      { code: 'XP1152', name: '点对点智能搬运机器人 1500 kg' },
      { code: 'XP1201', name: '薄背搬运式机器人 2000 kg' },
      { code: 'XP3201', name: '室内外多场景智能搬运机器人 2000 kg' },
      { code: 'XPL201', name: '高速重载智能搬运机器人 2000 kg' },
      { code: 'XPL201P', name: '物流专用高速搬运机器人 2000 kg' },
      { code: 'XPL201T', name: '薄背物流专用搬运机器人 2000 kg' },
      { code: 'XPL301', name: '高速重载智能搬运机器人 3000 kg' },
      { code: 'XPL501', name: '高速重载智能搬运机器人 5000 kg' },
    ],
  },
  {
    series: '智能堆高系列',
    models: [
      { code: 'XFL201', name: '平衡重式机器人 2000 kg' },
      { code: 'XNA101', name: '双侧叉平衡重式机器人 1000 kg' },
      { code: 'XNA121', name: '双侧叉平衡重式机器人 1200 kg' },
      { code: 'XNA151', name: '单侧叉平衡重式机器人 1500 kg' },
      { code: 'XQE151', name: '平衡重式机器人 1500 kg' },
      { code: 'XS1151', name: '薄背堆高机器人 1500 kg' },
      { code: 'XS1152', name: '薄背堆高机器人 1500 kg' },
      { code: 'XS1161', name: '超薄托盘堆垛机器人 1600 kg' },
      { code: 'XS2201', name: '重载堆高机器人 2000 kg' },
      { code: 'XSC081', name: '平衡重式堆高机器人 800 kg' },
      { code: 'XSC121', name: '平衡重式堆高机器人 1200 kg' },
      { code: 'XSC151', name: '平衡重式堆高机器人 1500 kg' },
      { code: 'XSC201', name: '平衡重式堆高机器人 2000 kg' },
      { code: 'XSF101', name: '单侧叉堆高式机器人 1000 kg' },
    ],
  },
  {
    series: '智能前移系列',
    models: [
      { code: 'XQC161', name: '室内前移式机器人 1600 kg' },
      { code: 'XQC201', name: '室内前移式机器人 2000 kg' },
      { code: 'XQE122', name: '室内前移式机器人 1200 kg' },
      { code: 'XQS151', name: '室外前移式机器人 1500 kg' },
      { code: 'XQS181', name: '室外前移式机器人 1800 kg' },
    ],
  },
  {
    series: '智能牵引系列',
    models: [
      { code: 'XCART', name: '智能观光车 500 kg' },
      { code: 'XCT201', name: '室内牵引式机器人 2000 kg' },
      { code: 'XTD401', name: '室外牵引式机器人 4000 kg' },
      { code: 'XTD601', name: '室外牵引式机器人 6000 kg' },
    ],
  },
  {
    series: '智能拣料系列',
    models: [
      { code: 'XCU0051', name: '料箱存取机器人 50/50+50×4 kg' },
    ],
  },
  {
    series: '具身机器人系列',
    models: [
      { code: 'XCB031', name: '单臂具身机器人 背负 300 kg / 抓取 2-5 kg' },
      { code: 'XCL0051', name: '料箱转运具身机器人 50×4 kg' },
      { code: 'XCO0051', name: '料箱拣选具身机器人 5/50+50 kg' },
    ],
  },
];

/** 行内改名输入框共用的 datalist（页面根部渲染一份，避免每行重复 50 个 option） */
export const VEHICLE_MODEL_DATALIST_ID = 'vehicle-model-options';

const MODEL_CODES = new Set(VEHICLE_MODEL_SERIES.flatMap((series) => series.models.map((model) => model.code)));

export const VEHICLE_MODEL_TOTAL = MODEL_CODES.size;

/** 标题是否已是目录里的车型型号（用于下拉框回显当前选中） */
export function isKnownVehicleModel(title: string): boolean {
  return MODEL_CODES.has((title ?? '').trim());
}

/**
 * 是否按车型节点处理：直接给「选车型」下拉框。
 * 命中任一即算——标题是模板占位的「车型1/车型2…」、标题已是目录中的型号，
 * 或挂在「车辆」节点下（这样手动输入的自定义车型也还能随时切回下拉选择）。
 * 注意不含「父级是车型节点」的情况：车型下的「数量」等子节点不是车型。
 */
export function isVehicleModelNode(title: string, parentTitle?: string): boolean {
  const trimmed = (title ?? '').trim();
  if (/^车型/.test(trimmed) || MODEL_CODES.has(trimmed)) return true;
  return (parentTitle ?? '').trim() === '车辆';
}
