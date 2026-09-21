import { Modal } from "antd";
import type { ModalProps } from "antd";

/** Shared shell for original-video previews opened from any list or drawer. */
export function VideoPreviewModal(
  props: Pick<ModalProps, "open" | "title" | "onCancel" | "children">,
) {
  return (
    <Modal
      {...props}
      width="min(800px, calc(100vw - 32px), max(560px, calc(100dvh - 200px)))"
      style={{ top: 24 }}
      styles={{
        body: { maxHeight: "calc(100dvh - 136px)", overflowY: "auto" },
      }}
      footer={null}
      destroyOnHidden
    />
  );
}
