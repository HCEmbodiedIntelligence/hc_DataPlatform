import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';

export const worker = setupWorker(...handlers);

export async function startBrowserWorker(): Promise<void> {
  await worker.start({ onUnhandledRequest: 'bypass' });
}
