import { useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Alert, Button, Form, Modal, Select } from "antd";
import { assignProjectMember, useJoinableProjects } from "../../features/account/access-api";

type Props = {
  organizationId: string;
  member: { principal_id: string; username: string };
  close: () => void;
  onAssigned: (name: string) => void;
};

export function AssignProjectDialog({ organizationId, member, close, onAssigned }: Props) {
  const projects = useJoinableProjects(organizationId, member.principal_id);
  const [choice, setChoice] = useState<string>();
  const options = (projects.data ?? []).map(p => ({ value: p.project_id, label: p.project_name === p.project_id
    ? p.project_name : p.project_name + "（" + p.project_id + "）" }));
  const selected = options.find(p => p.value === choice) ?? (options.length === 1 ? options[0] : undefined);
  const key = useRef(crypto.randomUUID());
  const assignment = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("请选择项目。");
      await assignProjectMember(organizationId, selected.value, member.principal_id, key.current);
      return selected.label;
    },
    onSuccess: onAssigned,
  });
  return <Modal open zIndex={1200} title={"将 " + member.username + " 加入项目"} okText="加入项目" cancelText="取消"
    confirmLoading={assignment.isPending} okButtonProps={{ disabled: !selected || projects.isError }}
    onOk={() => assignment.mutate()} onCancel={() => { if (!assignment.isPending) close(); }}>
    <Form layout="vertical"><Form.Item label="目标项目">
      <Select showSearch optionFilterProp="label" aria-label="目标项目" placeholder="选择本组织的项目"
        loading={projects.isLoading} disabled={assignment.isPending || !options.length} value={selected?.value} options={options}
        onChange={value => { key.current = crypto.randomUUID(); assignment.reset(); setChoice(value); }} />
    </Form.Item></Form>
    {projects.isSuccess && !options.length && <p>当前组织没有可添加该成员的项目。</p>}
    {projects.isError && <Alert type="error" showIcon message={projects.error.message}
      action={<Button onClick={() => void projects.refetch()}>重试</Button>} />}
    {assignment.isError && <Alert type="error" showIcon message={assignment.error.message} />}
  </Modal>;
}
