import { Alert, Button, Flex, Form, Input, Modal } from "antd";
import { useEffect } from "react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";
import { createZodResolver, RHFInput } from "../../../shared/ui";
import styles from "../styles.module.css";

const createDatasetSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, "请输入名称")
      .max(256, "名称最多 256 个字符"),
    folderPath: z.string(),
    description: z.string().max(4096, "描述最多 4096 个字符"),
    labels: z.string(),
  })
  .superRefine((value, context) => {
    const labels = normalizeLabels(value.labels);
    if (labels.length > 64) {
      context.addIssue({
        code: "custom",
        path: ["labels"],
        message: "标签最多 64 个",
      });
    }
    if (labels.some((label) => label.length > 96)) {
      context.addIssue({
        code: "custom",
        path: ["labels"],
        message: "每个标签最多 96 个字符",
      });
    }
    const folderPath = normalizeFolderPath(value.folderPath);
    if (value.folderPath.includes("\\")) {
      context.addIssue({
        code: "custom",
        path: ["folderPath"],
        message: "目录请使用 / 分隔",
      });
    } else if (folderPath.some((segment) => segment.length === 0)) {
      context.addIssue({
        code: "custom",
        path: ["folderPath"],
        message: "目录层级不能为空",
      });
    } else if (folderPath.length > 16) {
      context.addIssue({
        code: "custom",
        path: ["folderPath"],
        message: "目录最多 16 层",
      });
    } else if (folderPath.some((segment) => segment.length > 128)) {
      context.addIssue({
        code: "custom",
        path: ["folderPath"],
        message: "每层目录最多 128 个字符",
      });
    }
  });

type CreateDatasetValues = z.infer<typeof createDatasetSchema>;

export interface CreateDatasetDraft {
  readonly name: string;
  readonly folderPath: readonly string[];
  readonly description: string;
  readonly labels: readonly string[];
}

const defaults: CreateDatasetValues = {
  name: "",
  folderPath: "",
  description: "",
  labels: "",
};

function normalizeFolderPath(value: string): string[] {
  return value.trim() ? value.split("/").map((segment) => segment.trim()) : [];
}

function normalizeLabels(value: string): string[] {
  return [
    ...new Set(
      value
        .split(",")
        .map((label) => label.trim())
        .filter(Boolean),
    ),
  ];
}

export function CreateDatasetDialog({
  open,
  pending,
  errorMessage,
  onSubmit,
  onCancel,
}: Readonly<{
  open: boolean;
  pending: boolean;
  errorMessage?: string | null;
  onSubmit: (draft: CreateDatasetDraft) => void;
  onCancel: () => void;
}>) {
  const form = useForm<CreateDatasetValues>({
    defaultValues: defaults,
    mode: "onChange",
    resolver: createZodResolver(createDatasetSchema),
  });

  useEffect(() => {
    if (open) form.reset(defaults);
  }, [form, open]);

  const close = () => {
    if (pending) return;
    form.reset(defaults);
    onCancel();
  };

  return (
    <Modal
      open={open}
      title="创建数据集"
      footer={null}
      onCancel={close}
      closable={!pending}
      keyboard={!pending}
      mask={{ closable: false }}
      destroyOnHidden
      width={640}
      afterOpenChange={(nextOpen) => {
        if (nextOpen) form.setFocus("name");
      }}
    >
      <form
        className={styles.createForm}
        noValidate
        onSubmit={(event) => {
          void form.handleSubmit((value) =>
            onSubmit({
              name: value.name,
              folderPath: normalizeFolderPath(value.folderPath),
              description: value.description,
              labels: normalizeLabels(value.labels),
            }),
          )(event);
        }}
      >
        {errorMessage ? (
          <Alert type="error" showIcon title={errorMessage} />
        ) : null}
        <RHFInput
          control={form.control}
          name="name"
          label="名称"
          disabled={pending}
          autoComplete="off"
        />
        <RHFInput
          control={form.control}
          name="folderPath"
          label="目录路径（可选）"
          description="像文件夹一样用 / 分层，例如：机器人/G1/抓取。"
          disabled={pending}
          autoComplete="off"
          placeholder="机器人/G1/抓取"
        />
        <Controller
          control={form.control}
          name="description"
          render={({ field, fieldState }) => (
            <Form.Item
              label="描述"
              htmlFor="create-dataset-description"
              help={fieldState.error?.message}
              validateStatus={fieldState.error ? "error" : undefined}
            >
              <Input.TextArea
                {...field}
                id="create-dataset-description"
                rows={5}
                maxLength={4096}
                disabled={pending}
                status={fieldState.error ? "error" : undefined}
              />
            </Form.Item>
          )}
        />
        <RHFInput
          control={form.control}
          name="labels"
          label="标签（逗号分隔）"
          description="自动去重；最多 64 个，每个最多 96 个字符。"
          disabled={pending}
          autoComplete="off"
        />
        <Flex justify="end" gap="small" wrap="wrap">
          <Button disabled={pending} onClick={close}>
            取消
          </Button>
          <Button
            type="primary"
            htmlType="submit"
            loading={pending}
            disabled={pending || !form.formState.isValid}
          >
            创建
          </Button>
        </Flex>
      </form>
    </Modal>
  );
}
