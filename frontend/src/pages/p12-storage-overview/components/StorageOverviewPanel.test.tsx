import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { adaptStorageOverview } from '../../../features/storage-overview/api/adapter';
import { storageOverviewFixture } from '../../../mocks/fixtures/storage-overview';
import { StorageOverviewPanel } from './StorageOverviewPanel';

describe('StorageOverviewPanel', () => {
  it('uses clear Chinese copy for storage reconciliation', () => {
    const overview = adaptStorageOverview(storageOverviewFixture);
    const html = renderToStaticMarkup(<StorageOverviewPanel overview={overview} />);

    expect(html).toContain('存储容量核对');
    expect(html).toContain('核对一致');
    expect(html).toContain('平台登记容量');
    expect(html).toContain('对象存储实际盘点容量');
    expect(html).toContain('未完成分片上传容量');
    expect(html).not.toContain('Inventory 对账');
    expect(html).not.toContain('>MATCHED<');
  });
});
