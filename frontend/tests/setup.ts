import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { cleanup } from '@testing-library/react';
import { server } from '../src/mocks/server';
import { resetScenario } from '../src/mocks/scenarios/registry';

// jsdom does not implement URL.revokeObjectURL; stub it so viewer resource tests pass
if (typeof URL.revokeObjectURL === 'undefined') {
  Object.defineProperty(URL, 'revokeObjectURL', { value: () => undefined, configurable: true });
}
if (typeof URL.createObjectURL === 'undefined') {
  Object.defineProperty(URL, 'createObjectURL', { value: () => 'blob:fixture', configurable: true });
}

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  resetScenario();
});
afterAll(() => server.close());
