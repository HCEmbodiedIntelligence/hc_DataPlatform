import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createElement } from 'react';
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { isDatasetId } from '../../src/entities/dataset';
import { isDatasetVersionId } from '../../src/entities/dataset-version';
import { isReviewFindingId } from '../../src/entities/review-finding';
import {
  adaptApproveReviewResult,
  adaptDatasetListEnvelope,
  adaptDatasetVersionCapacity,
  adaptDatasetVersionSchemaSummary,
  adaptOperationalInventoryPage,
  adaptRequiredStoragePage,
  adaptReturnReviewResult,
  adaptSourceProvenancePage,
  adaptVersionSchema,
} from '../../src/features/datasets/api/adapters';
import {
  approveReviewResultWireSchema,
  datasetListEnvelopeWireSchema,
  datasetVersionCapacityWireSchema,
  datasetVersionSchemaSummaryWireSchema,
  deletionPreflightWireSchema,
  operationalInventoryPageWireSchema,
  requiredStoragePageWireSchema,
  returnReviewCommandWireSchema,
  returnReviewResultWireSchema,
  sourceProvenancePageWireSchema,
  versionSchemaWireSchema,
} from '../../src/features/datasets/api/wire-schemas';
import {
  RegionState,
  type DatasetRegionState,
} from '../../src/features/datasets/components/RegionState';
import { buildSuccessorDraftPendingLink } from '../../src/features/datasets/pending-links';
import { routes } from '../../src/features/datasets/routing';
import {
  approveResultFixture,
  datasetListFixture,
  datasetVersionCapacityFixture,
  datasetVersionSchemaFixture,
  deletionPreflightFixture,
  operationalInventoryFixture,
  requiredStorageFixture,
  returnResultFixture,
  sourceProvenanceFixture,
  versionSchemaFixture,
} from '../../src/mocks/fixtures/datasets/core';
import datasetsQueryCodec from '../../src/pages/p05-datasets/query-codec';
import datasetDetailQueryCodec, {
  assetEpisodeViewerQueryCodec,
} from '../../src/pages/p06-dataset-detail/query-codec';
import { p06Routes } from '../../src/pages/p06-dataset-detail/routes';
import versionDetailQueryCodec from '../../src/pages/p07-version-detail/query-codec';
import { makeQueryKey } from '../../src/shared/api/query-keys';
import { useShellStore } from '../../src/shared/scope/shell-store';

const regionStates: readonly DatasetRegionState[] = [
  'ready',
  'first-loading',
  'refreshing',
  'empty',
  'filtered-empty',
  'partial-error',
  'fatal-error',
  'forbidden',
  'not-found',
  'gone',
  'conflict',
  'rate-limited',
  'offline',
  'reconnecting',
  'contract-mismatch',
  'unknown-enum',
  'feature-unavailable',
];

