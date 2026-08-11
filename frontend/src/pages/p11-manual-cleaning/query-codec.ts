import { compareInt64 } from '../../shared/lib/bigint-string';
import { decimalNanoseconds } from '../../entities/edl';
import { isSafeAppRelativeUrl, routes } from '../../features/cleaning/routing';

export interface CleaningWorkbenchSearch {
  readonly t?: string;
  readonly layout?: string;
  readonly streamId?: string;
  readonly windowStartNs?: string;
  readonly windowEndNs?: string;
  readonly operationId?: string;
  readonly findingId?: string;
  readonly compare: 'source' | 'cleaned' | 'ab';
  readonly returnTo?: string;
}

const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function text(value: string | null, max = 256): string | undefined {
  const normalized = value?.trim().normalize('NFC');
  return normalized && normalized.length <= max && !/\p{Cc}/u.test(normalized)
    ? normalized
    : undefined;
}

function id(value: string | null): string | undefined {
  const normalized = text(value, 128);
  return normalized && ID.test(normalized) ? normalized : undefined;
}

function ns(value: string | null): ReturnType<typeof decimalNanoseconds> | undefined {
  try {
    return value === null ? undefined : decimalNanoseconds(value);
  } catch {
    return undefined;
  }
}

export const cleaningWorkbenchQueryCodec = {
  parse(input: string | URLSearchParams): CleaningWorkbenchSearch {
    const params = input instanceof URLSearchParams
      ? new URLSearchParams(input)
      : new URLSearchParams(input.startsWith('?') ? input.slice(1) : input);
    let windowStartNs = ns(params.get('windowStartNs'));
    let windowEndNs = ns(params.get('windowEndNs'));
    if (!windowStartNs || !windowEndNs || compareInt64(windowStartNs, windowEndNs) >= 0) {
      windowStartNs = undefined;
      windowEndNs = undefined;
    }
    const compare = params.get('compare');
    const returnTo = params.get('returnTo');
    return {
      t: text(params.get('t')),
      layout: text(params.get('layout')),
      streamId: id(params.get('streamId')),
      windowStartNs,
      windowEndNs,
      operationId: id(params.get('operationId')),
      findingId: id(params.get('findingId')),
      compare: compare === 'cleaned' || compare === 'ab' ? compare : 'source',
      returnTo: returnTo && isSafeAppRelativeUrl(returnTo) ? returnTo : undefined,
    };
  },
  build(draftId: string, input: Partial<CleaningWorkbenchSearch> = {}): string {
    return routes.cleaningWorkbench.build({ draftId, ...input });
  },
  canonicalize(draftId: string, input: string | URLSearchParams): string {
    return this.build(draftId, this.parse(input));
  },
} as const;
