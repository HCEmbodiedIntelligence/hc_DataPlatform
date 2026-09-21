import type { components } from "../api/generated/platform";

const qualityFindings = {
  QC_REQUIRED_TOPIC_MISSING: {
    title: "缺少必需的数据通道",
    description: "采集数据缺少必需的传感器或相机通道。",
  },
  QC_TIMESTAMP_DUPLICATE: {
    title: "时间戳重复",
    description: "多条采样数据使用了相同的时间戳，重复数量超过允许范围。",
  },
  QC_TIMESTAMP_BACKWARD: {
    title: "时间戳倒退",
    description: "后采集的数据时间早于前面的数据，时间顺序异常。",
  },
  QC_FREQUENCY_LOW: {
    title: "采样频率偏低",
    description: "单位时间内采集的数据数量不足，未达到要求的采样频率。",
  },
  QC_GAP_EXCESSIVE: {
    title: "数据间隔过长",
    description: "相邻采样数据之间的时间间隔超过允许范围。",
  },
  QC_CONSECUTIVE_FRAMES_MISSING: {
    title: "连续缺帧",
    description: "连续缺失的数据帧数量超过允许范围。",
  },
  QC_COVERAGE_LOW: {
    title: "数据覆盖不足",
    description: "有效采样数据覆盖的时间范围不足，部分时段缺少数据。",
  },
  QC_IMAGE_BLACK: {
    title: "黑帧比例超标",
    description: "视频中黑屏或曝光不足的画面占比超过允许范围。",
  },
  QC_IMAGE_REPEATED: {
    title: "重复帧比例超标",
    description: "视频中连续重复画面的占比超过允许范围。",
  },
  QC_IMAGE_CORRUPT: {
    title: "图像损坏",
    description: "检测到损坏或无法正常解码的图像。",
  },
  QC_JOINT_OUT_OF_RANGE: {
    title: "关节值超出范围",
    description: "机器人关节的采样值超出允许范围。",
  },
  QC_ACTION_MISSING: {
    title: "动作信号缺失",
    description: "采集数据中缺少所需的动作信号。",
  },
  QC_ACTION_JUMP: {
    title: "动作信号突变",
    description: "相邻动作信号的变化幅度超过允许范围。",
  },
  QC_POINT_CLOUD_EMPTY: {
    title: "点云为空",
    description: "检测到不包含有效点的点云数据。",
  },
  QC_POINT_COUNT_ABNORMAL: {
    title: "点云点数异常",
    description: "点云中的点数量超出允许范围。",
  },
  QC_MODALITY_OFFSET: {
    title: "传感器时间不同步",
    description: "不同传感器的数据存在超过允许范围的时间偏移。",
  },
  QC_COMPLETE_STEP_RATIO_LOW: {
    title: "完整采样比例不足",
    description: "同时具备所有必需通道数据的采样时刻占比不足。",
  },
} satisfies Record<
  components["schemas"]["QualityCode"],
  { title: string; description: string }
>;

export function qualityFindingText(code: string): {
  readonly title: string;
  readonly description: string;
} {
  return Object.hasOwn(qualityFindings, code)
    ? qualityFindings[code as keyof typeof qualityFindings]
    : {
        title: "数据质量异常",
        description: "检测到数据质量异常，请结合原始视频和数据进一步检查。",
      };
}

export function qualityProblemSummary(codes: readonly string[]): string {
  return [...new Set(codes.map((code) => qualityFindingText(code).title))].join(
    "、",
  );
}

export function qualityProblemDescription(codes: readonly string[]): string {
  return [
    ...new Set(codes.map((code) => qualityFindingText(code).description)),
  ].join("");
}
