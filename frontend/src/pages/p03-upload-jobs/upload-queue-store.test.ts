// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  FormalUploadSession,
  ManifestPreflight,
  PartAuthorization,
  UploadPart,
} from "./formal-client";

const formal = vi.hoisted(() => ({
  cancel: vi.fn(),
  commit: vi.fn(),
  complete: vi.fn(),
  create: vi.fn(),
  getManifest: vi.fn(),
  getSession: vi.fn(),
  listParts: vi.fn(),
  listSessions: vi.fn(),
  pause: vi.fn(),
  renew: vi.fn(),
  resume: vi.fn(),
  retryParts: vi.fn(),
}));

vi.mock("./formal-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./formal-client")>();
  return {
    ...actual,
    cancelFormalUpload: formal.cancel,
    commitFormalManifest: formal.commit,
    completeFormalUpload: formal.complete,
    createFormalUploadSession: formal.create,
    getFormalUploadManifest: formal.getManifest,
    getFormalUploadSession: formal.getSession,
    listFormalUploadParts: formal.listParts,
    listFormalUploadSessions: formal.listSessions,
    pauseFormalUpload: formal.pause,
    renewFormalUploadParts: formal.renew,
    resumeFormalUpload: formal.resume,
    retryFormalUploadParts: formal.retryParts,
  };
});

import {
  PART_TRANSFER_TIMEOUT_MS,
  resetUploadQueueStoreForTests,
  useUploadQueueStore,
} from "./upload-queue-store";
import { MIN_MULTIPART_BYTES } from "./upload-contract";

const scope = {
  organizationId: "org-e05",
  projectId: "project-e05",
  regionCode: "cn-east-01",
} as const;
const totalBytes = 8;
const manifest = {
  schema_version: 1,
  project_id: scope.projectId,
  task_id: "task-e05",
  collection_job_id: "job-e05",
  rollout_id: "rollout-e05",
  collection_session_id: "collection-session-e05",
  recording_request_id: "recording-request-e05",
  data_package_id: "package-e05",
  sequence_no: 1,
  robot_id: "robot-e05",
  start_time: "2026-08-18T04:00:00Z",
  end_time: "2026-08-18T04:00:08Z",
  cameras: [],
  topics: [],
  expected_topics: [],
  actual_topics: [],
  files: [
    {
      path: "recording.mcap",
      size: totalBytes,
      sha256: "a".repeat(64),
      crc64: "7",
      media_type: "application/octet-stream",
      role: "RAW_MCAP" as const,
    },
  ],
  file_size: totalBytes,
  sha256: "a".repeat(64),
  crc64: "7",
  compression: "none" as const,
  recorder_version: "recorder/1.0",
};
const preflight: ManifestPreflight = {
  schema_version: "manifest-preflight/v1",
  manifest_fingerprint: "b".repeat(64),
  identifiers: {
    collection_session_id: manifest.collection_session_id,
    recording_request_id: manifest.recording_request_id,
    data_package_id: manifest.data_package_id,
    robot_id: manifest.robot_id,
    pico_instance_id: null,
  },
  time_range: { start_time: manifest.start_time, end_time: manifest.end_time },
  files: [...manifest.files],
  total_file_size: totalBytes,
  discovery: {
    source: "MANIFEST",
    read_only: true,
    cameras: [],
    topics: [],
    missing_expected_topics: [],
  },
  manifest,
};
const session: FormalUploadSession = {
  session_id: "upload-session-e05",
  project_id: scope.projectId,
  region_code: scope.regionCode,
  data_package_id: manifest.data_package_id,
  rollout_id: manifest.rollout_id,
  source_type: "BROWSER_MULTIPART",
  status: "UPLOADING",
  manifest_fingerprint: preflight.manifest_fingerprint,
  object_key: "raw/recording.mcap",
  multipart_upload_id: "multipart-e05",
  expected_size: totalBytes,
  expected_sha256: manifest.sha256,
  expected_crc64: manifest.crc64,
  failure_code: null,
  etag: null,
  completed_at: null,
};
const authorization: PartAuthorization = {
  part_number: 1,
  url: "https://object-store.invalid/signed/part-1",
  expires_at: "2026-08-18T05:10:00Z",
};
const uploadedPart: UploadPart = {
  session_id: session.session_id!,
  project_id: scope.projectId,
  region_code: scope.regionCode,
  part_number: 1,
  status: "UPLOADED",
  etag: "etag-part-1",
  size: totalBytes,
  crc64: "7",
  retry_count: 0,
  failure_code: null,
  authorization_expires_at: null,
};
const timedOutServerPart: UploadPart = {
  ...uploadedPart,
  status: "FAILED",
  etag: null,
  size: null,
  crc64: null,
  failure_code: "PART_TIMEOUT",
};

