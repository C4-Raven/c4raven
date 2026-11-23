import { describe, expect, it } from 'vitest';
import formatDefault, { formatBitRate, formatBytes, formatByteRate } from './bytes_formatter';

describe('formatBytes', () => {
  it('uses binary prefixes', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1536)).toBe('1.5 KiB');
    expect(formatBytes(5 * 1024 ** 3)).toBe('5 GiB');
    expect(formatBytes(1234567, 1)).toBe('1.2 MiB');
  });

  it('copes with bad input', () => {
    expect(formatBytes(Number.NaN)).toBe('0 B');
    expect(formatBytes(-2048)).toBe('-2 KiB');
  });

  it('is the default export', () => {
    expect(formatDefault).toBe(formatBytes);
  });
});

describe('rates', () => {
  it('shows bits per second with decimal prefixes', () => {
    expect(formatBitRate(800)).toBe('800 b/s');
    expect(formatBitRate(2_500_000)).toBe('2.5 Mb/s');
    expect(formatBitRate(1_000_000_000)).toBe('1 Gb/s');
  });

  it('converts byte rates to bits', () => {
    expect(formatByteRate(125_000)).toBe('1 Mb/s');
    expect(formatByteRate(0)).toBe('0 b/s');
  });
});
