import { describe, expect, it } from 'vitest';
import { calibrationSetsPageWireSchema } from '../../src/features/calibrations/api';
import { dataSchemasPageWireSchema } from '../../src/features/data-schemas/api';
import { robotModelsPageWireSchema } from '../../src/features/robot-models/api';
import { robotsPageWireSchema } from '../../src/features/robots/api';
import {
  calibrationSetsPageFixture,
  dataSchemasPageFixture,
  robotModelsPageFixture,
  robotsPageFixture,
} from '../../src/mocks/fixtures/management';
import { robotModelsQueryCodec } from '../../src/pages/p14-robot-models/query-codec';
import { robotsQueryCodec } from '../../src/pages/p15-robots/query-codec';
import { pageCalibrationsQueryCodec } from '../../src/pages/p16-calibrations/query-codec';
import { pageDataSchemasQueryCodec } from '../../src/pages/p17-data-schemas/query-codec';

describe('P14-P17 robotics contracts', () => {
  it('accepts all four management fixtures', () => {
    expect(robotModelsPageWireSchema.parse(robotModelsPageFixture).items).toHaveLength(1);
    expect(robotsPageWireSchema.parse(robotsPageFixture).items).toHaveLength(1);
    expect(calibrationSetsPageWireSchema.parse(calibrationSetsPageFixture).items).toHaveLength(1);
    expect(dataSchemasPageWireSchema.parse(dataSchemasPageFixture).items).toHaveLength(1);
  });

  it('round-trips each page codec without latest/current aliases', () => {
    const p14 = { q: 'arm', modelId: 'robot_model_fx_01', versionId: 'robot_model_version_fx_01' };
    const p15 = { q: 'assembly', robotId: 'robot_fx_01', componentId: 'component_fx_camera_01' };
    const p16 = {
      robotId: 'robot_fx_01', componentId: 'component_fx_camera_01',
      setId: 'calibration_set_fx_01', section: 'overview' as const,
    };
    const p17 = {
      tab: 'registry' as const, schemaId: 'schema_fx_joint_state', schemaVersion: '3',
      detailTab: 'fields' as const,
    };
    const p14Canonical = robotModelsQueryCodec.parse(robotModelsQueryCodec.build(p14));
    const p15Canonical = robotsQueryCodec.parse(robotsQueryCodec.build(p15));
    const p16Canonical = pageCalibrationsQueryCodec.parse(pageCalibrationsQueryCodec.build(p16));
    const p17Canonical = pageDataSchemasQueryCodec.parse(pageDataSchemasQueryCodec.build(p17));
    expect(robotModelsQueryCodec.parse(robotModelsQueryCodec.build(p14Canonical))).toEqual(p14Canonical);
    expect(robotsQueryCodec.parse(robotsQueryCodec.build(p15Canonical))).toEqual(p15Canonical);
    expect(pageCalibrationsQueryCodec.parse(pageCalibrationsQueryCodec.build(p16Canonical))).toEqual(p16Canonical);
    expect(pageDataSchemasQueryCodec.parse(pageDataSchemasQueryCodec.build(p17Canonical))).toEqual(p17Canonical);
  });
});
