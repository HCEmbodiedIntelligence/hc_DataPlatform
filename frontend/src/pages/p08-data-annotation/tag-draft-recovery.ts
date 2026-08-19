import type { RuntimeAnnotationTag } from "./runtime-annotation-adapter";

const PREFIX = "hc:p08:tag-recovery:v2:";
const TTL_MS = 2 * 60 * 60 * 1000;

export interface TagDraftRecoveryIdentity {
  readonly principalId: string;
  readonly projectId: string;
  readonly taskId: string;
  readonly taskEtag: string;
  readonly revision: number;
}

interface StoredRecovery {
  readonly identity: TagDraftRecoveryIdentity;
  readonly expiresAt: number;
  readonly tags: readonly RuntimeAnnotationTag[];
}

function recoveryStorage(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

function key(identity: TagDraftRecoveryIdentity): string {
  return `${PREFIX}${encodeURIComponent(identity.principalId)}:${encodeURIComponent(identity.projectId)}:${encodeURIComponent(identity.taskId)}`;
}

function isScalar(value: unknown): value is string | number | boolean {
  return (
    typeof value === "string" ||
    (typeof value === "number" && Number.isFinite(value)) ||
    typeof value === "boolean"
  );
}

function isTag(value: unknown): value is RuntimeAnnotationTag {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const tag = value as Readonly<Record<string, unknown>>;
  const attributes = tag.attributes;
  const relations = tag.relations;
  const subject = tag.subject;
  const validObject = (candidate: unknown): boolean => {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate))
      return false;
    const object = candidate as Readonly<Record<string, unknown>>;
    return (
      typeof object.object_id === "string" &&
      typeof object.object_type === "string"
    );
  };
  return (
    typeof tag.annotation_id === "string" &&
    typeof tag.tag_id === "string" &&
    Array.isArray(tag.path) &&
    tag.path.every((entry) => typeof entry === "string") &&
    Number.isInteger(tag.start_step) &&
    Number.isInteger(tag.end_step) &&
    (attributes === undefined ||
      (typeof attributes === "object" &&
        attributes !== null &&
        !Array.isArray(attributes) &&
        Object.entries(attributes).every(
          ([name, entry]) =>
            !["__proto__", "constructor", "prototype"].includes(name) &&
            isScalar(entry),
        ))) &&
    Array.isArray(relations) &&
    relations.every((relation) => {
      if (!relation || typeof relation !== "object" || Array.isArray(relation))
        return false;
      const record = relation as Readonly<Record<string, unknown>>;
      return (
        typeof record.relation_type === "string" && validObject(record.target)
      );
    }) &&
    (subject === null || subject === undefined || validObject(subject))
  );
}

function isRecovery(value: unknown): value is StoredRecovery {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Readonly<Record<string, unknown>>;
  const identity = record.identity;
  if (!identity || typeof identity !== "object" || Array.isArray(identity))
    return false;
  const id = identity as Readonly<Record<string, unknown>>;
  return (
    typeof id.principalId === "string" &&
    typeof id.projectId === "string" &&
    typeof id.taskId === "string" &&
    typeof id.taskEtag === "string" &&
    Number.isInteger(id.revision) &&
    typeof record.expiresAt === "number" &&
    Number.isFinite(record.expiresAt) &&
    Array.isArray(record.tags) &&
    record.tags.every(isTag)
  );
}

export function saveTagDraftRecovery(
  identity: TagDraftRecoveryIdentity,
  tags: readonly RuntimeAnnotationTag[],
  now = Date.now(),
): void {
  const storage = recoveryStorage();
  if (!storage) return;
  const payload: StoredRecovery = { identity, expiresAt: now + TTL_MS, tags };
  try {
    storage.setItem(key(identity), JSON.stringify(payload));
  } catch {
    // Session recovery is best effort and never changes the server draft.
  }
}

export function loadTagDraftRecovery(
  identity: TagDraftRecoveryIdentity,
  now = Date.now(),
): readonly RuntimeAnnotationTag[] | null {
  const storage = recoveryStorage();
  const raw = storage?.getItem(key(identity));
  if (!storage || !raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (
      !isRecovery(parsed) ||
      parsed.expiresAt <= now ||
      JSON.stringify(parsed.identity) !== JSON.stringify(identity)
    ) {
      storage.removeItem(key(identity));
      return null;
    }
    return parsed.tags;
  } catch {
    storage.removeItem(key(identity));
    return null;
  }
}

export function clearTagDraftRecovery(
  identity: TagDraftRecoveryIdentity,
): void {
  try {
    recoveryStorage()?.removeItem(key(identity));
  } catch {
    // Recovery cleanup is best effort.
  }
}
