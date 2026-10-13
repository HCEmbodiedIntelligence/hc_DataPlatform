import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
} from "antd";
import { useScopedQuery } from "../../shared/api/use-scoped-query";
import { useScopedMutation } from "../../shared/api/use-scoped-mutation";
import { request } from "../../shared/api/http-client";

type Configuration = {
  format?: string;
  root_frames: string[];
  cameras: Record<string, unknown>[];
  static_transforms: Record<string, unknown>[];
};
type State = {
  organization_id: string;
  robot_id: string;
  etag: string;
  configuration: Configuration | null;
};
const required = [{ required: true, message: "请填写此项" }];
const models = ["none", "plumb_bob", "rational_polynomial", "equidistant"];

function StreamFields({
  camera,
  kind,
}: {
  camera: number;
  kind: "rgb" | "depth";
}) {
  const form = Form.useFormInstance();
  const stream = Form.useWatch(["cameras", camera, kind], {
    form,
    preserve: true,
  });
  const name = [camera, kind];
  return (
    <Card
      size="small"
      title={
        <Checkbox
          checked={stream != null}
          onChange={(e) =>
            form.setFieldValue(
              ["cameras", camera, kind],
              e.target.checked
                ? {
                    intrinsics: {
                      distortion_model: "none",
                      distortion_coefficients: [],
                    },
                  }
                : null,
            )
          }
        >
          {kind === "rgb" ? "RGB 内参" : "深度内参"}
        </Checkbox>
      }
    >
      {stream != null && (
        <>
          <Form.Item
            name={[...name, "feature_key"]}
            label="图像特征名或源 topic"
            rules={required}
          >
            <Input placeholder="observation.images.camera_left 或源 topic" />
          </Form.Item>
          <Form.Item
            name={[...name, "optical_frame"]}
            label="光学坐标系"
            rules={required}
          >
            <Input placeholder="camera_left_rgb_optical_frame" />
          </Form.Item>
          <Space wrap align="start">
            {(["width", "height", "fx", "fy", "cx", "cy", "skew"] as const).map(
              (key) => (
                <Form.Item
                  key={key}
                  name={[...name, "intrinsics", key]}
                  label={
                    {
                      width: "宽（像素）",
                      height: "高（像素）",
                      fx: "fx（像素）",
                      fy: "fy（像素）",
                      cx: "cx（像素）",
                      cy: "cy（像素）",
                      skew: "skew（默认 0）",
                    }[key]
                  }
                  rules={key === "skew" ? [] : required}
                >
                  <InputNumber
                    min={
                      ["width", "height", "fx", "fy"].includes(key)
                        ? 0.000001
                        : undefined
                    }
                    precision={
                      key === "width" || key === "height" ? 0 : undefined
                    }
                  />
                </Form.Item>
              ),
            )}
          </Space>
          <Form.Item
            name={[...name, "intrinsics", "distortion_model"]}
            label="畸变模型"
            rules={required}
          >
            <Select
              options={models.map((value) => ({
                value,
                label: value === "none" ? "无畸变（已校正图像）" : value,
              }))}
            />
          </Form.Item>
          <Form.Item
            name={[...name, "intrinsics", "distortion_coefficients"]}
            label="畸变系数（逗号分隔；无畸变留空）"
            getValueProps={(value) => ({
              value: Array.isArray(value) ? value.join(", ") : value,
            })}
            getValueFromEvent={(e) => e.target.value}
          >
            <Input placeholder="k1, k2, p1, p2, k3" />
          </Form.Item>
        </>
      )}
    </Card>
  );
}

function numbers(value: unknown): number[] {
  if (Array.isArray(value)) return value.map(Number);
  if (typeof value !== "string" || !value.trim()) return [];
  return value
    .split(/[,，\s]+/)
    .filter(Boolean)
    .map((v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) throw new Error("参数必须为有限数值");
      return n;
    });
}