class ImmediateXhr {
  static statuses: number[] = [];
  readonly upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  status = 0;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  timeout = 0;

  open() {}

  send(blob: Blob) {
    this.status = ImmediateXhr.statuses.shift() ?? 200;
    this.upload.onprogress?.({
      lengthComputable: true,
      loaded: blob.size,
    } as ProgressEvent);
    queueMicrotask(() => this.onload?.());
  }

  abort() {
    queueMicrotask(() => this.onabort?.());
  }
}

class ControlledXhr {
  static instances: ControlledXhr[] = [];
  readonly upload: { onprogress: ((event: ProgressEvent) => void) | null } = {
    onprogress: null,
  };
  status = 0;
  timeout = 0;
  url = "";
  abortCalls = 0;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  ontimeout: (() => void) | null = null;

  constructor() {
    ControlledXhr.instances.push(this);
  }

  open(_method: string, url: string) {
    this.url = url;
  }

  send() {}

  abort() {
    this.abortCalls += 1;
    queueMicrotask(() => this.onabort?.());
  }

  resolve(status = 200) {
    this.status = status;
    this.onload?.();
  }

  timeOut() {
    this.ontimeout?.();
  }
}

function selectedFiles(): File[] {
  return [
    new File([JSON.stringify(manifest)], "rollout_manifest.json", {
      type: "application/json",
    }),
    new File([new Uint8Array(totalBytes)], "recording.mcap", {
      type: "application/octet-stream",
    }),
  ];
}

function startInput() {
  return {
    scope,
    preflight,
    sourceType: "BROWSER_MULTIPART" as const,
    files: selectedFiles(),
  };
}

function multipartInput(partCount: 2 | 3) {
  const size =
    partCount === 2 ? MIN_MULTIPART_BYTES + 1 : MIN_MULTIPART_BYTES * 2 + 1;
  const multipartManifest = {
    ...manifest,
    data_package_id: `package-e05-${partCount}-parts`,
    file_size: size,
    files: [{ ...manifest.files[0]!, size }],
  };
  const multipartPreflight: ManifestPreflight = {
    ...preflight,
    identifiers: {
      ...preflight.identifiers,
      data_package_id: multipartManifest.data_package_id,
    },
    files: [...multipartManifest.files],
    total_file_size: size,
    manifest: multipartManifest,
  };
  return {
    scope,
    preflight: multipartPreflight,
    sourceType: "BROWSER_MULTIPART" as const,
    files: [
      new File([JSON.stringify(multipartManifest)], "rollout_manifest.json", {
        type: "application/json",
      }),
      new File([new Uint8Array(size)], "recording.mcap", {
        type: "application/octet-stream",
      }),
    ],
  };
}

function partAuthorization(partNumber: number): PartAuthorization {
  return {
    ...authorization,
    part_number: partNumber,
    url: `https://object-store.invalid/signed/part-${partNumber}`,
  };
}

function uploadedMultipartPart(partNumber: number, size: number): UploadPart {
  return {
    ...uploadedPart,
    part_number: partNumber,
    etag: `etag-part-${partNumber}`,
    size,
  };
}

