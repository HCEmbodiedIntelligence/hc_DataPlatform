import { afterEach, describe, expect, it, vi } from 'vitest';
import { readEnvironment } from '../../src/app/env';

describe('public startup environment', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

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

  it('keeps HTTPS and application-root relative endpoints valid', () => {
    const result = readEnvironment({
      VITE_API_BASE_URL: 'https://api.example.com/v1',
      VITE_SSE_BASE_URL: '/api/v1/events',
      VITE_MOCK_MODE: 'off',
      VITE_BUILD_VERSION: 'web-fixture',
      VITE_RELEASE_ENV: 'test',
    });

    expect(result.ok).toBe(true);
  });

  it.each([
    'http://localhost:8000/api/v1',
    'http://127.0.0.1:8000/api/v1',
    'http://[::1]:8000/api/v1',
  ])('allows the exact loopback HTTP endpoint in development: %s', (endpoint) => {
    const result = readEnvironment({
      VITE_API_BASE_URL: endpoint,
      VITE_SSE_BASE_URL: endpoint,
      VITE_MOCK_MODE: 'off',
      VITE_BUILD_VERSION: 'web-fixture',
      VITE_RELEASE_ENV: 'local',
    });

    expect(result.ok).toBe(true);
  });

  it.each([
    'http://192.168.1.10:8000/api/v1',
    'http://api.example.com/v1',
    'http://evil.localhost.attacker.com/v1',
  ])('rejects non-loopback HTTP endpoints in development: %s', (endpoint) => {
    const result = readEnvironment({
      VITE_API_BASE_URL: endpoint,
      VITE_SSE_BASE_URL: '/api/v1/events',
      VITE_MOCK_MODE: 'off',
      VITE_BUILD_VERSION: 'web-fixture',
      VITE_RELEASE_ENV: 'local',
    });

    expect(result.ok).toBe(false);
  });

  it('rejects loopback HTTP endpoints outside a development build', () => {
    vi.stubEnv('DEV', false);

    const result = readEnvironment({
      VITE_API_BASE_URL: 'http://localhost:8000/api/v1',
      VITE_SSE_BASE_URL: '/api/v1/events',
      VITE_MOCK_MODE: 'off',
      VITE_BUILD_VERSION: 'web-fixture',
      VITE_RELEASE_ENV: 'production',
    });

    expect(result.ok).toBe(false);
  });
});
