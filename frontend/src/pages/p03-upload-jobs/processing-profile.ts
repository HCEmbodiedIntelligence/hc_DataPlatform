/** Local affordance only; the server verifies the complete source before processing. */
export function supportsLeRobotProcessing(
  info: Readonly<Record<string, unknown>>,
): boolean {
  if (info.robot_type === "unitree_g1") return true;
  if (!info.features || typeof info.features !== "object") return false;
  const features = info.features as Record<string, unknown>;
  return ["observation.state", "action"].every((key) => {
    const value = features[key];
    if (!value || typeof value !== "object") return false;
    const feature = value as Record<string, unknown>;
    return (
      feature.dtype === "float32" &&
      Array.isArray(feature.names) &&
      Array.isArray(feature.shape) &&
      feature.shape.length === 1 &&
      Number.isInteger(feature.shape[0]) &&
      feature.shape[0] > 0 &&
      feature.names.length === feature.shape[0] &&
      feature.names.every(
        (name) => typeof name === "string" && name.trim().length > 0,
      ) &&
      new Set(feature.names).size === feature.names.length
    );
  });
}