function abortablePending(signal?: AbortSignal): Promise<UploadPart[]> {
  return new Promise((_, reject) => {
    signal?.addEventListener(
      "abort",
      () => reject(new DOMException("aborted", "AbortError")),
      { once: true },
    );
  });
}

beforeEach(() => {
  resetUploadQueueStoreForTests();
  vi.clearAllMocks();
  Object.defineProperty(globalThis.navigator, "onLine", {
    configurable: true,
    value: true,
  });
  Object.defineProperty(globalThis, "XMLHttpRequest", {
    configurable: true,
    value: ImmediateXhr,
  });
  ImmediateXhr.statuses = [];
  ControlledXhr.instances = [];
  formal.create.mockResolvedValue({
    idempotency_outcome: "CREATED",
    session,
    parts: [authorization],
  });
  formal.getSession.mockResolvedValue(session);
  formal.complete.mockResolvedValue({
    ...session,
    status: "MULTIPART_COMPLETED",
  });
  formal.commit.mockResolvedValue({
    data_package_id: manifest.data_package_id,
    rollout_id: manifest.rollout_id,
    object_key: session.object_key,
    status: "RAW_COMMITTED",
  });
  formal.cancel.mockResolvedValue({ ...session, status: "CANCELLED" });
  formal.pause.mockResolvedValue({ ...session, status: "PAUSED" });
  formal.renew.mockResolvedValue([authorization]);
  formal.retryParts.mockResolvedValue([authorization]);
});

afterEach(() => resetUploadQueueStoreForTests());

