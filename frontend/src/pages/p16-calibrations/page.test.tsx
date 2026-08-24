// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import CalibrationsPage from "./page";

vi.mock("./components/CalibrationThreePreview", () => ({
  CalibrationThreePreview: ({
    document,
  }: {
    document: { frame_transforms: unknown[] };
  }) => (
    <div data-testid="real-three-preview">
      {document.frame_transforms.length} 真实变换
    </div>
  ),
}));

vi.mock("../../shared/auth/use-capabilities", () => ({
  useCapabilities: () => ({
    has: (capability: string) => capability === "calibration.publish",
    loading: false,
    failed: false,
  }),
}));

const {
  associateDatasetMutate,
  createMutate,
  pageCalibration,
  recalibrateMutate,
  validateMutate,
} = vi.hoisted(() => ({
  associateDatasetMutate: vi.fn(),
  createMutate: vi.fn(),
  pageCalibration: { snapshotStatus: "DRAFT" as "DRAFT" | "READY" },
  recalibrateMutate: vi.fn(),
  validateMutate: vi.fn(),
}));

const calibration = {
  id: "set-1",
  robotId: "robot-1",
  componentId: "camera-1",
  version: "1",
  snapshotStatus: "DRAFT",
  availability: null,
  contentHash: "a".repeat(64),
  validationContextHash: "b".repeat(64),
  validation: {
    status: "PASSED",
    contentHash: "a".repeat(64),
    validationContextHash: "b".repeat(64),
    reportId: "report-1",
  },
  etag: '"calibration:set-1:2"',
  allowedActions: ["VIEW", "VALIDATE", "PUBLISH"],
  blockedReasons: [],
} as const;

