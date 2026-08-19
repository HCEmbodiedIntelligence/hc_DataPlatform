import { useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import type { StorageInventoryFact } from "../../../entities/storage-inventory";
import { int64String } from "../../../shared/lib/bigint-string";
import { StorageObjectDrawer } from "../components/StorageObjectDrawer";

const visualObject: StorageInventoryFact = {
  objectId:
    "object-fe13-with-a-deliberately-long-auditable-identifier-that-must-remain-contained",
  snapshotId: "snapshot-fe13-20260818",
  objectRole: "SOURCE",
  wireObjectRole: "SOURCE",
  displayKey:
    "raw/project-fe13/production-cell-with-an-extremely-long-readable-name/rollout/recording.mcap",
  masked: true,
  physicalBytes: int64String("68719476736"),
  storageClass: "STANDARD",
  wireStorageClass: "STANDARD",
  status: "AVAILABLE",
  referenceCount: int64String("3"),
  createdAt: "2026-08-18T00:00:00Z",
  lastAccessedAt: null,
  protectionReasons: ["MANIFEST_REFERENCE", "PUBLISHED_MANIFEST_REFERENCE"],
  allowedActions: [],
  readOnly: true,
};

function StorageObjectDrawerVisualFixture() {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeDrawer = () => {
    setOpen(false);
    window.setTimeout(() => triggerRef.current?.focus(), 0);
  };
  return (
    <section aria-label="对象详情抽屉视觉测试">
      <button ref={triggerRef} type="button" onClick={() => setOpen(true)}>
        查看超长对象详情
      </button>
      <StorageObjectDrawer
        open={open}
        state="ready"
        object={visualObject}
        returnFocusRef={triggerRef}
        onClose={closeDrawer}
      />
    </section>
  );
}

const mountedRoots = new WeakMap<HTMLElement, Root>();

export function mountStorageObjectDrawerVisualFixture(host: HTMLElement): void {
  mountedRoots.get(host)?.unmount();
  const root = createRoot(host);
  mountedRoots.set(host, root);
  root.render(<StorageObjectDrawerVisualFixture />);
}