export function CameraCalibrationEditor({
  organizationId,
  robotId,
  canManage,
}: {
  organizationId: string;
  robotId: string;
  canManage: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [form] = Form.useForm();
  const scope = { organizationId };
  const path = `/organizations/${encodeURIComponent(organizationId)}/robots/${encodeURIComponent(robotId)}/camera-calibration`;
  const query = useScopedQuery(
    scope,
    "robots",
    "camera-calibration",
    { robotId },
    {
      enabled: open,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      queryFn: ({ signal }) =>
        request<State>({
          method: "GET",
          path,
          scope,
          scopeMode: "explicit",
          signal,
        }),
    },
  );
  const save = useScopedMutation(
    scope,
    async (bound, intent: { configuration: Configuration; etag: string }) =>
      request<State>({
        method: "PUT",
        path,
        scope: bound,
        scopeMode: "explicit",
        body: intent.configuration,
        ifMatch: intent.etag,
      }),
  );
  useEffect(() => {
    if (!query.data) return;
    form.resetFields();
    const c = query.data.configuration;
    form.setFieldsValue(
      c
        ? { ...c, roots: c.root_frames.join(", ") }
        : { roots: "", cameras: [], static_transforms: [] },
    );
  }, [query.data, form]);
  const submit = async () => {
    try {
      setMessage(null);
      const value = await form.validateFields();
      const cameras = value.cameras.map((camera: Record<string, any>) => {
        const result = { ...camera };
        for (const kind of ["rgb", "depth"])
          if (result[kind]) {
            result[kind] = {
              ...result[kind],
              intrinsics: {
                ...result[kind].intrinsics,
                distortion_coefficients: numbers(
                  result[kind].intrinsics.distortion_coefficients,
                ),
              },
            };
          }
        return result;
      });
      const static_transforms = value.static_transforms.map(
        (edge: Record<string, unknown>) => ({
          ...edge,
          translation_m: numbers(edge.translation_m),
          rotation_xyzw: numbers(edge.rotation_xyzw),
        }),
      );
      await save.mutateAsync({
        etag: query.data!.etag,
        configuration: {
          root_frames: value.roots.split(/[,，\s]+/).filter(Boolean),
          cameras,
          static_transforms,
        },
      });
      await query.refetch();
      setMessage("已保存。新的 LeRobot 导出将附带这份配置；已有导出包不变。");
    } catch (error) {
      setMessage(
        error instanceof Error
          ? error.message
          : "请检查表单和坐标系关系后重试。",
      );
    }
  };
  return (
    <>
      <Button
        onClick={() => {
          setOpen(true);
          setMessage(null);
        }}
      >
        相机内外参
      </Button>
      <Modal
        title="机器人相机内外参"
        open={open}
        width={850}
        onCancel={() => setOpen(false)}
        onOk={() => void submit()}
        okText="保存配置"
        confirmLoading={save.isPending}
        okButtonProps={{
          disabled: !canManage || !query.data || query.isFetching,
        }}
      >
        <Space orientation="vertical" style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            title="RGB 与深度分别配置内参；外参表示子坐标系到父坐标系的变换。平移单位为米，旋转使用 xyzw 单位四元数。手部相机挂到手腕连杆；静态 TF 不代替机器人运动学。"
          />
          {query.isError && (
            <Alert
              type="error"
              title="相机参数加载失败"
              action={
                <Button onClick={() => void query.refetch()}>重试</Button>
              }
            />
          )}
          {message && (
            <Alert
              type={message.startsWith("已保存") ? "success" : "error"}
              title={message}
            />
          )}
          <Form
            form={form}
            layout="vertical"
            disabled={!canManage || query.isPending}
          >
            <Form.Item
              name="roots"
              label="静态树根坐标系（逗号分隔，可填写基座、手腕等机器人连杆）"
              rules={required}
            >
              <Input placeholder="base_link, wrist_link" />
            </Form.Item>
            <Form.List name="cameras">
              {(fields, { add, remove }) => (
                <Space orientation="vertical" style={{ width: "100%" }}>
                  {fields.map((field) => (
                    <Card
                      key={field.key}
                      title={`相机 ${field.name + 1}`}
                      extra={
                        <Button danger onClick={() => remove(field.name)}>
                          删除相机
                        </Button>
                      }
                    >
                      <Form.Item
                        name={[field.name, "camera_id"]}
                        label="相机标识"
                        rules={required}
                      >
                        <Input placeholder="camera_left" />
                      </Form.Item>
                      <StreamFields camera={field.name} kind="rgb" />
                      <StreamFields camera={field.name} kind="depth" />
                    </Card>
                  ))}
                  <Button
                    onClick={() =>
                      add({ camera_id: "", rgb: null, depth: null })
                    }
                  >
                    添加相机
                  </Button>
                </Space>
              )}
            </Form.List>
            <h4>静态 TF 关系</h4>
            <Form.List name="static_transforms">
              {(fields, { add, remove }) => (
                <Space orientation="vertical" style={{ width: "100%" }}>
                  {fields.map((field) => (
                    <Card
                      key={field.key}
                      size="small"
                      extra={
                        <Button danger onClick={() => remove(field.name)}>
                          删除变换
                        </Button>
                      }
                    >
                      <Space wrap>
                        <Form.Item
                          name={[field.name, "parent_frame"]}
                          label="父坐标系"
                          rules={required}
                        >
                          <Input />
                        </Form.Item>
                        <Form.Item
                          name={[field.name, "child_frame"]}
                          label="子坐标系"
                          rules={required}
                        >
                          <Input />
                        </Form.Item>
                      </Space>
                      <Form.Item
                        name={[field.name, "translation_m"]}
                        label="平移 x, y, z（米）"
                        rules={required}
                        getValueProps={(v) => ({
                          value: Array.isArray(v) ? v.join(", ") : v,
                        })}
                      >
                        <Input placeholder="0, 0, 0" />
                      </Form.Item>
                      <Form.Item
                        name={[field.name, "rotation_xyzw"]}
                        label="旋转四元数 x, y, z, w"
                        rules={required}
                        getValueProps={(v) => ({
                          value: Array.isArray(v) ? v.join(", ") : v,
                        })}
                      >
                        <Input placeholder="0, 0, 0, 1" />
                      </Form.Item>
                    </Card>
                  ))}
                  <Button
                    onClick={() =>
                      add({
                        parent_frame: "",
                        child_frame: "",
                        translation_m: "",
                        rotation_xyzw: "",
                      })
                    }
                  >
                    添加静态变换
                  </Button>
                </Space>
              )}
            </Form.List>
          </Form>
        </Space>
      </Modal>
    </>
  );
}