describe('P05/P06/P07 dataset contract', () => {
  it('parses fixture through the production wire schema and adapter', () => {
    const wire = datasetListEnvelopeWireSchema.parse(datasetListFixture);
    const vm = adaptDatasetListEnvelope(wire);
    expect(vm.items[0]?.datasetId).toBe('dataset_fx_01');
    expect(vm.pageInfo.hasNextPage).toBe(false);
  });

  it('keeps route builders on exact immutable identities', () => {
    expect(routes.datasets.build({ q: 'arm', limit: 20 })).toBe('/datasets?q=arm');
    expect(
      routes.datasetDetail.build({ datasetId: 'dataset_fx_01' as never, tab: 'episodes' }),
    ).toBe('/datasets/dataset_fx_01?tab=episodes');
    expect(
      routes.versionDetail.build({
        datasetId: 'dataset_fx_01' as never,
        versionId: 'version_fx_review_01' as never,
      }),
    ).toBe('/datasets/dataset_fx_01/versions/version_fx_review_01');
    expect(
      routes.versionDetail.build({
        datasetId: 'dataset_fx_01' as never,
        versionId: 'version_fx_review_01' as never,
        returnTo: '/datasets/dataset_fx_01?tab=versions',
      }),
    ).toContain('returnTo=');
    expect(
      routes.episodeViewer.build({
        datasetId: 'dataset_fx_01' as never,
        versionId: 'version_fx_review_01' as never,
        episodeId: 'episode_fx_01' as never,
      }),
    ).toBe('/datasets/dataset_fx_01/versions/version_fx_review_01/episodes/episode_fx_01/view');
    expect(isDatasetId('dataset_fx_01')).toBe(true);
    expect(isDatasetVersionId('version_latest')).toBe(false);
    expect(buildSuccessorDraftPendingLink('draft_fx_successor_01')).toBe(
      '/manual/drafts/draft_fx_successor_01',
    );
  });

  it('registers the Viewer as a hidden P06-owned route', () => {
    expect(p06Routes[1]).toMatchObject({
      path: '/datasets/:datasetId/versions/:versionId/episodes/:episodeId/view',
      navigationOwnerPageId: 'P06',
      navigationOwnerGroupId: 'datasets',
      hiddenFromNavigation: true,
      requiredCapabilities: ['episode.read'],
    });
  });

  it('drops unknown query keys and clears both cursors when a dimension changes', () => {
    const p05 = datasetsQueryCodec.parse('q=arm&limit=20&after=a&unknown=leak');
    expect(datasetsQueryCodec.canonicalize('q=arm&limit=20&after=a&unknown=leak')).toBe(
      'q=arm&after=a',
    );
    const changed = datasetsQueryCodec.withChanges(p05, { sort: 'nameAsc' });
    expect(changed.after).toBeUndefined();
    expect(changed.before).toBeUndefined();
    expect(datasetDetailQueryCodec.canonicalize('tab=overview&q=discarded&unknown=x')).toBe('');
    expect(versionDetailQueryCodec.canonicalize('tab=review&q=discarded&unknown=x')).toBe(
      'tab=review',
    );
    expect(
      assetEpisodeViewerQueryCodec.canonicalize(
        'selectionStartNs=2&selectionEndNs=1&signedUrl=https://bad.invalid',
      ),
    ).toBe('');
    expect(versionDetailQueryCodec.canonicalize('tab=episodes&revisionId=revision_fx_01')).toBe('');
    expect(
      versionDetailQueryCodec.canonicalize('tab=review&returnTo=https%3A%2F%2Fevil.invalid'),
    ).toBe('tab=review');
    expect(versionDetailQueryCodec.canonicalize('tab=review&returnTo=%2Fdatasets')).toBe(
      'tab=review',
    );
    expect(
      assetEpisodeViewerQueryCodec.parse(new URLSearchParams({ returnTo: '/datasets' })).returnTo,
    ).toBeUndefined();
    expect(
      assetEpisodeViewerQueryCodec.parse(
        new URLSearchParams({
          returnTo: '/datasets/dataset_fx_01?tab=episodes&versionId=version_fx_review_01',
        }),
      ).returnTo,
    ).toContain('tab=episodes');
  });

  it('roundtrips every page codec without reviving tab-incompatible state', () => {
    const p05 = datasetsQueryCodec.parse(
      'q=arm&channels=%2Fcamera%2Ffront&channels=%2Fjoint&channelMatch=any&sort=nameAsc&limit=50&after=cursor_a',
    );
    expect(datasetsQueryCodec.parse(datasetsQueryCodec.build(p05))).toEqual(p05);
    const p06 = datasetDetailQueryCodec.parse(
      'tab=episodes&versionId=version_fx_review_01&q=arm&episodeId=episode_fx_01&sort=started-desc&limit=50&after=cursor_b',
    );
    expect(datasetDetailQueryCodec.parse(datasetDetailQueryCodec.build(p06))).toEqual(p06);
    const viewer = assetEpisodeViewerQueryCodec.parse(
      't=1.25&streamId=stream_fx_cam_01&selectionStartNs=1&selectionEndNs=2&returnTo=%2Fdatasets%2Fdataset_fx_01',
    );
    expect(assetEpisodeViewerQueryCodec.parse(assetEpisodeViewerQueryCodec.build(viewer))).toEqual(
      viewer,
    );
    const p07 = versionDetailQueryCodec.parse(
      'tab=revisions&q=arm&included=true&hasFinding=true&revisionId=revision_fx_01&limit=100&before=cursor_c',
    );
    expect(versionDetailQueryCodec.parse(versionDetailQueryCodec.build(p07))).toEqual(p07);
    expect(
      datasetDetailQueryCodec.canonicalize('tab=schema&q=must-drop&episodeId=episode_fx_01'),
    ).toBe('tab=schema');
    expect(
      versionDetailQueryCodec.canonicalize('tab=schema&revisionId=revision_fx_01&q=must-drop'),
    ).toBe('tab=schema');
  });

  it('maps unknown enums to read-only UNKNOWN and ignores unknown resource actions', () => {
    const unknownFixture = {
      ...datasetListFixture,
      items: [
        {
          ...datasetListFixture.items[0],
          current_version: { ...datasetListFixture.items[0].current_version, kind: 'FUTURE_KIND' },
          allowed_actions: [
            ...datasetListFixture.items[0].allowed_actions,
            { action: 'DELETE_DATASET_NOW', allowed: true, blocked_reasons: [] },
            { action: 'UPDATE_DATASET', allowed: true, blocked_reasons: [] },
            { action: 'DELETE_DATASET_VERSION', allowed: true, blocked_reasons: [] },
          ],
        },
      ],
    };
    const page = adaptDatasetListEnvelope(datasetListEnvelopeWireSchema.parse(unknownFixture));
    expect(page.items[0]?.currentVersion?.kind).toBe('UNKNOWN');
    expect(
      page.items[0]?.allowedActions.some((item) => item.action === ('DELETE_DATASET_NOW' as never)),
    ).toBe(false);
    expect(
      page.items[0]?.allowedActions.some((item) => item.action === ('UPDATE_DATASET' as never)),
    ).toBe(false);
    expect(
      page.items[0]?.allowedActions.some(
        (item) => item.action === ('DELETE_DATASET_VERSION' as never),
      ),
    ).toBe(false);
  });

  it('adapts P06 and P07 conditional fact regions without inventing values', () => {
    const schemaSummary = adaptDatasetVersionSchemaSummary(
      datasetVersionSchemaSummaryWireSchema.parse(datasetVersionSchemaFixture).data,
    );
    const sources = adaptSourceProvenancePage(
      sourceProvenancePageWireSchema.parse(sourceProvenanceFixture),
    );
    const capacity = adaptDatasetVersionCapacity(
      datasetVersionCapacityWireSchema.parse(datasetVersionCapacityFixture).data,
    );
    const schema = adaptVersionSchema(versionSchemaWireSchema.parse(versionSchemaFixture).data);
    const required = adaptRequiredStoragePage(
      requiredStoragePageWireSchema.parse(requiredStorageFixture),
    );
    const inventory = adaptOperationalInventoryPage(
      operationalInventoryPageWireSchema.parse(operationalInventoryFixture),
    );
    expect(schemaSummary).toMatchObject({ channelCount: '4', snapshot: { version: 'schema-v7' } });
    expect(sources.items[0]).toMatchObject({
      uploadId: 'upload_fx_01',
      sourceDisplayName: 'Line A ingest',
    });
    expect(capacity).toMatchObject({ state: 'SETTLED', actualOssBytes: '2048' });
    expect(schema.channels).toHaveLength(2);
    expect(required.items[0]).toMatchObject({ role: 'REVISION', safeLocator: null });
    expect(inventory.items[0]).toMatchObject({
      status: 'SUCCEEDED',
      operationalRevision: 'operational-revision-11',
    });
  });

  it('isolates all dataset query keys by organization, project and region scope', () => {
    useShellStore
      .getState()
      .setScope({
        organizationId: 'org_fx_01',
        projectId: 'project_fx_01',
        regionCode: 'cn-shanghai',
      });
    const first = makeQueryKey('datasets', 'list', { q: 'arm', sort: 'activityDesc' });
    useShellStore
      .getState()
      .setScope({
        organizationId: 'org_fx_01',
        projectId: 'project_fx_02',
        regionCode: 'cn-shanghai',
      });
    const projectChanged = makeQueryKey('datasets', 'list', { q: 'arm', sort: 'activityDesc' });
    useShellStore
      .getState()
      .setScope({
        organizationId: 'org_fx_02',
        projectId: 'project_fx_01',
        regionCode: 'cn-beijing',
      });
    const organizationAndRegionChanged = makeQueryKey('datasets', 'list', {
      q: 'arm',
      sort: 'activityDesc',
    });
    expect(projectChanged).not.toEqual(first);
    expect(organizationAndRegionChanged).not.toEqual(first);
  });

  it('renders all dataset regional states, including retained ready content', () => {
    for (const state of regionStates) {
      const view = render(
        createElement(RegionState, { state }, createElement('span', null, 'ready content')),
      );
      if (state === 'ready') expect(view.getByText('ready content')).toBeVisible();
      else expect(view.container.querySelector('.dataset-region-state')).not.toBeNull();
      view.unmount();
    }
  });

  it('requires at least one immutable ReviewFinding and preserves atomic lineage', () => {
    expect(
      returnReviewCommandWireSchema.safeParse({
        expected_status: 'REVIEWING',
        review_token: 'x'.repeat(32),
        finding_catalog_version: 'v1',
        findings: [],
      }).success,
    ).toBe(false);
    const envelope = returnReviewResultWireSchema.parse(returnResultFixture);
    const result = adaptReturnReviewResult(envelope.data);
    expect(result.findings).toHaveLength(1);
    expect(result.successorDraftId).not.toBe(result.supersedesDraftId);
    expect(isReviewFindingId(result.findings[0]?.id)).toBe(true);
    expect(result.returnedFromVersionId).toBe(result.outputVersionId);
    expect(result.returnedFromReviewDecisionId).toBe(result.reviewDecision.id);
    expect(
      returnReviewResultWireSchema.safeParse({
        ...returnResultFixture,
        data: {
          ...returnResultFixture.data,
          findings: [returnResultFixture.data.findings[0], returnResultFixture.data.findings[0]],
          review_finding_ids: ['review_finding_fx_01', 'review_finding_fx_01'],
        },
      }).success,
    ).toBe(false);
  });

  it('accepts only a self-consistent APPROVED result that stays REVIEWING', () => {
    const result = adaptApproveReviewResult(
      approveReviewResultWireSchema.parse(approveResultFixture),
    );
    expect(result.reviewDecision.decision).toBe('APPROVED');
    expect(result.outputVersionId).toBe(result.reviewDecision.outputVersionId);
    expect(result.jobId).toBe('job_fx_manifest_materialization');
  });

  it('keeps deletion preflight permanently non-executable', () => {
    const result = deletionPreflightWireSchema.parse(deletionPreflightFixture);
    expect(result.executable).toBe(false);
    expect(result.checks).toHaveLength(7);
    expect(JSON.stringify(result)).not.toContain('delete_command');
  });

  it('keeps ReviewFinding isolated and the P06 shell read-only', () => {
    const reviewFindingSource = readFileSync(
      resolve(process.cwd(), 'src/entities/review-finding.ts'),
      'utf8',
    );
    const manualIssueSource = readFileSync(
      resolve(process.cwd(), 'src/entities/manual-issue.ts'),
      'utf8',
    );
    const viewerShellSource = readFileSync(
      resolve(process.cwd(), 'src/pages/p06-dataset-detail/ViewerShell.tsx'),
      'utf8',
    );
    expect(reviewFindingSource).not.toMatch(/from ["'][^"']*manual-issue/);
    expect(manualIssueSource).not.toMatch(/from ["'][^"']*review-finding/);
    expect(viewerShellSource).toContain('EpisodeWorkbenchCore');
    expect(viewerShellSource).toContain('mode="readonly"');
    expect(viewerShellSource).not.toMatch(/createCleaningDraft|cleaningWorkbench/);
  });
});
