import type {
  FullResult,
  Reporter,
  TestCase,
  TestResult,
} from '@playwright/test/reporter';

export default class FailOnSkippedReporter implements Reporter {
  private skipped: string[] = [];

  onTestEnd(test: TestCase, result: TestResult): void {
    if (result.status === 'skipped') {
      this.skipped.push(test.titlePath().filter(Boolean).join(' > '));
    }
  }

  onEnd(_result: FullResult): { status: 'failed' } | undefined {
    if (this.skipped.length === 0) {
      return undefined;
    }
    console.error(
      `Real API release gate blocked: ${this.skipped.length} skipped test(s):\n${this.skipped.join('\n')}`,
    );
    return { status: 'failed' };
  }
}
