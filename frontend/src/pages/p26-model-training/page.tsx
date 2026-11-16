import { useState } from "react";
import {
  Button,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Table,
  Tag,
} from "antd";
import { Plus } from "lucide-react";
import { StandardPageScaffold } from "../../shared/ui/layout";
import layout from "../../shared/ui/workspace.module.css";

interface TrainingConfig {
  name: string;
  policy: string;
  dataset: string;
  steps: number;
  batchSize: number;
}

interface Run extends TrainingConfig {
  id: number;
  status: "训练中" | "已完成" | "草稿";
  completed: number;
}

const datasets = ["双臂抓取 · v3", "工件分拣 · v2"];
const policies = ["ACT", "Diffusion Policy"];
const examples: Run[] = [
  {
    id: 1,
    name: "双臂抓取基线",
    policy: "ACT",
    dataset: datasets[0]!,
    steps: 20000,
    batchSize: 16,
    completed: 7200,
    status: "训练中",
  },
  {
    id: 2,
    name: "工件分拣对照实验",
    policy: "Diffusion Policy",
    dataset: datasets[1]!,
    steps: 10000,
    batchSize: 8,
    completed: 10000,
    status: "已完成",
  },
];

function RunStatus({ status }: { status: Run["status"] }) {
  const colors = { 训练中: "processing", 已完成: "success", 草稿: "default" };
  return <Tag color={colors[status]}>{status}</Tag>;
}

export function Component() {
  const [runs, setRuns] = useState(examples);
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState<Run | null>(null);
  const [form] = Form.useForm<TrainingConfig>();

  function addDraft(values: TrainingConfig) {
    setRuns((current) => [
      {
        ...values,
        name: values.name.trim(),
        id: Math.max(...current.map((run) => run.id)) + 1,
        completed: 0,
        status: "草稿",
      },
      ...current,
    ]);
    setCreating(false);
  }

  return (
    <div className={layout.page}>
      <StandardPageScaffold
        header={{
          title: "模型训练",
          description:
            "查看示例训练进度，或配置训练草稿。草稿仅在当前页面保留。",
          actions: (
            <Button
              type="primary"
              icon={<Plus size={16} />}
              onClick={() => setCreating(true)}
            >
              新建训练草稿
            </Button>
          ),
        }}
      >
        <Table<Run>
          rowKey="id"
          dataSource={runs}
          scroll={{ x: 710 }}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          columns={[
            {
              title: "任务名称",
              dataIndex: "name",
              render: (name: string, run) => (
                <Button type="link" onClick={() => setDetail(run)}>
                  {name}
                </Button>
              ),
            },
            { title: "策略模型", dataIndex: "policy" },
            { title: "数据集版本", dataIndex: "dataset" },
            {
              title: "状态",
              dataIndex: "status",
              render: (status: Run["status"]) => <RunStatus status={status} />,
            },
            {
              title: "训练进度",
              width: 140,
              render: (_, run) =>
                run.status === "草稿" ? (
                  "待提交"
                ) : (
                  <Progress
                    size="small"
                    percent={Math.round((100 * run.completed) / run.steps)}
                  />
                ),
            },
          ]}
        />
      </StandardPageScaffold>
      <Modal
        title="新建训练草稿"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => form.submit()}
        okText="添加草稿"
        cancelText="取消"
        destroyOnHidden
      >
        <Form<TrainingConfig>
          form={form}
          layout="vertical"
          preserve={false}
          initialValues={{
            policy: policies[0],
            dataset: datasets[0],
            steps: 20000,
            batchSize: 16,
          }}
          onFinish={addDraft}
        >
          <Form.Item
            label="任务名称"
            name="name"
            rules={[
              { required: true, whitespace: true, message: "请输入任务名称" },
            ]}
          >
            <Input placeholder="例如：双臂抓取实验" maxLength={60} />
          </Form.Item>
          <Form.Item label="策略模型" name="policy">
            <Select options={policies.map((value) => ({ value }))} />
          </Form.Item>
          <Form.Item label="示例数据集版本" name="dataset">
            <Select options={datasets.map((value) => ({ value }))} />
          </Form.Item>
          <Form.Item
            label="训练步数"
            name="steps"
            rules={[
              {
                type: "integer",
                min: 1,
                required: true,
                message: "请输入正整数",
              },
            ]}
          >
            <InputNumber min={1} precision={0} />
          </Form.Item>
          <Form.Item
            label="批大小"
            name="batchSize"
            rules={[
              {
                type: "integer",
                min: 1,
                required: true,
                message: "请输入正整数",
              },
            ]}
          >
            <InputNumber min={1} precision={0} />
          </Form.Item>
        </Form>
      </Modal>
      <Drawer
        title="训练任务详情"
        open={detail !== null}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <Descriptions
            bordered
            column={1}
            size="small"
            items={[
              { key: "name", label: "任务名称", children: detail.name },
              {
                key: "status",
                label: "状态",
                children: <RunStatus status={detail.status} />,
              },
              { key: "policy", label: "策略模型", children: detail.policy },
              { key: "dataset", label: "数据集版本", children: detail.dataset },
              { key: "batch", label: "批大小", children: detail.batchSize },
              {
                key: "steps",
                label: "已完成 / 总步数",
                children: `${detail.completed.toLocaleString()} / ${detail.steps.toLocaleString()}`,
              },
            ]}
          />
        )}
      </Drawer>
    </div>
  );
}
