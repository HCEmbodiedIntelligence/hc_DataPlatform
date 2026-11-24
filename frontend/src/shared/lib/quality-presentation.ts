import type { components } from "../api/generated/platform";

const qualityFindings = {
  QC_REQUIRED_TOPIC_MISSING: {
    title: "缺少必需的数据通道",
    description: "逐个核对方案要求的通道。若某个通道完全没有出现在本次质检的采样清单中，就报告该通道的名称。通道存在但采样稀疏，由频率或缺采样规则检查。",
  },
  QC_TIMESTAMP_DUPLICATE: {
    title: "时间戳重复",
    description: "在同一通道、同一检查片段内，用采样条数减去不同时间戳的个数。一个时间戳出现 3 次，计为重复 2 次；不同相机在同一时刻各采一帧不会计为重复。次数严格超过阈值才触发，相等时通过。",
  },
  QC_TIMESTAMP_BACKWARD: {
    title: "时间戳倒退",
    description: "按通道内的原始记录顺序，两两比较相邻采样的时间戳。后一条早于前一条就计 1 次倒退；时间戳相等计入重复。倒退次数超过拒绝阈值时，标记发生倒退的时间段。",
  },
  QC_FREQUENCY_LOW: {
    title: "采样频率偏低",
    description: "每个通道单独计算。用有效采样时段内不同时间戳的数量除以时长（秒），再与方案基准频率乘以提示比例进行比较。例：基准 30 Hz、提示比例 90% 时，低于 27 Hz 才提示。当前采集方案按提示阈值判定。",
  },
  QC_GAP_EXCESSIVE: {
    title: "数据间隔过长",
    description: "将同一通道的时间戳去重、排序，计算相邻两次采样的时间差，取最大值。当前采集方案在指标中记录这个间隔；片段内部的缺口由“连续缺帧 / 缺采样”产生提示。",
  },
  QC_CONSECUTIVE_FRAMES_MISSING: {
    title: "连续缺帧 / 缺采样",
    description:
      "把同一通道相邻两次真实采样换算成目标频率下的帧序号，中间跳过多少个序号，就连续缺多少帧。例如从第 10 帧直接到第 14 帧，缺 3 帧。当前采集方案对超过提示阈值的内部缺口标记时间段，供人工复核。",
  },
  QC_LEADING_IDLE: {
    title: "开头等待段（正常操作）",
    description:
      "对每个有采样的必需通道，从片段开始到该通道首条采样之前，有一段空白就记录其时长。此项为信息提示，供转换时确定各通道共同的起点。",
  },
  QC_TRAILING_IDLE: {
    title: "结尾等待段（正常操作）",
    description:
      "预估该通道最后一条采样覆盖到的时刻（末条时间戳加一个采样周期）。若必需通道在片段结束前已停止，记录剩余等待时长，供转换时确定共同终点。",
  },
  QC_NO_COMMON_WINDOW: {
    title: "没有共同有效区间",
    description:
      "取各必需通道有效起点中的最晚时刻、有效终点中的最早时刻。在这个左闭右开区间内找不到目标频率网格上的一帧，就提示各通道没有可共同截取的采样帧。",
  },
  QC_COVERAGE_LOW: {
    title: "数据覆盖不足",
    description: "在通道从首条到末条采样覆盖的有效时段内，用不同时间戳的个数除以按基准频率预计的帧数，最高记为 100%。当前采集方案把覆盖率记录为指标，单独降低覆盖率不会产生此项告警。",
  },
  QC_IMAGE_BLACK: {
    title: "黑帧比例超标",
    description: "按相机统计。帧的平均亮度小于或等于亮度界限时计为黑帧。黑帧数除以送检帧数得到黑帧比例，严格高于对应比例阈值时提示或拒绝。",
  },
  QC_IMAGE_REPEATED: {
    title: "重复帧比例超标",
    description: "按相机比较相邻图像帧的内容指纹。两帧指纹相同就计 1 对重复，重复比例为重复对数除以相邻帧对数。连续 3 张相同图像计 2 对；比例超过阈值时触发。",
  },
  QC_IMAGE_CORRUPT: {
    title: "图像损坏",
    description: "对每个相机，用解码检查标记为损坏的帧数除以送检帧数。比例严格大于阈值时触发。若拒绝阈值为 0%，只要探测到 1 张损坏帧就拒绝。",
  },
  QC_JOINT_OUT_OF_RANGE: {
    title: "关节值超出范围",
    description: "逐帧检查方案已设置上下限的关节。关节值低于下限或高于上限即越界，正好等于边界仍通过。结果按方案中的“关节超出范围时拒绝”设置判为拒绝或提示。",
  },
  QC_ACTION_MISSING: {
    title: "动作信号缺失",
    description: "统计检查片段内读到的动作观测条数。少于最少条数才触发。例如最少条数为 1 时，0 条触发、1 条通过。此项统计总数，片段中间的缺帧另行检查。",
  },
  QC_ACTION_JUMP: {
    title: "动作信号突变",
    description: "比较相邻两条动作向量。维数不同时按维数设置提示或拒绝；维数相同时，对两帧都有效的维度计算差值平方和的平方根。结果超过跳变阈值时触发，未设置数值阈值时只检查维数。",
  },
  QC_POINT_CLOUD_EMPTY: {
    title: "点云为空",
    description: "按点云通道逐帧统计。点数为 0 的帧数除以该通道送检点云帧数，就是空点云比例。比例超过提示或拒绝阈值时触发。",
  },
  QC_POINT_COUNT_ABNORMAL: {
    title: "点云点数异常",
    description: "按通道检查每一帧的点数。少于最少点数或多于最多点数的帧计为异常，异常帧数除以送检帧数再与比例阈值比较。未设置的上下界不参与比较，两项均为空时跳过此检查。",
  },
  QC_MODALITY_OFFSET: {
    title: "传感器时间不同步",
    description: "分别计算各通道采样时刻与对齐参考时刻之间的绝对时间差，按从小到大排序，取第 95 百分位（P95）。该值严格大于配置的毫秒阈值时触发；没有偏差观测的通道跳过。",
  },
  QC_COMPLETE_STEP_RATIO_LOW: {
    title: "完整采样比例不足",
    description: "用完整采样步数除以参与检查的总步数，与方案的最小比例比较。录制提供逐行有效性时使用有效行占比。例如 100 步中有 95 步完整，比例为 95%；低于对应阈值时提示或拒绝。",
  },
} satisfies Record<
  components["schemas"]["QualityCode"],
  { title: string; description: string }
