import type { StreamDescriptor } from "./types";

// Canonical action channels in recordings, normalized Episodes and older ROS captures.
const primaryActionPaths = [
  "action", "humanoid/action", "action/joint_states", "io_teleop/joint_cmd",
];

export function selectRobotActionTopic(topics: readonly string[]): string | null {
  for (const path of primaryActionPaths) {
    const match = topics.find((topic) => topic === path || topic === `/${path}`);
    if (match) return match;
  }
  return null;
}

export function selectRobotActionStream(streams: readonly StreamDescriptor[]): StreamDescriptor | null {
  // Older snapshots can mark a joint command as joint_state based on its topic name.
  const candidates = streams.filter((stream) => stream.windowSource &&
    (stream.modality === "action" || stream.modality === "joint_state"));
  const topic = selectRobotActionTopic(candidates.map((stream) => stream.canonicalPath));
  return candidates.find((stream) => stream.canonicalPath === topic) ?? null;
}
