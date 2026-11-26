import { PackageCheck } from "lucide-react";
import { createContext, useContext, useId, useState, type ReactNode } from "react";
import { Alert, Button, Radio, Space, Typography } from "antd";
import type { ExportDestination } from "./oss-export";

const Destination = createContext<
  readonly [ExportDestination, (value: ExportDestination) => void] | null
>(null);

export function ExportDestinationProvider({
  children,
}: {
  children: ReactNode;
}) {
  const selection = useState<ExportDestination>("zip");
  return (
    <Destination.Provider value={selection}>{children}</Destination.Provider>
  );
}

export function useExportDestination() {
  const local = useState<ExportDestination>("zip");
  return useContext(Destination) ?? local;
}

export function ExportControls({
  selection,
  disabled,
  busy,
  canPublish,
  onExport,
}: {
  selection: ReturnType<typeof useExportDestination>;
  disabled: boolean;
  busy: boolean;
  canPublish: boolean;
  onExport: (destination: ExportDestination) => void;
}) {
  const [destination, setDestination] = selection;
  const destinationGroupName = useId();
  return (
    <Space wrap>
      <span>导出方式</span>
      <Radio.Group
        name={destinationGroupName}
        aria-label="导出方式"
        value={destination}
        disabled={busy}
        onChange={(event) => setDestination(event.target.value)}
      >
        <Radio.Button value="zip">下载 ZIP</Radio.Button>
        <Radio.Button value="oss" disabled={!canPublish}>
          导出到 OSS
        </Radio.Button>
      </Radio.Group>
      <Button
        type="primary"
        icon={<PackageCheck size={16} aria-hidden="true" />}
        aria-label="导出"
        loading={busy}
        disabled={disabled || busy || (destination === "oss" && !canPublish)}
        onClick={() => onExport(destination)}
      >
        导出
      </Button>
    </Space>
  );
}

export function OssExportResult({ uri, onClose }: { uri: string; onClose: () => void }) {
  return (
    <Alert
      type="success"
      showIcon
      title="已导出到 OSS"
      closable={{ onClose, "aria-label": "关闭导出提示" }}
      description={
        <div title={uri} style={{ overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
          {uri}
        </div>
      }
    />
  );
}
