import { loadEnv } from 'vite';

const loaded = loadEnv('production', process.cwd(), 'VITE_');
const value = (name) => process.env[name] ?? loaded[name] ?? '';
const failures = [];

if (value('VITE_MOCK_MODE') !== 'off') failures.push('VITE_MOCK_MODE must be off');
if (value('VITE_RELEASE_ENV') !== 'production') {
  failures.push('VITE_RELEASE_ENV must be production');
}
if (!value('VITE_BUILD_VERSION') || value('VITE_BUILD_VERSION') === 'web-local') {
  failures.push('VITE_BUILD_VERSION must identify an immutable release');
}
for (const name of ['VITE_API_BASE_URL', 'VITE_SSE_BASE_URL']) {
  if (!/^(\/|https:\/\/)/u.test(value(name))) {
    failures.push(`${name} must be an application-root path or an HTTPS URL`);
  }
}

if (failures.length > 0) {
  process.stderr.write(`Unsafe frontend container build:\n- ${failures.join('\n- ')}\n`);
  process.exit(1);
}

