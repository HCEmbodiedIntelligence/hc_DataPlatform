import {
  featureSchema,
  type Feature,
  type McapConfig,
  type SchemeDocument,
} from "./contracts";
export function newFeature(role: Feature["role"]): Feature {
  return {
    dtype: role === "rgb" ? "video" : "float32",
    shape: role === "rgb" ? [1, 1, 3] : role === "depth" ? [1, 1] : [1],
    names: role === "state" || role === "action" ? [""] : null,
    units: role === "state" || role === "action" ? [""] : [],
    role,
    required: true,
    missing: "presence_and_validity",
    validity_feature: null,
    media: {},
  };
}
export function emptyDocument(): SchemeDocument {
  return {
    name: "",
    description: "",
    software: "",
    model_version_ids: [],
    adapters: [
      {
        source_format: "LEROBOT_V3",
        parser_version: "lerobot-v3/1",
        codebase_version: "v3.0",
        feature_sources: {},
      },
    ],
    output: {
      algorithm: "capture-structure/v1",
      frequency_hz: null,
      frequency_policy: "PRESERVE",
      time_source: "FRAME_TIMESTAMP",
      features: {
        "observation.state": newFeature("state"),
        action: newFeature("action"),
      },
    },
    quality_policy: {},
  };
}
export function mcapConfiguration(
  doc: SchemeDocument,
  format: "MCAP" | "CAPTURE_BUNDLE" = "MCAP",
): McapConfig {
  const old = doc.adapters.find((a) => a.source_format === format);
  const previous =
    old && old.source_format !== "LEROBOT_V3" ? old.configuration : null;
  const cameras: McapConfig["cameras"] = Object.entries(doc.output.features)
    .filter(([, feature]) => feature.role === "rgb" || feature.role === "depth")
    .map(([name, feature]) => {
      const id = name.replace(/^observation.images\./, "");
      const mapping = previous?.cameras.find(
        (camera) => camera.camera_id === id,
      );
      return {
        camera_id: id,
        topic: mapping?.topic ?? "",
        message_type: mapping?.message_type ?? "",
        kind: feature.role as "rgb" | "depth",
        required: feature.required,
      };
    });
  const state = Object.values(doc.output.features).find(
    (f) => f.role === "state",
  );
  const action = Object.values(doc.output.features).find(
    (f) => f.role === "action",
  );
  return {
    ...(old && old.source_format !== "LEROBOT_V3"
      ? old.configuration
      : {
          collection_mode: "JOINT_ONLY" as const,
          time_source: "STATE_LOG_TIME" as const,
          cameras: [],
          state_topic: null,
          action_topic: null,
          primary_camera: null,
          depth_scale_m_per_unit: null,
          depth_unit_evidence: null,
          derive_action_from_next_state: false,
          maximum_time_error_ms: 50,
        }),
    fps: doc.output.frequency_hz,
    cameras,
    collection_mode: cameras.length ? "RGB" : "JOINT_ONLY",
    primary_camera: cameras.some(
      (camera) =>
        camera.topic === previous?.primary_camera && camera.kind === "rgb",
    )
      ? previous!.primary_camera
      : null,
    joint_names: Array.isArray(state?.names) ? state.names : [],
    joint_units: state?.units ?? [],
    state_joint_names: Array.isArray(state?.names) ? state.names : [],
    state_joint_units: state?.units ?? [],
    action_joint_names: Array.isArray(action?.names) ? action.names : [],
    action_joint_units: action?.units ?? [],
  };
}
export function normalizeAdapters(doc: SchemeDocument): SchemeDocument {
  return {
    ...doc,
    adapters: doc.adapters.map((adapter) =>
      adapter.source_format !== "LEROBOT_V3"
        ? {
            ...adapter,
            configuration: mcapConfiguration(doc, adapter.source_format),
          }
        : {
            ...adapter,
            feature_sources: Object.fromEntries(
              Object.entries(adapter.feature_sources).filter(
                ([name]) => name in doc.output.features,
              ),
            ),
          },
    ),
  };
}
export function validateEditor(doc: SchemeDocument): string[] {
  const errors: string[] = [];
  if (!doc.name.trim()) errors.push("请输入方案名称");
  if (
    doc.output.frequency_hz !== null &&
    !(doc.output.frequency_hz > 0 && doc.output.frequency_hz <= 1000)
  )
    errors.push("采样帧率填写时须大于 0 且不超过 1000 Hz");
  if (!doc.adapters.length) errors.push("至少选择一种输入格式");
  if (!Object.keys(doc.output.features).length)
    errors.push("至少定义一个数据字段");
  for (const [key, f] of Object.entries(doc.output.features)) {
    if (
      !key.trim() ||
      key.startsWith("__hc_") ||
      !featureSchema.safeParse(f).success
    )
      errors.push(`${key}：字段名称、类型和维度必须有效`);
    if (["state", "action"].includes(f.role)) {
      if (
        !Array.isArray(f.names) ||
        f.names.length !== f.shape[0] ||
        f.names.some((n) => !n.trim()) ||
        new Set(f.names).size !== f.names.length
      )
        errors.push(`${key}：按实际顺序填写不重复的关节名称`);
      if (
        f.units.length !== f.shape[0] ||
        f.units.some((u) =>
          ["", "unknown", "未知"].includes(u.trim().toLowerCase()),
        )
      )
        errors.push(`${key}：每个关节必须明确单位，不从维数推测`);
    }
    if (
      f.validity_feature &&
      (doc.output.features[f.validity_feature]?.role !== "validity" ||
        !doc.output.features[f.validity_feature]?.required)
    )
      errors.push(`${key}：有效性字段必须指向必需的 validity 字段`);
  }
  for (const mcap of doc.adapters) {
    if (mcap.source_format === "LEROBOT_V3") continue;
    if (
      doc.output.frequency_hz !== null &&
      (!Number.isInteger(doc.output.frequency_hz) ||
        doc.output.frequency_hz > 240)
    )
      errors.push(
        "MCAP 原始帧率须为 1–240 Hz 的整数；不支持的频率不能改填其他值",
      );
    if (!mcap.configuration.state_topic || !mcap.configuration.action_topic)
      errors.push("请明确 MCAP 状态和动作来源话题");
    if (
      mcap.configuration.time_source === "RGB_LOG_TIME" &&
      !mcap.configuration.primary_camera
    )
      errors.push(
        "使用相机时间轴时，请选择真实主相机；无相机时请选择状态消息记录时间",
      );
    for (const camera of mcap.configuration.cameras) {
      if (!camera.topic.trim() || !camera.message_type.trim())
        errors.push(`${camera.camera_id}：请填写真实相机话题及消息类型`);
      if ((camera.kind === "depth") !== camera.camera_id.endsWith("_depth"))
        errors.push(
          `${camera.camera_id}：深度相机名称须以 _depth 结尾，RGB 相机不可使用该后缀`,
        );
    }
  }
  return errors;
}
export function candidateFromInfo(
  doc: SchemeDocument,
  info: Record<string, unknown>,
): SchemeDocument {
  const features: Record<string, Feature> = {};
  for (const [name, raw] of Object.entries(
    (info.features ?? {}) as Record<string, Record<string, unknown>>,
  )) {
    if (
      [
        "index",
        "frame_index",
        "episode_index",
        "task_index",
        "timestamp",
      ].includes(name)
    )
      continue;
    const role =
      name === "observation.state"
        ? "state"
        : name === "action"
          ? "action"
          : name.startsWith("observation.images.")
            ? (raw.info as Record<string, unknown> | undefined)?.[
                "video.is_depth_map"
              ] === true
              ? "depth"
              : "rgb"
            : [
                  "observation.source_available",
                  "observation.source_valid",
                ].includes(name)
              ? "validity"
              : [
                    "observation.source_time",
                    "observation.recording_time",
                  ].includes(name)
                ? "timestamp"
                : "numeric";
    const shape = Array.isArray(raw.shape) ? raw.shape.map(Number) : [1];
    const names = Array.isArray(raw.names) ? raw.names.map(String) : null;
    features[name] = {
      ...newFeature(role),
      dtype: String(raw.dtype ?? ""),
      shape,
      names,
      units: Array.isArray(raw.units)
        ? raw.units.map(String)
        : role === "state" || role === "action"
          ? (Array(shape[0]).fill("") as string[])
          : [],
      media: (raw.info ?? {}) as Feature["media"],
    };
  }
  return {
    ...doc,
    adapters: [
      {
        source_format: "LEROBOT_V3",
        parser_version: "lerobot-v3/1",
        codebase_version: "v3.0",
        feature_sources: {},
      },
    ],
    output: {
      ...doc.output,
      frequency_hz: info.fps == null ? null : Number(info.fps),
      time_source: "FRAME_TIMESTAMP",
      features,
    },
  };
}
