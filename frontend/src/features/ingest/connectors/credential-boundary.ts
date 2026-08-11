const FORBIDDEN_SECRET_KEYS = new Set([
  'access_key',
  'access_key_id',
  'access_key_secret',
  'secret_access_key',
  'access_token',
  'secret',
  'secret_key',
  'password',
  'private_key',
  'security_token',
  'session_token',
  'token',
  'credential_input',
  'signed_url',
  'signed_query',
  'signature',
  'authorization',
  'credentials',
  'sts',
  'endpoint',
  'bucket',
  'object_key',
  'object_prefix',
  'multipart_upload_id',
]);

export interface WriteOnlyCredentialInput {
  readonly kind: string;
  readonly value: string;
}

export function assertNoCredentialLeak(value: unknown, path = '$'): void {
  if (!value || typeof value !== 'object') return;
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    if (FORBIDDEN_SECRET_KEYS.has(key.toLowerCase())) {
      throw new Error(`CONTRACT_MISMATCH: forbidden credential field at ${path}.${key}`);
    }
    assertNoCredentialLeak(child, `${path}.${key}`);
  }
}

/** Secrets are exposed only for the synchronous construction of one mutation body. */
export function consumeCredential<T>(
  input: WriteOnlyCredentialInput,
  consumer: (credential: WriteOnlyCredentialInput) => T,
): T {
  try {
    return consumer(input);
  } finally {
    // JavaScript strings cannot be zeroed; callers must clear the uncontrolled input immediately.
  }
}

export function credentialDisplay(maskedHint: string | null, configured: boolean): string {
  if (!configured) return '未配置';
  return maskedHint ? `已配置（${maskedHint}）` : '已配置';
}
