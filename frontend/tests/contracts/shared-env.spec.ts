import { describe, expect, it } from 'vitest';
import { readEnvironment } from '../../src/app/env';

describe('public startup environment', () => {
  it('fails safely when a required endpoint is missing or empty', () => {
    const result = readEnvironment({
      VITE_API_BASE_URL: '',
      VITE_SSE_BASE_URL: '/api/v1/events',
      VITE_MOCK_MODE: 'off',
      VITE_BUILD_VERSION: 'web-fixture',
      VITE_RELEASE_ENV: 'test',
    });
    expect(result.ok).toBe(false);
  });

  it('only projects the five public variables', () => {
    const result = readEnvironment({
      VITE_API_BASE_URL: '/api/v1',
      VITE_SSE_BASE_URL: '/api/v1/events',
      VITE_MOCK_MODE: 'test',
      VITE_BUILD_VERSION: 'web-fixture',
      VITE_RELEASE_ENV: 'test',
      VITE_SECRET: 'must-not-be-projected',
    });
    expect(result).toEqual({
      ok: true,
      value: {
        apiBaseUrl: '/api/v1',
        sseBaseUrl: '/api/v1/events',
        mockMode: 'test',
        buildVersion: 'web-fixture',
        releaseEnv: 'test',
      },
    });
    expect(JSON.stringify(result)).not.toContain('must-not-be-projected');
  });
});
