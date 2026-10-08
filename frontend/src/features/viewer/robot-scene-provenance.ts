/** Presentation evidence supplied by the data adapter; current binding is a preview. */
export type RobotSceneProvenance = {
  readonly kind: "current-binding-compatibility";
  readonly title: string;
} & (
  | { readonly availability: "available" }
  | { readonly availability: "unavailable"; readonly reason: string }
);

/** Compatibility for old callers with no frozen capture context. */
export const currentBindingPreview: RobotSceneProvenance = Object.freeze({
  kind: "current-binding-compatibility",
  availability: "available",
  title: "当前绑定的机器人模型（未冻结采集时模型）",
});
