import type {
  ViewerInputCapability,
  ViewerRendererRef,
  ViewerRendererRegistration,
} from "./renderer-contract";
import type { StreamDescriptor } from "./types";

export type ViewerRendererSelection =
  | {
      readonly registration: ViewerRendererRegistration;
      readonly reason?: never;
    }
  | {
      readonly registration?: never;
      readonly reason: "unsupported" | "missing-input" | "ambiguous";
    };

export interface ViewerRendererCatalog {
  get(ref: ViewerRendererRef): ViewerRendererRegistration | undefined;
  select(stream: StreamDescriptor): ViewerRendererSelection;
}

const identifier = /^[a-z][a-z0-9_.-]{0,127}$/u;
const inputCapabilities: readonly ViewerInputCapability[] = ["media", "window"];
const builtinKinds = new Set([
  "video",
  "depth",
  "pointcloud-preview",
  "time-series",
  "event-track",
  "unsupported",
]);

function key(ref: ViewerRendererRef): string {
  return JSON.stringify([ref.id, ref.version]);
}

function requireStrings(values: readonly string[], label: string): void {
  if (
    values.length === 0 ||
    new Set(values).size !== values.length ||
    values.some(
      (value) =>
        typeof value !== "string" || !value.trim() || value.length > 128,
    )
  )
    throw new Error(`Invalid renderer ${label}`);
}

function freezeRegistration(
  value: ViewerRendererRegistration,
): ViewerRendererRegistration {
  const descriptor = value.descriptor;
  if (
    !identifier.test(descriptor.id) ||
    typeof descriptor.version !== "string" ||
    !descriptor.version.trim() ||
    descriptor.version.length > 128
  ) {
    throw new Error("Invalid renderer identity/version");
  }
  requireStrings(descriptor.modalities, "modalities");
  if (descriptor.schemas !== "*") {
    requireStrings(
      descriptor.schemas.map((schema) => schema.id),
      "schemas",
    );
    for (const schema of descriptor.schemas) {
      if (schema.versions !== "*")
        requireStrings(schema.versions, "schema versions");
    }
  }
  if (
    !Number.isFinite(descriptor.priority) ||
    !Number.isFinite(descriptor.minWidthPx) ||
    descriptor.minWidthPx <= 0 ||
    typeof value.default !== "boolean" ||
    new Set(descriptor.requiredInputs).size !==
      descriptor.requiredInputs.length ||
    descriptor.requiredInputs.some(
      (input) => !inputCapabilities.includes(input),
    )
  ) {
    throw new Error("Invalid renderer layout or required inputs");
  }
  if (value.implementation.kind === "builtin") {
    if (
      !builtinKinds.has(value.implementation.panelKind) ||
      (descriptor.jointAxes && value.implementation.panelKind !== "time-series")
    ) {
      throw new Error("Invalid builtin renderer");
    }
  } else if (
    value.implementation.kind !== "lazy" ||
    typeof value.implementation.load !== "function" ||
    descriptor.jointAxes
  ) {
    throw new Error("Invalid lazy renderer");
  }
  return Object.freeze({
    descriptor: Object.freeze({
      ...descriptor,
      modalities: Object.freeze([...descriptor.modalities]),
      requiredInputs: Object.freeze([...descriptor.requiredInputs]),
      schemas:
        descriptor.schemas === "*"
          ? "*"
          : Object.freeze(
              descriptor.schemas.map((schema) =>
                Object.freeze({
                  id: schema.id,
                  versions:
                    schema.versions === "*"
                      ? "*"
                      : Object.freeze([...schema.versions]),
                }),
              ),
            ),
    }),
    default: value.default,
    implementation: Object.freeze({ ...value.implementation }),
  });
}

/** Immutable viewer-local assembly; selection never loads code or reads global UI state. */
export function createViewerRendererCatalog(
  registrations: readonly ViewerRendererRegistration[],
): ViewerRendererCatalog {
  const byRef = new Map<string, ViewerRendererRegistration>();
  const defaults = new Map<string, ViewerRendererRegistration>();
  for (const value of registrations) {
    const registration = freezeRegistration(value);
    const ref = key(registration.descriptor);
    if (byRef.has(ref)) throw new Error(`Duplicate renderer ${ref}`);
    byRef.set(ref, registration);
    if (registration.default) {
      if (defaults.has(registration.descriptor.id)) {
        throw new Error(
          `Duplicate default renderer ${registration.descriptor.id}`,
        );
      }
      defaults.set(registration.descriptor.id, registration);
    }
  }
  const candidates = [...defaults.values()];
  return Object.freeze({
    get: (ref: ViewerRendererRef) => byRef.get(key(ref)),
    select(stream: StreamDescriptor): ViewerRendererSelection {
      const matching = candidates.filter(
        ({ descriptor }) =>
          descriptor.modalities.includes(stream.modality) &&
          (descriptor.schemas === "*" ||
            descriptor.schemas.some(
              (schema) =>
                schema.id === stream.schema.id &&
                (schema.versions === "*" ||
                  schema.versions.includes(stream.schema.version)),
            )),
      );
      const available = matching
        .filter(({ descriptor }) =>
          descriptor.requiredInputs.every((input) =>
            input === "media"
              ? Boolean(stream.mediaSource)
              : Boolean(stream.windowSource),
          ),
        )
        .sort((a, b) => a.descriptor.priority - b.descriptor.priority);
      const selected = available[0];
      if (!selected)
        return { reason: matching.length ? "missing-input" : "unsupported" };
      if (available[1]?.descriptor.priority === selected.descriptor.priority) {
        return { reason: "ambiguous" };
      }
      return { registration: selected };
    },
  });
}
