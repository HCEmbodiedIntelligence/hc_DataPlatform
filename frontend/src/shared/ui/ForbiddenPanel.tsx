export interface ForbiddenPanelProps {
  title?: string;
  description?: string;
  returnAction?: ReactNode;
}

import type { ReactNode } from 'react';

export function ForbiddenPanel({ title = '无权访问', description = '当前授权不允许读取此资源。', returnAction }: ForbiddenPanelProps) {
  return (
    <section className="forbidden-panel" role="alert">
      <h2>{title}</h2>
      <p>{description}</p>
      {returnAction ? <div>{returnAction}</div> : null}
    </section>
  );
}
