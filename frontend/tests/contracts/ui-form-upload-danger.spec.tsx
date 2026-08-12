import { readFileSync } from 'node:fs';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';
import { ProviderHarness } from '../../src/app/providers';
import { DangerConfirmModal, type DangerConfirmModalProps } from '../../src/shared/ui/actions';
import {
  createZodResolver,
  RHFCheckbox,
  RHFInput,
  RHFSelect,
  SecureUploadPicker,
} from '../../src/shared/ui/forms';

const originalMatchMedia = window.matchMedia;

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => getComputedStyle(element));
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    }),
  });
  localStorage.clear();
});

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: originalMatchMedia });
  vi.restoreAllMocks();
});

const formSchema = z.object({
  name: z.string().min(3, '名称至少 3 个字符'),
  mode: z.enum(['SAFE', 'FAST']),
  enabled: z.boolean(),
}).passthrough();
type FormValues = z.infer<typeof formSchema>;

function ControlledFormHarness({ onSubmit }: { onSubmit: (values: FormValues) => void }) {
  const form = useForm<FormValues>({
    defaultValues: { name: 'stable', mode: 'SAFE', enabled: false, futureField: { retained: true } },
    mode: 'onChange',
    resolver: createZodResolver(formSchema),
  });
  return (
    <form noValidate onSubmit={(event) => { void form.handleSubmit(onSubmit)(event); }}>
      <RHFInput control={form.control} name="name" label="名称" />
      <RHFSelect
        control={form.control}
        name="mode"
        label="模式"
        options={[{ label: '安全', value: 'SAFE' }, { label: '快速', value: 'FAST' }]}
      />
      <RHFCheckbox control={form.control} name="enabled" label="启用">允许执行</RHFCheckbox>
      <output data-testid="dirty">{form.formState.isDirty ? 'dirty' : 'clean'}</output>
      <output data-testid="unknown">{JSON.stringify(form.getValues('futureField'))}</output>
      <button type="button" onClick={() => form.setError('name', { type: 'server', message: '服务端名称冲突' })}>注入服务端错误</button>
      <button type="button" onClick={() => form.reset()}>重置</button>
      <button type="submit">保存</button>
    </form>
  );
}

describe('RHF/Zod controlled adapters', () => {
  it('keeps dirty/reset/server errors in RHF and validation/unknown preservation in Zod', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<ProviderHarness><ControlledFormHarness onSubmit={onSubmit} /></ProviderHarness>);

    expect(screen.getByTestId('dirty')).toHaveTextContent('clean');
    expect(screen.getByTestId('unknown')).toHaveTextContent('{"retained":true}');

    const name = screen.getByRole('textbox', { name: '名称' });
    await user.clear(name);
    await user.type(name, 'x');
    expect(screen.getByTestId('dirty')).toHaveTextContent('dirty');
    expect(await screen.findByText('名称至少 3 个字符')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '保存' }));
    expect(onSubmit).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: '注入服务端错误' }));
    expect(await screen.findByText('服务端名称冲突')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '重置' }));
    expect(screen.getByTestId('dirty')).toHaveTextContent('clean');
    expect(name).toHaveValue('stable');

    await user.click(screen.getByRole('checkbox', { name: /启用\s+允许执行/ }));
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    expect(onSubmit.mock.calls[0]?.[0]).toMatchObject({
      name: 'stable',
      mode: 'SAFE',
      enabled: true,
      futureField: { retained: true },
    });
  });

  it('does not create an Ant Design Form validation or initial-value source', () => {
    const source = readFileSync('src/shared/ui/forms/controlled-fields.tsx', 'utf8');
    expect(source).not.toMatch(/<Form(?!\.)\b/);
    expect(source).not.toMatch(/\brules\s*=/);
    expect(source).not.toMatch(/\binitialValues\s*=/);
    expect(source).toContain('useController');
  });
});

function UploadHarness({ onChange }: { onChange: (files: readonly File[]) => void }) {
  const [files, setFiles] = useState<readonly File[]>([]);
  return (
    <SecureUploadPicker
      files={files}
      maxCount={2}
      multiple
      onFilesChange={(next) => {
        onChange(next);
        setFiles([...next]);
      }}
    />
  );
}