vi.mock("../../features/calibrations/api", () => ({
  useCalibrationSets: () => ({
    isPending: false,
    error: null,
    refetch: vi.fn(),
    data: {
      items: [calibration],
      pageInfo: {
        start_cursor: null,
        end_cursor: null,
        has_previous_page: false,
        has_next_page: false,
      },
      snapshotAt: "2026-08-21T08:00:00Z",
    },
  }),
  useCalibrationSet: () => ({
    data: { ...calibration, snapshotStatus: pageCalibration.snapshotStatus },
  }),
  useCalibrationVersions: () => ({
    isPending: false,
    error: null,
    refetch: vi.fn(),
    data: [
      {
        version: "2",
        source: "RECALIBRATION",
        content_hash: "c".repeat(64),
        created_by: "calibration-editor",
        created_at: "2026-08-21T08:05:00Z",
      },
      {
        version: "1",
        source: "IMPORT",
        content_hash: "a".repeat(64),
        created_by: "calibration-editor",
        created_at: "2026-08-21T08:00:00Z",
      },
    ],
  }),
  useCalibrationDatasetAssociations: () => ({
    isPending: false,
    error: null,
    refetch: vi.fn(),
    data: [],
  }),
  useCalibrationVersionDocument: () => ({
    isPending: false,
    error: null,
    refetch: vi.fn(),
    data: {
      set_id: "set-1",
      version: "1",
      source: "IMPORT",
      content_hash: "a".repeat(64),
      created_by: "calibration-editor",
      created_at: "2026-08-21T08:00:00Z",
      document: {
        frame_transforms: [
          {
            parent_frame: "base_link",
            child_frame: "camera_front",
            translation_m: [0.12, 0, 0.42],
            quaternion_xyzw: [0, 0, 0, 1],
            covariance: null,
          },
        ],
        camera_intrinsics: [
          {
            frame_id: "camera_front",
            width_px: 1920,
            height_px: 1080,
            fx_px: 1010,
            fy_px: 1008,
            cx_px: 960,
            cy_px: 540,
            distortion: [],
          },
        ],
      },
    },
  }),
  useCalibrationValidationReport: () => ({
    isPending: false,
    error: null,
    refetch: vi.fn(),
    data: {
      id: "report-1",
      status: "PASSED",
      checked_at: "2026-08-21T08:00:00Z",
      findings: [],
    },
  }),
  useCreateCalibrationSet: () => ({
    isPending: false,
    error: null,
    mutate: createMutate,
  }),
  useRecalibrateCalibrationSet: () => ({
    isPending: false,
    error: null,
    mutate: recalibrateMutate,
  }),
  useAssociateCalibrationDatasetVersion: () => ({
    isPending: false,
    error: null,
    mutate: associateDatasetMutate,
  }),
  useValidateCalibrationVersion: () => ({
    isPending: false,
    error: null,
    mutate: validateMutate,
  }),
  usePreflightCalibrationPublish: () => ({
    isPending: false,
    error: null,
    data: null,
    mutate: vi.fn(),
  }),
  usePublishCalibration: () => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
  }),
  parseCalibrationDocumentInput: (value: unknown) => value,
}));

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={[
        "/settings/calibrations?robotId=robot-1&componentId=camera-1&setId=set-1",
      ]}
    >
      <CalibrationsPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  pageCalibration.snapshotStatus = "DRAFT";
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  const computedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computedStyle(element),
  );
  if (!globalThis.crypto.randomUUID) {
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: () => "p16-test-key",
    });
  }
  createMutate.mockImplementation((input, callbacks) =>
    callbacks?.onSuccess?.({ ...calibration, id: input.input.set_id }),
  );
  recalibrateMutate.mockImplementation((input, callbacks) =>
    callbacks?.onSuccess?.({
      ...calibration,
      version: "2",
      contentHash: "c".repeat(64),
      validationContextHash: null,
      validation: null,
      etag: '"calibration:set-1:3"',
    }),
  );
  associateDatasetMutate.mockImplementation((_input, callbacks) =>
    callbacks?.onSuccess?.(),
  );
  validateMutate.mockImplementation(() => undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("P16 real calibration page", () => {
  it("loads real transforms, submits an import draft and starts server validation", async () => {
    const user = userEvent.setup();
    renderPage();
    expect(screen.getByTestId("real-three-preview")).toHaveTextContent(
      "1 真实变换",
    );
    expect(screen.getAllByText("base_link → camera_front")).not.toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "导入或新建标定" }));
    await user.type(screen.getByLabelText("标定集 ID"), "camera-import-v1");
    await user.type(screen.getByLabelText("机器人 ID"), "robot-1");
    await user.type(screen.getByLabelText("组件 ID"), "camera-1");
    fireEvent.change(screen.getByLabelText("标定 JSON 文档"), {
      target: {
        value: JSON.stringify({
          frame_transforms: [
            {
              parent_frame: "base_link",
              child_frame: "camera_front",
              translation_m: [0.12, 0, 0.42],
              quaternion_xyzw: [0, 0, 0, 1],
            },
          ],
          camera_intrinsics: [],
        }),
      },
    });
    await user.click(
      screen.getByRole("button", { name: "保存草稿并开始校验" }),
    );
    expect(createMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        input: expect.objectContaining({
          set_id: "camera-import-v1",
          robot_instance_id: "robot-1",
          component_id: "camera-1",
          source: "IMPORT",
        }),
      }),
      expect.any(Object),
    );

    await user.click(screen.getByRole("button", { name: "校验当前版本" }));
    expect(validateMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        setId: "set-1",
        version: "1",
        etag: '"calibration:set-1:2"',
      }),
    );
    await user.click(screen.getByRole("button", { name: "查看报告" }));
    expect(screen.getByText("服务端未发现校验问题。")).toBeInTheDocument();
  });

  it("uses a real historical document to create an immutable successor draft", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByRole("button", { name: "v1 · IMPORT" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "重新标定" }));
    expect(screen.getByLabelText("标定集 ID")).toBeDisabled();
    expect(screen.getByLabelText("机器人 ID")).toBeDisabled();
    await user.type(
      screen.getByLabelText("重新标定说明"),
      "更换了相机支架后重新实测外参。",
    );
    fireEvent.change(screen.getByLabelText("标定 JSON 文档"), {
      target: {
        value: JSON.stringify({
          frame_transforms: [
            {
              parent_frame: "base_link",
              child_frame: "camera_front",
              translation_m: [0.15, 0, 0.42],
              quaternion_xyzw: [0, 0, 0, 1],
              covariance: null,
            },
          ],
          camera_intrinsics: [],
        }),
      },
    });
    await user.click(screen.getByRole("button", { name: "创建后继草稿" }));

    expect(recalibrateMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        setId: "set-1",
        etag: '"calibration:set-1:2"',
        input: {
          change_summary: "更换了相机支架后重新实测外参。",
          document: expect.objectContaining({
            frame_transforms: [
              expect.objectContaining({ translation_m: [0.15, 0, 0.42] }),
            ],
          }),
        },
      }),
      expect.any(Object),
    );
  });

  it("pins the ready current calibration version to a real dataset version", async () => {
    const user = userEvent.setup();
    pageCalibration.snapshotStatus = "READY";
    renderPage();
    await user.click(screen.getByRole("button", { name: "关联数据版本" }));
    await user.type(screen.getByLabelText("数据集 ID"), "dataset_p16ui");
    await user.type(screen.getByLabelText("数据集版本 ID"), "version_p16ui");
    await user.click(screen.getByRole("button", { name: "固定关联" }));

    expect(associateDatasetMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        setId: "set-1",
        version: "1",
        etag: '"calibration:set-1:2"',
        input: {
          dataset_id: "dataset_p16ui",
          dataset_version_id: "version_p16ui",
        },
      }),
      expect.any(Object),
    );
  });
});
