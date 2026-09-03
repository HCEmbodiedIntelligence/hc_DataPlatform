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
  preflight: vi.fn(),
  renew: vi.fn(),
  resume: vi.fn(),
  retryParts: vi.fn(),
}));

const leRobot = vi.hoisted(() => ({ upload: vi.fn() }));

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
    preflightUploadManifest: formal.preflight,
    renewFormalUploadParts: formal.renew,
    resumeFormalUpload: formal.resume,
    retryFormalUploadParts: formal.retryParts,
  };
});

vi.mock("./lerobot-client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lerobot-client")>();
  return { ...actual, uploadNativeLeRobot: leRobot.upload };
});

import {
  MAX_CONCURRENT_PART_UPLOADS,
  MAX_IN_FLIGHT_PART_BYTES,
  PART_TRANSFER_TIMEOUT_MS,
  PRESIGNED_URL_SETTLE_MARGIN_MS,
  SLOW_LINK_BYTES_PER_SECOND,
  partUploadConcurrency,
  partTransferTimeoutMs,
  resetUploadQueueStoreForTests,
  useUploadQueueStore,
} from "./upload-queue-store";
import {
  MAX_PACKAGE_BYTES,
  MIN_MULTIPART_BYTES,
  planUploadParts,
} from "./upload-contract";
import {
  LeRobotUploadFailure,
  type LeRobotUploadProgress,
} from "./lerobot-client";

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
  processing_mode: "DIRECT_EPISODE" as const,
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
  expires_at: "2036-08-18T05:10:00Z",
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
  leRobot.upload.mockReset();
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
  formal.preflight.mockImplementation((_scope, receivedManifest) =>
    Promise.resolve({
      ...preflight,
      identifiers: {
        ...preflight.identifiers,
        data_package_id: receivedManifest.data_package_id,
      },
      files: [...receivedManifest.files],
      total_file_size: receivedManifest.file_size,
      manifest: receivedManifest,
    }),
  );
});

afterEach(() => resetUploadQueueStoreForTests());

