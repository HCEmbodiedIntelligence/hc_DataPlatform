import { Alert, Button, Modal, Spin } from "antd";
import { useState } from "react";
import { captureApi } from "./api";
import type { SchemeDetail, SchemeDocument } from "./contracts";
import { Differences } from "./EvidenceView";
import { captureError, structureDiff } from "./presentation";

export function CompareServerDraft({
  organizationId,
  schemeId,
  current,
  onLoad,
}: {
  organizationId: string;
  schemeId: string;
  current: SchemeDocument;
  onLoad: (result: SchemeDetail) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [server, setServer] = useState<SchemeDetail>();
  const [error, setError] = useState("");
  async function compare() {
    setOpen(true);
    setLoading(true);
    setServer(undefined);
    setError("");
    try {
      setServer(await captureApi.detail(organizationId, schemeId));
    } catch (cause) {
      setError(captureError(cause));
    } finally {
      setLoading(false);
    }
  }
  const differences = server ? structureDiff(server.draft, current) : [];
  return (
    <>
      <Button size="small" loading={loading} onClick={compare}>
        核对服务器草稿
      </Button>
      <Modal
        open={open}
        title="核对服务器草稿"
        width={720}
        cancelText="保留本页输入"
        okText="载入服务器草稿"
        okButtonProps={{ disabled: !server }}
        onCancel={() => setOpen(false)}
        onOk={() => {
          if (server) {
            setOpen(false);
            onLoad(server);
          }
        }}
      >
        {loading && <Spin aria-label="正在读取服务器草稿" />}
        {error && (
          <Alert
            type="error"
            title="读取草稿失败"
            description={error}
            action={<Button onClick={compare}>重试</Button>}
          />
        )}
        {server &&
          (differences.length ? (
            <>
              <p>
                左栏是服务器草稿，右栏是本页输入。载入服务器草稿会替换本页输入。
              </p>
              <Differences items={differences} />
            </>
          ) : (
            <p>本页内容与服务器草稿一致。</p>
          ))}
      </Modal>
    </>
  );
}
