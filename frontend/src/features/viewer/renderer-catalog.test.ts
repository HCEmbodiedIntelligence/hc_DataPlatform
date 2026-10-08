import { describe, expect, it, vi } from "vitest";
import { builtinViewerRenderers } from "./builtin-renderers";
import { createViewerRendererCatalog } from "./renderer-catalog";
import type { ViewerRendererRegistration } from "./renderer-contract";
import { resolveViewerComposition } from "./ViewerCompositionResolver";
import type { StreamDescriptor } from "./types";

export const textStream: StreamDescriptor = {
  id: "transcript",
  canonicalPath: "/transcript",
  displayName: "字幕",
  modality: "text",
  schema: { id: "test.caption.events", version: "1" },
  availability: "ready",
  startNs: "0",
  endNs: "1000000000",
  windowSource: {
    loadWindow: async () => ({ generation: 1, timestampsNs: [], events: [] }),
  },
};

function renderer(
  overrides: Partial<ViewerRendererRegistration> = {},
): ViewerRendererRegistration {
  return {
    descriptor: {
      id: "captions",
      version: "1",
      modalities: ["text"],
      schemas: [{ id: "test.caption.events", versions: ["1"] }],
      requiredInputs: ["window"],
      priority: 60,
      minWidthPx: 420,
      resourceScope: "curve",
    },
    default: true,
    implementation: { kind: "lazy", load: vi.fn() },
    ...overrides,
  };
}

describe("viewer-local renderer catalog", () => {
  it("routes a new authorized-media modality without authorizing it during selection", () => {
    const authorize = vi.fn(async () => ({
      url: "/audio",
      expiresAt: "2099-01-01T00:00:00Z",
      kind: "audio",
    }));
    const base = renderer();
    const catalog = createViewerRendererCatalog([
      renderer({
        descriptor: {
          ...base.descriptor,
          id: "audio-waveform",
          modalities: ["audio"],
          requiredInputs: ["media"],
          schemas: [{ id: "test.audio", versions: ["1"] }],
        },
      }),
    ]);
    const stream: StreamDescriptor = {
      ...textStream,
      modality: "audio",
      schema: { id: "test.audio", version: "1" },
      mediaSource: { authorize, refresh: authorize },
      windowSource: undefined,
    };
    expect(catalog.select(stream).registration?.descriptor.id).toBe(
      "audio-waveform",
    );
    expect(authorize).not.toHaveBeenCalled();
    expect(catalog.select({ ...stream, mediaSource: undefined }).reason).toBe(
      "missing-input",
    );
  });

  it("routes a new modality without loading its code or modifying the resolver", () => {
    const registration = renderer();
    const catalog = createViewerRendererCatalog([
      ...builtinViewerRenderers,
      registration,
    ]);
    expect(
      resolveViewerComposition([textStream], catalog).panels[0],
    ).toMatchObject({
      kind: "extension",
      renderer: { id: "captions", version: "1" },
      state: "ready",
    });
    if (registration.implementation.kind === "lazy")
      expect(registration.implementation.load).not.toHaveBeenCalled();
  });

  it("keeps unknown modalities and unsupported schema versions explicit", () => {
    const catalog = createViewerRendererCatalog([renderer()]);
    for (const stream of [
      { ...textStream, modality: "audio" },
      { ...textStream, schema: { ...textStream.schema, version: "2" } },
      { ...textStream, schema: { id: "other", version: "1" } },
    ]) {
      expect(
        resolveViewerComposition([stream], catalog).panels[0],
      ).toMatchObject({ kind: "unsupported", state: "unsupported" });
    }
  });

  it("requires declared input capabilities and degrades only the affected panel", () => {
    const catalog = createViewerRendererCatalog([renderer()]);
    const composition = resolveViewerComposition(
      [{ ...textStream, windowSource: undefined }],
      catalog,
    );
    expect(composition.panels[0]?.state).toBe("unsupported");
    expect(composition.diagnostics[0]?.code).toBe("RENDERER_INPUT_UNAVAILABLE");
  });

  it("retains exact old versions but selects only an explicit default", () => {
    const old = renderer({ default: false });
    const current = renderer({
      descriptor: { ...old.descriptor, version: "2" },
    });
    const catalog = createViewerRendererCatalog([old, current]);
    expect(catalog.select(textStream).registration?.descriptor.version).toBe(
      "2",
    );
    expect(
      catalog.get({ id: "captions", version: "1" })?.descriptor.version,
    ).toBe("1");
    expect(catalog.get({ id: "captions", version: "absent" })).toBeUndefined();
    expect(createViewerRendererCatalog([old]).select(textStream).reason).toBe(
      "unsupported",
    );
  });

  it("rejects duplicate references and multiple defaults instead of replacing a registration", () => {
    expect(() => createViewerRendererCatalog([renderer(), renderer()])).toThrow(
      "Duplicate renderer",
    );
    const current = renderer();
    expect(() =>
      createViewerRendererCatalog([
        current,
        renderer({ descriptor: { ...current.descriptor, version: "2" } }),
      ]),
    ).toThrow("Duplicate default");
  });

  it("selects the best available priority and refuses equal matches", () => {
    const caption = renderer();
    const alternative = renderer({
      descriptor: { ...caption.descriptor, id: "alternate", priority: 50 },
    });
    expect(
      createViewerRendererCatalog([caption, alternative]).select(textStream)
        .registration?.descriptor.id,
    ).toBe("alternate");
    const ambiguous = renderer({
      descriptor: { ...alternative.descriptor, priority: 60 },
    });
    const composition = resolveViewerComposition(
      [textStream],
      createViewerRendererCatalog([caption, ambiguous]),
    );
    expect(composition.panels[0]?.state).toBe("unsupported");
    expect(composition.diagnostics[0]?.code).toBe(
      "RENDERER_SELECTION_AMBIGUOUS",
    );
  });

  it("freezes capabilities so callers cannot change routing after assembly", () => {
    const modalities = ["text"];
    const versions = ["1"];
    const current = renderer();
    const catalog = createViewerRendererCatalog([
      renderer({
        descriptor: {
          ...current.descriptor,
          modalities,
          schemas: [{ id: "test.caption.events", versions }],
        },
      }),
    ]);
    modalities[0] = "audio";
    versions[0] = "2";
    expect(catalog.select(textStream).registration?.descriptor.id).toBe(
      "captions",
    );
    expect(
      Object.isFrozen(
        catalog.get({ id: "captions", version: "1" })?.descriptor,
      ),
    ).toBe(true);
  });

  it.each(["media-preparing", "partial", "missing", "unsupported"] as const)(
    "retains stream availability %s",
    (availability) => {
      const state = resolveViewerComposition(
        [{ ...textStream, availability }],
        createViewerRendererCatalog([renderer()]),
      ).panels[0]?.state;
      expect(state).toBe(
        availability === "media-preparing" ? "pending" : availability,
      );
    },
  );
});