describe('SecureUploadPicker', () => {
  it('selects local files without a request and clears controlled state on unmount', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn<(files: readonly File[]) => void>();
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const xhrOpenSpy = vi.spyOn(XMLHttpRequest.prototype, 'open');
    const view = render(<ProviderHarness><UploadHarness onChange={onChange} /></ProviderHarness>);
    const input = view.container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();

    const file = new File([new Uint8Array([1, 2, 3])], 'fixture.bin', { type: 'application/octet-stream' });
    await user.upload(input as HTMLInputElement, file);
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    expect(onChange.mock.calls.some(([files]) => files[0] === file)).toBe(true);
    expect(await screen.findByText('fixture.bin')).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(xhrOpenSpy).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);

    view.unmount();
    expect(onChange.mock.calls.at(-1)?.[0]).toEqual([]);
  });

  it('has no network, credential, persistence or locator API', () => {
    const source = readFileSync('src/shared/ui/forms/SecureUploadPicker.tsx', 'utf8');
    expect(source).toContain('return false');
    expect(source).not.toMatch(/\baction\s*=/);
    expect(source).not.toMatch(/\bcustomRequest\s*=/);
    expect(source).not.toMatch(/\bfetch\s*\(/);
    expect(source).not.toMatch(/XMLHttpRequest|localStorage|sessionStorage|createObjectURL/);
    expect(source).not.toMatch(/signature|credential|uploadId|bucket|objectKey|authorization/i);
  });
});

const validPreflight = {
  preparedAt: '2026-08-12T11:00:00.000Z',
  expiresAt: '2099-08-12T12:00:00.000Z',
  resourceVersion: 'rv-42',
  scopeKey: 'org-1/project-1/region-1',
} as const;

function renderDanger(props: Partial<DangerConfirmModalProps> = {}) {
  const defaults: DangerConfirmModalProps = {
    open: true,
    title: '删除数据集',
    actionLabel: '确认删除',
    resourceId: 'dataset-123',
    impact: ['删除当前数据集入口', '保留审计事实'],
    blockers: [],
    preflight: validPreflight,
    currentScopeKey: validPreflight.scopeKey,
    onCancel: vi.fn(),
    onConfirm: vi.fn(),
  };
  return render(<ProviderHarness><DangerConfirmModal {...defaults} {...props} /></ProviderHarness>);
}

