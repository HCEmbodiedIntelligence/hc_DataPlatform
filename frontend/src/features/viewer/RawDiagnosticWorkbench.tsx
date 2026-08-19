import type { JSX } from 'react';
import { DataVisualizationWorkbench } from './DataVisualizationWorkbench';
import {
  createRawDiagnosticWorkbenchAdapter,
  type RawDiagnosticAdapterInput,
} from './raw-diagnostic-adapter';
import type { DataVisualizationWorkbenchSlots } from './workbench-contract';

export interface RawDiagnosticWorkbenchProps extends RawDiagnosticAdapterInput {
  readonly slots?: DataVisualizationWorkbenchSlots;
}

export function RawDiagnosticWorkbench({ slots, ...input }: RawDiagnosticWorkbenchProps): JSX.Element {
  const adapter = createRawDiagnosticWorkbenchAdapter(input);
  return <DataVisualizationWorkbench adapter={adapter} slots={slots} />;
}
