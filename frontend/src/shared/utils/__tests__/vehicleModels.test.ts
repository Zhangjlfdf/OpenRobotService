import { describe, it, expect } from 'vitest';
import {
  isKnownVehicleModel,
  isVehicleModelNode,
  VEHICLE_MODEL_DATALIST_ID,
  VEHICLE_MODEL_SERIES,
  VEHICLE_MODEL_TOTAL,
} from '../vehicleModels';

describe('vehicleModels（AGV 车型目录）', () => {
  it('8 大系列 50 款，型号不重复', () => {
    expect(VEHICLE_MODEL_SERIES.map((s) => s.series)).toEqual([
      '潜伏小车系列', '自动搬运车系列', '智能搬运车系列', '智能堆高系列',
      '智能前移系列', '智能牵引系列', '智能拣料系列', '具身机器人系列',
    ]);
    const codes = VEHICLE_MODEL_SERIES.flatMap((series) => series.models.map((model) => model.code));
    expect(codes).toHaveLength(50);
    expect(new Set(codes).size).toBe(50);
    expect(VEHICLE_MODEL_TOTAL).toBe(50);
    // 抽查各系列（含单款系列与具身系列）
    expect(codes).toContain('XCU0051');
    expect(codes).toContain('XCB031');
    expect(codes).toContain('XCART');
    // 每款都带展示名
    expect(VEHICLE_MODEL_SERIES.every((s) => s.models.every((m) => m.code && m.name))).toBe(true);
  });

  it('节点识别：模板占位（车型N）、已选型号、挂在「车辆」下的都按车型节点处理', () => {
    expect(isVehicleModelNode('车型1')).toBe(true);
    expect(isVehicleModelNode('车型')).toBe(true);
    expect(isVehicleModelNode('车型2', '车辆')).toBe(true);
    expect(isVehicleModelNode('XC1051')).toBe(true);            // 已选目录型号
    expect(isVehicleModelNode('XYZ-100', '车辆')).toBe(true);    // 自定义车型：仍可随时切回下拉
    expect(isVehicleModelNode('数量', '车型1')).toBe(false);     // 车型下的「数量」不是车型
    expect(isVehicleModelNode('客户信息', '基础信息')).toBe(false);
    expect(isVehicleModelNode('')).toBe(false);
  });

  it('isKnownVehicleModel 只认目录里的型号（去空白）', () => {
    expect(isKnownVehicleModel('XC1051')).toBe(true);
    expect(isKnownVehicleModel(' XCD301 ')).toBe(true);
    expect(isKnownVehicleModel('车型1')).toBe(false);
    expect(isKnownVehicleModel('')).toBe(false);
  });

  it('datalist 备选 id 稳定（ProjectInfoEdit 行内输入框引用它）', () => {
    expect(VEHICLE_MODEL_DATALIST_ID).toBe('vehicle-model-options');
  });
});