describe("P03 resumable upload queue", () => {
  function folderBundle(dataPackageId: string) {
    const bundledManifest = {
      ...manifest,
      data_package_id: dataPackageId,
      rollout_id: `rollout-${dataPackageId}`,
    };
    const manifestFile = new File(
      [JSON.stringify(bundledManifest)],
      "rollout_manifest.json",
      { type: "application/json" },
    );
    const rawFile = new File([new Uint8Array(totalBytes)], "recording.mcap", {
      type: "application/octet-stream",
    });
    Object.defineProperty(manifestFile, "webkitRelativePath", {
      value: `factory/2026-08-21/${dataPackageId}/rollout_manifest.json`,
    });
    Object.defineProperty(rawFile, "webkitRelativePath", {
      value: `factory/2026-08-21/${dataPackageId}/recording.mcap`,
    });
    return {
      id: `factory/${dataPackageId}`,
      manifestFile,
      rawFile,
      manifest: bundledManifest,
      relativeDirectory: `factory/2026-08-21/${dataPackageId}`,
    };
  }

  it("creates a real waiting session before any object bytes are transferred", async () => {
    const itemId = await useUploadQueueStore.getState().prepare(startInput());

    expect(formal.create).toHaveBeenCalledTimes(1);
    expect(formal.listParts).not.toHaveBeenCalled();
    expect(useUploadQueueStore.getState().items).toEqual([
      expect.objectContaining({
        id: itemId,
        sessionId: session.session_id,
        transferStatus: "waiting",
        uploadedBytes: 0,
      }),
    ]);

    formal.listParts
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart]);
    await useUploadQueueStore.getState().beginPrepared(itemId);

    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "committed",
      uploadedBytes: totalBytes,
    });
  });

  it("uploads a nested-folder batch sequentially with one stable idempotency key per package", async () => {
    const bundles = [folderBundle("folder-a"), folderBundle("folder-b")];
    formal.listParts
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart]);

    await useUploadQueueStore.getState().startFolderBatch({ scope, bundles });

    expect(formal.preflight).toHaveBeenCalledTimes(2);
    expect(formal.create).toHaveBeenCalledTimes(2);
    expect(formal.create.mock.calls.map((call) => call[2])).toEqual([
      expect.stringContaining("folder-import:folder-a:"),
      expect.stringContaining("folder-import:folder-b:"),
    ]);
    expect(useUploadQueueStore.getState().folderBatch).toMatchObject({
      total: 2,
      completed: 2,
      failed: 0,
      status: "completed",
    });
    expect(
      useUploadQueueStore.getState().items.map((item) => item.dataPackageId),
    ).toEqual(["folder-b", "folder-a"]);
    expect(
      useUploadQueueStore
        .getState()
        .items.every((item) => item.transferStatus === "committed"),
    ).toBe(true);
  });

  it("continues with later packages when one folder Manifest fails server preflight", async () => {
    const bundles = [
      folderBundle("folder-invalid"),
      folderBundle("folder-valid"),
    ];
    formal.preflight
      .mockRejectedValueOnce(new Error("server preflight rejected package"))
      .mockImplementationOnce((_scope, receivedManifest) =>
        Promise.resolve({
          ...preflight,
          identifiers: {
            ...preflight.identifiers,
            data_package_id: receivedManifest.data_package_id,
          },
          files: [...receivedManifest.files],
          total_file_size: receivedManifest.file_size,
          manifest: receivedManifest,
        }),
      );
    formal.listParts
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart]);

    await useUploadQueueStore.getState().startFolderBatch({ scope, bundles });

    expect(formal.create).toHaveBeenCalledTimes(1);
    expect(useUploadQueueStore.getState().folderBatch).toMatchObject({
      total: 2,
      completed: 2,
      failed: 1,
      status: "completed",
    });
    expect(
      useUploadQueueStore
        .getState()
        .items.find((item) => item.dataPackageId === "folder-invalid"),
    ).toMatchObject({ transferStatus: "failed" });
    expect(
      useUploadQueueStore
        .getState()
        .items.find((item) => item.dataPackageId === "folder-valid"),
    ).toMatchObject({ transferStatus: "committed" });
  });

  it("keeps a selected folder batch pending offline and starts it automatically once connected", async () => {
    Object.defineProperty(globalThis.navigator, "onLine", {
      configurable: true,
      value: false,
    });
    const bundles = [folderBundle("folder-after-network")];

    await useUploadQueueStore.getState().startFolderBatch({ scope, bundles });
    expect(useUploadQueueStore.getState().folderBatch).toMatchObject({
      status: "offline",
      completed: 0,
    });
    expect(formal.preflight).not.toHaveBeenCalled();

    Object.defineProperty(globalThis.navigator, "onLine", {
      configurable: true,
      value: true,
    });
    formal.listParts
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart]);
    useUploadQueueStore.getState().handleOnline();

    await vi.waitFor(() =>
      expect(useUploadQueueStore.getState().folderBatch).toMatchObject({
        status: "completed",
        completed: 1,
      }),
    );
  });

  it("reattaches every recovered package from one selected nested folder without creating duplicate sessions", async () => {
    formal.listSessions.mockResolvedValue({
      items: [{ ...session, status: "UPLOADING" }],
      total: 1,
    });
    formal.getManifest.mockResolvedValue(preflight);
    formal.listParts
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([uploadedPart]);

    await useUploadQueueStore.getState().recover(scope);
    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "needs-file",
      dataPackageId: manifest.data_package_id,
    });

    await useUploadQueueStore.getState().startFolderBatch({
      scope,
      bundles: [folderBundle(manifest.data_package_id)],
    });

    expect(formal.preflight).not.toHaveBeenCalled();
    expect(formal.create).not.toHaveBeenCalled();
    expect(useUploadQueueStore.getState().folderBatch).toMatchObject({
      total: 1,
      completed: 1,
      failed: 0,
      status: "completed",
    });
    expect(useUploadQueueStore.getState().items).toHaveLength(1);
    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "committed",
      dataPackageId: manifest.data_package_id,
    });
  });

  it("uses four bounded workers for small multipart parts and protects the in-flight byte budget", () => {
    expect(
      partUploadConcurrency(
        { partSize: 16 * 1024 ** 2, partCount: 20, partNumbers: [] },
        20,
      ),
    ).toBe(MAX_CONCURRENT_PART_UPLOADS);
    expect(
      partUploadConcurrency(
        {
          partSize: MAX_IN_FLIGHT_PART_BYTES + 1,
          partCount: 2,
          partNumbers: [],
        },
        2,
      ),
    ).toBe(1);
  });

  it("scales a legal TiB part timeout without exceeding its presigned URL window", () => {
    const nowMs = Date.parse("2030-01-01T00:00:00Z");
    const maximum = planUploadParts(MAX_PACKAGE_BYTES);
    const largeAuthorization = {
      ...authorization,
      expires_at: new Date(nowMs + 15 * 60_000).toISOString(),
    };
    const expectedLargePartTimeout =
      Math.ceil((maximum.partSize / SLOW_LINK_BYTES_PER_SECOND) * 1_000) +
      PRESIGNED_URL_SETTLE_MARGIN_MS;

    expect(
      partTransferTimeoutMs(maximum.partSize, largeAuthorization, nowMs),
    ).toBe(expectedLargePartTimeout);
    expect(expectedLargePartTimeout).toBeGreaterThan(PART_TRANSFER_TIMEOUT_MS);
    expect(
      partTransferTimeoutMs(
        maximum.partSize,
        {
          ...largeAuthorization,
          expires_at: new Date(nowMs + 90_000).toISOString(),
        },
        nowMs,
      ),
    ).toBe(90_000 - PRESIGNED_URL_SETTLE_MARGIN_MS);
  });

  it("rejects invalid or expired part authorizations", () => {
    const nowMs = Date.parse("2030-01-01T00:00:00Z");
    expect(() =>
      partTransferTimeoutMs(
        1024,
        { ...authorization, expires_at: "not-a-timestamp" },
        nowMs,
      ),
    ).toThrow("part authorization is invalid or expired");
    expect(() =>
      partTransferTimeoutMs(
        1024,
        { ...authorization, expires_at: new Date(nowMs).toISOString() },
        nowMs,
      ),
    ).toThrow("part authorization is invalid or expired");
  });

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

  it("walks every signed recovery page sequentially instead of truncating a large folder at 100 sessions", async () => {
    const nextCursor = "signed-upload-session-cursor-page-2";
    const secondSession = {
      ...session,
      session_id: "upload-session-page-2",
      rollout_id: "rollout-page-2",
      data_package_id: "package-page-2",
    };
    formal.listSessions
      .mockResolvedValueOnce({
        items: [session],
        total: 1,
        next_cursor: nextCursor,
      })
      .mockResolvedValueOnce({
        items: [secondSession],
        total: 1,
        next_cursor: null,
      });
    formal.getManifest.mockResolvedValue(preflight);
    formal.listParts.mockResolvedValue([]);

    await useUploadQueueStore.getState().recover(scope);

    expect(formal.listSessions).toHaveBeenNthCalledWith(1, scope, {
      limit: 100,
      cursor: undefined,
    });
    expect(formal.listSessions).toHaveBeenNthCalledWith(2, scope, {
      limit: 100,
      cursor: nextCursor,
    });
    expect(
      useUploadQueueStore.getState().items.map((item) => item.dataPackageId),
    ).toEqual([manifest.data_package_id, secondSession.data_package_id]);
  });

  it("keeps a visible recovery failure until a later retry succeeds", async () => {
    formal.listSessions.mockRejectedValueOnce(new Error("gateway unavailable"));

    await useUploadQueueStore.getState().recover(scope);
    expect(useUploadQueueStore.getState()).toMatchObject({
      recovering: false,
      recoveryProblem: {
        title: "上传操作未完成",
      },
    });

    formal.listSessions.mockResolvedValueOnce({
      items: [],
      total: 0,
      next_cursor: null,
    });
    await useUploadQueueStore.getState().recover(scope);
    expect(useUploadQueueStore.getState()).toMatchObject({
      recovering: false,
      recoveryProblem: null,
    });
  });

  it("keeps native LeRobot progress in the same queue after route subscribers leave", async () => {
    let reportProgress!: (value: {
      stage: "uploading";
      transferMode: "direct";
      currentPath: string;
      completedFiles: number;
      totalFiles: number;
      uploadedBytes: number;
      totalBytes: number;
    }) => void;
    let resolveUpload!: (value: {
      schema_version: "lerobot-web-import-accepted/v1";
      import_id: string;
      status: "EPISODES_QUEUED";
      episode_count: number;
      source_file_count: number;
      episode_task_count: number;
      episode_plan_key: string;
    }) => void;
    const importId = "a".repeat(32);
    leRobot.upload.mockImplementation(
      (_scope, selection, _binding, onProgress, _resume, options) => {
        reportProgress = onProgress;
        options.onSession({
          importId,
          assets: [],
          transferMode: "direct",
        });
        return new Promise((resolve) => {
          resolveUpload = resolve;
        });
      },
    );
    const source = new File(["lerobot-source"], "info.json");
    const selection = {
      format: "lerobot" as const,
      version: "v3.0" as const,
      robotType: "unitree_g1" as const,
      rootDirectory: "factory-run",
      info: { total_episodes: 1 },
      episodeCount: 1,
      sourceFiles: [{ file: source, path: "meta/info.json" }],
      sourceBytes: source.size,
    };

    const start = useUploadQueueStore.getState().startLeRobot({
      scope,
      selection,
      binding: {
        datasetId: "dataset-a",
        collectionTaskId: "task-a",
        robotId: "robot-a",
      },
    });
    const unsubscribe = useUploadQueueStore.subscribe(() => {});
    unsubscribe();
    reportProgress({
      stage: "uploading",
      transferMode: "direct",
      currentPath: "meta/info.json",
      completedFiles: 0,
      totalFiles: 1,
      uploadedBytes: 5,
      totalBytes: source.size,
    });

    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      sourceType: "LEROBOT_NATIVE",
      sessionId: importId,
      transferStatus: "uploading",
      uploadedBytes: 5,
      dataPackageId: "dataset-a",
      robotId: "robot-a",
    });

    resolveUpload({
      schema_version: "lerobot-web-import-accepted/v1",
      import_id: importId,
      status: "EPISODES_QUEUED",
      episode_count: 1,
      source_file_count: 1,
      episode_task_count: 1,
      episode_plan_key: "derived/plan.json",
    });
    await start;
    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "committed",
      completedParts: 1,
      totalParts: 1,
    });
  });

  it("throttles rapid LeRobot progress paints without hiding finalization", async () => {
    let reportProgress!: (value: LeRobotUploadProgress) => void;
    let resolveUpload!: (value: {
      schema_version: "lerobot-web-import-accepted/v1";
      import_id: string;
      status: "EPISODES_QUEUED";
      episode_count: number;
      source_file_count: number;
      episode_task_count: number;
      episode_plan_key: string;
    }) => void;
    const importId = "c".repeat(32);
    leRobot.upload.mockImplementation(
      (_scope, _selection, _binding, onProgress, _resume, options) => {
        reportProgress = onProgress;
        options.onSession({
          importId,
          assets: [],
          transferMode: "direct",
        });
        return new Promise((resolve) => {
          resolveUpload = resolve;
        });
      },
    );
    const source = new File(["lerobot-source"], "info.json");
    const selection = {
      format: "lerobot" as const,
      version: "v3.0" as const,
      robotType: "unitree_g1" as const,
      rootDirectory: "factory-run",
      info: { total_episodes: 1 },
      episodeCount: 1,
      sourceFiles: [{ file: source, path: "meta/info.json" }],
      sourceBytes: source.size,
    };
    let notifications = 0;
    const unsubscribe = useUploadQueueStore.subscribe(() => {
      notifications += 1;
    });
    const start = useUploadQueueStore.getState().startLeRobot({
      scope,
      selection,
      binding: {
        datasetId: "dataset-a",
        collectionTaskId: "task-a",
        robotId: "robot-a",
      },
    });
    const beforeProgress = notifications;

    for (let uploadedBytes = 0; uploadedBytes < 100; uploadedBytes += 1) {
      reportProgress({
        stage: "uploading",
        transferMode: "direct",
        currentPath: "meta/info.json",
        completedFiles: 0,
        totalFiles: 1,
        uploadedBytes,
        totalBytes: 100,
      });
    }
    expect(notifications - beforeProgress).toBe(1);

    reportProgress({
      stage: "committing",
      transferMode: "direct",
      currentPath: null,
      completedFiles: 1,
      totalFiles: 1,
      uploadedBytes: 100,
      totalBytes: 100,
    });
    expect(notifications - beforeProgress).toBe(2);

    resolveUpload({
      schema_version: "lerobot-web-import-accepted/v1",
      import_id: importId,
      status: "EPISODES_QUEUED",
      episode_count: 1,
      source_file_count: 1,
      episode_task_count: 1,
      episode_plan_key: "derived/plan.json",
    });
    await start;
    unsubscribe();
    expect(useUploadQueueStore.getState().items[0]?.transferStatus).toBe(
      "committed",
    );
  });

  it("shows paused LeRobot state and resumes the same import session", async () => {
    const importId = "b".repeat(32);
    const resume = {
      importId,
      assets: [],
      transferMode: "direct" as const,
    };
    leRobot.upload
      .mockImplementationOnce(
        (_scope, _selection, _binding, _onProgress, _resume, options) => {
          options.onSession(resume);
          return new Promise((_resolve, reject) => {
            options.signal.addEventListener(
              "abort",
              () =>
                reject(
                  new LeRobotUploadFailure(
                    "上传已暂停",
                    resume,
                    new Error("paused"),
                  ),
                ),
              { once: true },
            );
          });
        },
      )
      .mockResolvedValueOnce({
        schema_version: "lerobot-web-import-accepted/v1",
        import_id: importId,
        status: "EPISODES_QUEUED",
        episode_count: 1,
        source_file_count: 1,
        episode_task_count: 1,
        episode_plan_key: "derived/plan.json",
      });
    const source = new File(["raw"], "info.json");
    const start = useUploadQueueStore.getState().startLeRobot({
      scope,
      selection: {
        format: "lerobot",
        version: "v3.0",
        robotType: "unitree_g1",
        rootDirectory: "source",
        info: { total_episodes: 1 },
        episodeCount: 1,
        sourceFiles: [{ file: source, path: "meta/info.json" }],
        sourceBytes: source.size,
      },
      binding: {
        datasetId: "dataset-a",
        collectionTaskId: "task-a",
        robotId: "robot-a",
      },
    });
    const itemId = useUploadQueueStore.getState().items[0]!.id;
    await useUploadQueueStore.getState().pause(itemId);
    await start;
    expect(useUploadQueueStore.getState().items[0]!.transferStatus).toBe(
      "paused",
    );

    await useUploadQueueStore.getState().resume(itemId);
    expect(leRobot.upload).toHaveBeenNthCalledWith(
      2,
      scope,
      expect.anything(),
      expect.anything(),
      expect.any(Function),
      resume,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(useUploadQueueStore.getState().items[0]!.transferStatus).toBe(
      "committed",
    );
  });

  it("ignores late LeRobot progress after a terminal upload failure", async () => {
    const importId = "c".repeat(32);
    const resume = {
      importId,
      assets: [],
      transferMode: "proxy" as const,
    };
    let reportProgress!: (value: {
      stage: "uploading";
      transferMode: "proxy";
      currentPath: string;
      completedFiles: number;
      totalFiles: number;
      uploadedBytes: number;
      totalBytes: number;
    }) => void;
    leRobot.upload.mockImplementation(
      (_scope, _selection, _binding, onProgress, _resume, options) => {
        reportProgress = onProgress;
        options.onSession(resume);
        return Promise.reject(
          new LeRobotUploadFailure(
            "文件 videos/camera/file-002.mp4 上传中断",
            resume,
            new Error("The server could not complete the request."),
          ),
        );
      },
    );
    const source = new File(["raw"], "file-002.mp4");

    await useUploadQueueStore.getState().startLeRobot({
      scope,
      selection: {
        format: "lerobot",
        version: "v3.0",
        robotType: "unitree_g1",
        rootDirectory: "source",
        info: { total_episodes: 1 },
        episodeCount: 1,
        sourceFiles: [{ file: source, path: "videos/camera/file-002.mp4" }],
        sourceBytes: source.size,
      },
      binding: {
        datasetId: "dataset-a",
        collectionTaskId: "task-a",
        robotId: "robot-a",
      },
    });

    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "failed",
      sessionId: importId,
    });
    reportProgress({
      stage: "uploading",
      transferMode: "proxy",
      currentPath: "videos/camera/file-002.mp4",
      completedFiles: 0,
      totalFiles: 1,
      uploadedBytes: source.size,
      totalBytes: source.size,
    });

    expect(useUploadQueueStore.getState().items[0]).toMatchObject({
      transferStatus: "failed",
      uploadedBytes: 0,
      failureMessage: "文件 videos/camera/file-002.mp4 上传中断",
    });
  });
});