>;

export function isQualityCode(code: unknown): code is keyof typeof qualityFindings {
  return typeof code === "string" && Object.hasOwn(qualityFindings, code);
}

const findingTextByCode = {
  ...qualityFindings,
  MCAP_DUPLICATE_RECORDING: {
    title: "重复录制",
    description: "原始 MCAP 内容与已有录制完全相同，已拦截重复入库。",
  },
  MCAP_INTERIOR_ACTION_MISSING: {
    title: "中间帧缺动作",
    description: "两个实录动作之间存在缺失帧，校验失败。",
  },
  CAPTURE_INTERRUPTION_TOO_LONG: {
    title: "连续无效采集行超过拒绝阈值",
    description: "连续无效的采集行数超过了方案设置的拒绝阈值。",
  },
  CAPTURE_VALID_RATIO_LOW: {
    title: "有效采集行比例过低",
    description: "有效采集行数占片段总行数的比例低于方案阈值。",
  },
  QUALITY_REJECT: {
    title: "质检不通过",
    description: "自动检查存在失败项。",
  },
};

export function qualityFindingText(code: string): {
  readonly title: string;
  readonly description: string;
} {
  return Object.hasOwn(findingTextByCode, code)
    ? findingTextByCode[code as keyof typeof findingTextByCode]
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
