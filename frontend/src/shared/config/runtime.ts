export interface RuntimeConfig {
  apiBaseUrl: string;
  sseBaseUrl: string;
  buildVersion: string;
  releaseEnv: 'local' | 'dev' | 'test' | 'staging' | 'production';
}

let runtimeConfig: RuntimeConfig | null = null;

export function configureRuntime(config: RuntimeConfig): void {
  runtimeConfig = Object.freeze({ ...config });
}

export function getRuntimeConfig(): RuntimeConfig {
  if (runtimeConfig === null) throw new Error('Runtime configuration is not initialized');
  return runtimeConfig;
}

export function resetRuntimeConfigForTests(): void {
  runtimeConfig = null;
}