describe("P03 resumable upload queue", () => {
  it("stops an in-flight transfer offline and reconciles confirmed parts when the network returns", async () => {
    formal.listParts
      .mockImplementationOnce((_scope, _sessionId, signal) =>
        abortablePending(signal),
      )
      .mockResolvedValueOnce([uploadedPart]);

    const startPromise = useUploadQueueStore.getState().start(startInput());
    await vi.waitFor(() =>
      expect(useUploadQueueStore.getState().items[0]?.transferStatus).toBe(
        "uploading",
      ),
    );
    useUploadQueueStore.getState().handleOffline();
    await vi.waitFor(() =>
      expect(useUploadQueueStore.getState().items[0]?.transferStatus).toBe(
        "offline",
      ),
    );
    await startPromise;

    expect(useUploadQueueStore.getState().items[0]?.failureCode).toBe(
      "NETWORK_OFFLINE",
    );

    useUploadQueueStore.getState().handleOnline();
    await vi.waitFor(() =>
      expect(useUploadQueueStore.getState().items[0]?.transferStatus).toBe(
        "committed",
      ),
    );
    expect(formal.complete).toHaveBeenCalledWith(scope, session.session_id, {
      parts: [{ part_number: 1, etag: uploadedPart.etag }],
    });
    expect(formal.commit).toHaveBeenCalled();
  });

  it("cancels the server multipart session while bytes are being transferred", async () => {
    formal.listParts.mockImplementationOnce((_scope, _sessionId, signal) =>
      abortablePending(signal),
    );
    const startPromise = useUploadQueueStore.getState().start(startInput());
    await vi.waitFor(() =>
      expect(useUploadQueueStore.getState().items[0]?.transferStatus).toBe(
        "uploading",
      ),
    );
    const itemId = useUploadQueueStore.getState().items[0]!.id;

    await useUploadQueueStore.getState().cancel(itemId);
    await startPromise;

    expect(formal.cancel).toHaveBeenCalledWith(scope, session.session_id);
    expect(useUploadQueueStore.getState().items[0]?.transferStatus).toBe(
      "cancelled",
    );
    expect(useUploadQueueStore.getState().items[0]?.failureCode).not.toBe(
      "PART_TIMEOUT",
    );
  });

  it("re-authorizes only the failed part and commits after an explicit retry", async () => {
    ImmediateXhr.statuses = [500, 200];
    formal.listParts
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart]);

    await useUploadQueueStore.getState().start(startInput());
    const failed = useUploadQueueStore.getState().items[0]!;
    expect(failed.transferStatus).toBe("failed");
    expect(failed.failedParts).toEqual([1]);

    await useUploadQueueStore.getState().retryFailedParts(failed.id);

    expect(formal.retryParts).toHaveBeenCalledWith(scope, session.session_id, {
      failures: [{ part_number: 1, failure_code: "HTTP_500" }],
    });
    expect(useUploadQueueStore.getState().items[0]?.transferStatus).toBe(
      "committed",
    );
  });

  it("sets a 120-second part timeout, settles once, and succeeds only after an explicit retry with PART_TIMEOUT", async () => {
    Object.defineProperty(globalThis, "XMLHttpRequest", {
      configurable: true,
      value: ControlledXhr,
    });
    formal.listParts
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart]);

    const startPromise = useUploadQueueStore.getState().start(startInput());
    await vi.waitFor(() => expect(ControlledXhr.instances).toHaveLength(1));
    const timedOutXhr = ControlledXhr.instances[0]!;
    expect(timedOutXhr.timeout).toBe(PART_TRANSFER_TIMEOUT_MS);
    const lateAbort = timedOutXhr.onabort;
    timedOutXhr.timeOut();
    lateAbort?.();
    await startPromise;

    const failed = useUploadQueueStore.getState().items[0]!;
    expect(failed).toMatchObject({
      transferStatus: "failed",
      failedParts: [1],
      failedPartTransfers: [{ partNumber: 1, failureCode: "PART_TIMEOUT" }],
      failureCode: "PART_TIMEOUT",
      requestId: null,
      speedBytesPerSecond: null,
      remainingSeconds: null,
    });
    expect(failed.failureMessage).toContain("分片 #1");
    expect(failed.failureMessage).toContain("重试失败分片");
    expect(failed.failureMessage).toContain("已确认分片不会重复上传");
    expect(timedOutXhr.abortCalls).toBe(0);
    expect(formal.complete).not.toHaveBeenCalled();
    expect(formal.commit).not.toHaveBeenCalled();

    const retryPromise = useUploadQueueStore
      .getState()
      .retryFailedParts(failed.id);
    await vi.waitFor(() => expect(ControlledXhr.instances).toHaveLength(2));
    ControlledXhr.instances[1]!.resolve();
    await retryPromise;

    expect(formal.retryParts).toHaveBeenCalledWith(scope, session.session_id, {
      failures: [{ part_number: 1, failure_code: "PART_TIMEOUT" }],
    });
    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "committed",
      failedParts: [],
      failedPartTransfers: [],
    });
  });

  it("aborts the other concurrent part requests when one part times out", async () => {
    Object.defineProperty(globalThis, "XMLHttpRequest", {
      configurable: true,
      value: ControlledXhr,
    });
    const input = multipartInput(3);
    const multipartSession = {
      ...session,
      data_package_id: input.preflight.identifiers.data_package_id,
      expected_size: input.preflight.total_file_size,
    };
    formal.create.mockResolvedValue({
      idempotency_outcome: "CREATED",
      session: multipartSession,
      parts: [1, 2, 3].map(partAuthorization),
    });
    formal.listParts.mockResolvedValueOnce([]);

    const startPromise = useUploadQueueStore.getState().start(input);
    await vi.waitFor(() => expect(ControlledXhr.instances).toHaveLength(3));
    ControlledXhr.instances[0]!.timeOut();
    await startPromise;
    await vi.waitFor(() => {
      expect(ControlledXhr.instances[1]!.abortCalls).toBe(1);
      expect(ControlledXhr.instances[2]!.abortCalls).toBe(1);
    });

    expect(ControlledXhr.instances[0]!.abortCalls).toBe(0);
    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "failed",
      failedParts: [1],
      failureCode: "PART_TIMEOUT",
      speedBytesPerSecond: null,
      remainingSeconds: null,
    });
    expect(formal.complete).not.toHaveBeenCalled();
    expect(formal.commit).not.toHaveBeenCalled();
  });

  it("reconciles an already confirmed part and never retransmits it", async () => {
    Object.defineProperty(globalThis, "XMLHttpRequest", {
      configurable: true,
      value: ControlledXhr,
    });
    const input = multipartInput(2);
    const multipartSession = {
      ...session,
      data_package_id: input.preflight.identifiers.data_package_id,
      expected_size: input.preflight.total_file_size,
    };
    const firstPart = uploadedMultipartPart(1, MIN_MULTIPART_BYTES);
    const secondPart = uploadedMultipartPart(2, 1);
    formal.create.mockResolvedValue({
      idempotency_outcome: "CREATED",
      session: multipartSession,
      parts: [partAuthorization(1), partAuthorization(2)],
    });
    formal.listParts
      .mockResolvedValueOnce([firstPart])
      .mockResolvedValueOnce([firstPart, secondPart]);

    const startPromise = useUploadQueueStore.getState().start(input);
    await vi.waitFor(() => expect(ControlledXhr.instances).toHaveLength(1));
    expect(ControlledXhr.instances[0]!.url).toContain("part-2");
    ControlledXhr.instances[0]!.resolve();
    await startPromise;

    expect(ControlledXhr.instances).toHaveLength(1);
    expect(formal.complete).toHaveBeenCalledWith(scope, session.session_id, {
      parts: [
        { part_number: 1, etag: firstPart.etag },
        { part_number: 2, etag: secondPart.etag },
      ],
    });
  });

  it("treats manual pause and a late timeout event as pause, never PART_TIMEOUT", async () => {
    Object.defineProperty(globalThis, "XMLHttpRequest", {
      configurable: true,
      value: ControlledXhr,
    });
    formal.listParts.mockResolvedValueOnce([]);

    const startPromise = useUploadQueueStore.getState().start(startInput());
    await vi.waitFor(() => expect(ControlledXhr.instances).toHaveLength(1));
    const xhr = ControlledXhr.instances[0]!;
    const lateTimeout = xhr.ontimeout;
    const itemId = useUploadQueueStore.getState().items[0]!.id;
    await useUploadQueueStore.getState().pause(itemId);
    lateTimeout?.();
    await startPromise;

    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "paused",
      failureCode: null,
    });
    expect(useUploadQueueStore.getState().items[0]!.failureCode).not.toBe(
      "PART_TIMEOUT",
    );
  });

  it("preserves a recovered part failure code through file reattachment and retry", async () => {
    formal.listSessions.mockResolvedValue({
      items: [{ ...session, status: "FAILED", failure_code: "PART_TIMEOUT" }],
      total: 1,
    });
    formal.getManifest.mockResolvedValue(preflight);
    formal.listParts
      .mockResolvedValueOnce([timedOutServerPart])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart]);

    await useUploadQueueStore.getState().recover(scope);
    const recovered = useUploadQueueStore.getState().items[0]!;
    expect(recovered.failedPartTransfers).toEqual([
      { partNumber: 1, failureCode: "PART_TIMEOUT" },
    ]);

    await useUploadQueueStore.getState().retryFailedParts(recovered.id);
    expect(useUploadQueueStore.getState().items[0]!.transferStatus).toBe(
      "needs-file",
    );
    await useUploadQueueStore
      .getState()
      .reattachAndResume(recovered.id, selectedFiles()[1]!);

    expect(formal.retryParts).toHaveBeenCalledWith(scope, session.session_id, {
      failures: [{ part_number: 1, failure_code: "PART_TIMEOUT" }],
    });
    expect(useUploadQueueStore.getState().items[0]!.transferStatus).toBe(
      "committed",
    );
  });
});
