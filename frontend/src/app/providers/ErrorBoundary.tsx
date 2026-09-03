import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(): void {
    // Error details are intentionally not emitted here; telemetry receives a sanitized event upstream.
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="app-error-boundary" role="alert">
          <h1>页面暂时不可用</h1>
          <p>请刷新后重试；若问题持续，请联系平台管理员并提供发生时间。</p>
          <button type="button" onClick={() => globalThis.location.reload()}>
            刷新页面
          </button>
        </main>
      );
    }
    return this.props.children;
  }
}
