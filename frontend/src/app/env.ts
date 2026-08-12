import { z } from 'zod';
import { createElement } from 'react';

const DEVELOPMENT_LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);

function isLoopbackHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && DEVELOPMENT_LOOPBACK_HOSTS.has(url.hostname);
  } catch {
    return false;
  }
}

const publicUrlSchema = z
  .string()
  .trim()
  .min(1)
  .refine(
    (value) => (
      value.startsWith('/')
      || /^https:\/\//u.test(value)
      || (import.meta.env.DEV && isLoopbackHttpUrl(value))
    ),
    {
      message: 'must be an absolute HTTPS URL, an application-root relative path, or a development loopback HTTP URL',
    },
  );

const environmentSchema = z
  .object({
    VITE_API_BASE_URL: publicUrlSchema,
    VITE_SSE_BASE_URL: publicUrlSchema,
    VITE_MOCK_MODE: z.enum(['off', 'browser', 'test']),
    VITE_BUILD_VERSION: z.string().trim().min(1),
    VITE_RELEASE_ENV: z.enum(['local', 'dev', 'test', 'staging', 'production']),
  })
  .strict();

export interface AppEnvironment {
  apiBaseUrl: string;
  sseBaseUrl: string;
  mockMode: 'off' | 'browser' | 'test';
  buildVersion: string;
  releaseEnv: 'local' | 'dev' | 'test' | 'staging' | 'production';
}

export type EnvironmentResult =
  | { ok: true; value: AppEnvironment }
  | { ok: false; issues: readonly string[] };

export function readAppEnvironment(): EnvironmentResult {
  return readEnvironment({
    VITE_API_BASE_URL: import.meta.env.VITE_API_BASE_URL as unknown,
    VITE_SSE_BASE_URL: import.meta.env.VITE_SSE_BASE_URL as unknown,
    VITE_MOCK_MODE: import.meta.env.VITE_MOCK_MODE as unknown,
    VITE_BUILD_VERSION: import.meta.env.VITE_BUILD_VERSION as unknown,
    VITE_RELEASE_ENV: import.meta.env.VITE_RELEASE_ENV as unknown,
  });
}

export function readEnvironment(source: unknown): EnvironmentResult {
  const values = typeof source === 'object' && source !== null
    ? source as Record<string, unknown>
    : {};
  const result = environmentSchema.safeParse({
    VITE_API_BASE_URL: values.VITE_API_BASE_URL,
    VITE_SSE_BASE_URL: values.VITE_SSE_BASE_URL,
    VITE_MOCK_MODE: values.VITE_MOCK_MODE,
    VITE_BUILD_VERSION: values.VITE_BUILD_VERSION,
    VITE_RELEASE_ENV: values.VITE_RELEASE_ENV,
  });
  if (!result.success) {
    return {
      ok: false,
      issues: result.error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`),
    };
  }
  return {
    ok: true,
    value: {
      apiBaseUrl: result.data.VITE_API_BASE_URL,
      sseBaseUrl: result.data.VITE_SSE_BASE_URL,
      mockMode: result.data.VITE_MOCK_MODE,
      buildVersion: result.data.VITE_BUILD_VERSION,
      releaseEnv: result.data.VITE_RELEASE_ENV,
    },
  };
}

export function StartupErrorPage({ issues }: { issues: readonly string[] }) {
  return createElement(
    'main',
    { className: 'startup-error', role: 'alert', 'aria-labelledby': 'startup-error-title' },
    createElement('h1', { id: 'startup-error-title' }, '应用无法安全启动'),
    createElement('p', null, '公开环境配置缺失或无效。应用没有发起任何接口请求。'),
    createElement(
      'ul',
      null,
      issues.map((issue) => createElement('li', { key: issue }, issue)),
    ),
  );
}