describe('DangerConfirmModal', () => {
  it('fails closed for missing, invalid, expired, scope-changed, blocked and conflict evidence', () => {
    const view = renderDanger({ preflight: null });
    const confirm = screen.getByRole('button', { name: '确认删除' });
    expect(confirm).toBeDisabled();
    expect(screen.getByText(/缺少完整预检证据/)).toBeInTheDocument();

    view.rerender(<ProviderHarness><DangerConfirmModal
      open title="删除数据集" actionLabel="确认删除" resourceId="dataset-123" impact="影响"
      blockers={[]} preflight={{ ...validPreflight, resourceVersion: '' }} currentScopeKey={validPreflight.scopeKey}
      onCancel={vi.fn()} onConfirm={vi.fn()}
    /></ProviderHarness>);
    expect(confirm).toBeDisabled();

    view.rerender(<ProviderHarness><DangerConfirmModal
      open title="删除数据集" actionLabel="确认删除" resourceId="dataset-123" impact="影响"
      blockers={[]} preflight={{ ...validPreflight, expiresAt: '2020-01-01T00:00:00.000Z' }} currentScopeKey={validPreflight.scopeKey}
      onCancel={vi.fn()} onConfirm={vi.fn()}
    /></ProviderHarness>);
    expect(screen.getByText(/预检已过期/)).toBeInTheDocument();
    expect(confirm).toBeDisabled();

    view.rerender(<ProviderHarness><DangerConfirmModal
      open title="删除数据集" actionLabel="确认删除" resourceId="dataset-123" impact="影响"
      blockers={[]} preflight={validPreflight} currentScopeKey="org-2/project-2/region-2"
      onCancel={vi.fn()} onConfirm={vi.fn()}
    /></ProviderHarness>);
    expect(screen.getByText(/当前作用域已变化/)).toBeInTheDocument();
    expect(confirm).toBeDisabled();

    view.rerender(<ProviderHarness><DangerConfirmModal
      open title="删除数据集" actionLabel="确认删除" resourceId="dataset-123" impact="影响"
      blockers={[{ code: 'HAS_CHILDREN', message: '仍有关联版本' }]} preflight={validPreflight} currentScopeKey={validPreflight.scopeKey}
      onCancel={vi.fn()} onConfirm={vi.fn()}
    /></ProviderHarness>);
    expect(screen.getByText('HAS_CHILDREN')).toBeInTheDocument();
    expect(confirm).toBeDisabled();

    view.rerender(<ProviderHarness><DangerConfirmModal
      open title="删除数据集" actionLabel="确认删除" resourceId="dataset-123" impact="影响"
      blockers={[]} preflight={validPreflight} currentScopeKey={validPreflight.scopeKey}
      conflict={{ status: 412, code: 'VERSION_MISMATCH' }} onCancel={vi.fn()} onConfirm={vi.fn()}
    /></ProviderHarness>);
    expect(screen.getByText(/资源版本不匹配/)).toBeInTheDocument();
    expect(confirm).toBeDisabled();

    view.rerender(<ProviderHarness><DangerConfirmModal
      open title="删除数据集" actionLabel="确认删除" resourceId="" impact="影响"
      blockers={[]} preflight={validPreflight} currentScopeKey={validPreflight.scopeKey}
      confirmation={{ expectedText: '' }} onCancel={vi.fn()} onConfirm={vi.fn()}
    /></ProviderHarness>);
    expect(screen.getByText(/缺少操作或资源标识/)).toBeInTheDocument();
    expect(screen.getByText(/确认文本要求无效/)).toBeInTheDocument();
    expect(confirm).toBeDisabled();
  });

  it('requires exact typed confirmation, traps focus, handles Escape and returns focus', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <ProviderHarness>
          <button type="button" onClick={() => setOpen(true)}>打开危险操作</button>
          <DangerConfirmModal
            open={open}
            title="删除数据集"
            actionLabel="确认删除"
            resourceId="dataset-123"
            impact="删除数据集"
            blockers={[]}
            preflight={validPreflight}
            currentScopeKey={validPreflight.scopeKey}
            confirmation={{ expectedText: 'dataset-123' }}
            onCancel={() => setOpen(false)}
            onConfirm={onConfirm}
          />
        </ProviderHarness>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole('button', { name: '打开危险操作' });
    await user.click(trigger);
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(screen.getByRole('button', { name: /取\s*消/ })).toHaveFocus());
    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);

    const confirmation = screen.getByRole('textbox');
    await user.type(confirmation, 'dataset-wrong');
    expect(screen.getByRole('button', { name: '确认删除' })).toBeDisabled();
    await user.clear(confirmation);
    await user.type(confirmation, 'dataset-123');
    const confirm = screen.getByRole('button', { name: '确认删除' });
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);

    await user.keyboard('{Escape}');
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it.each([409, 412] as const)('delegates %s conflict recovery without performing a request', async (status) => {
    const user = userEvent.setup();
    const onResolveConflict = vi.fn();
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    renderDanger({ conflict: { status, code: status === 409 ? 'STATE_CHANGED' : 'VERSION_MISMATCH' }, onResolveConflict });
    await user.click(screen.getByRole('button', { name: '重新加载并预检' }));
    expect(onResolveConflict).toHaveBeenCalledWith(status);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('blocks cancellation while pending and redacts unsafe impact or blocker text', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    renderDanger({
      pending: true,
      onCancel,
      impact: '下载地址 https://private.example/export?token=secret',
      blockers: [{ code: 'PRIVATE_DETAIL', message: 'token=super-secret' }],
      conflict: { status: 409, code: 'STATE_CHANGED' },
      onResolveConflict: vi.fn(),
    });
    expect(screen.getAllByText('敏感详情已隐藏，请在受控详情页查看。').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/private\.example|super-secret/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /取\s*消/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: '重新加载并预检' })).toBeDisabled();
    await user.keyboard('{Escape}');
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('accepts only safe preflight metadata and owns no API, token or idempotency state', () => {
    const source = readFileSync('src/shared/ui/actions/DangerConfirmModal.tsx', 'utf8');
    const propsBlock = source.slice(source.indexOf('export interface DangerConfirmModalProps'), source.indexOf('function parseTime'));
    expect(propsBlock).not.toMatch(/token|secret|signature|credential|authorization|idempotency/i);
    expect(source).not.toMatch(/\bfetch\s*\(|XMLHttpRequest|localStorage|sessionStorage/);
    expect(source).toContain('status: 409 | 412');
  });
});
