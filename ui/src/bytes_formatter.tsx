/**
 * Display helpers for data sizes and transfer rates.
 *
 * Sizes (files, memory, disk) are binary: 1 KiB = 1024 B. Rates follow the networking
 * convention: bits per second with decimal prefixes, 1 Mb/s = 1,000,000 bits/s.
 */

const SIZE_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'] as const;
const RATE_UNITS = ['b/s', 'kb/s', 'Mb/s', 'Gb/s', 'Tb/s'] as const;

function scaled(value: number, base: number, units: readonly string[], decimals: number): string {
  if (!Number.isFinite(value) || value === 0) {
    return `0 ${units[0]}`;
  }
  let step = 0;
  let n = Math.abs(value);
  while (n >= base && step < units.length - 1) {
    n /= base;
    step += 1;
  }
  // Number() drops trailing zeros: "1.5 MiB", not "1.50 MiB".
  const shown = Number(n.toFixed(step === 0 ? 0 : Math.max(0, decimals)));
  return `${value < 0 ? '-' : ''}${shown} ${units[step]}`;
}

/** A byte count as a size, e.g. 1536 -> "1.5 KiB". */
export function formatBytes(bytes: number, decimals = 2): string {
  return scaled(bytes, 1024, SIZE_UNITS, decimals);
}

/** A rate in bits per second, e.g. 2_500_000 -> "2.5 Mb/s". */
export function formatBitRate(bitsPerSecond: number, decimals = 1): string {
  return scaled(bitsPerSecond, 1000, RATE_UNITS, decimals);
}

/** A rate measured in bytes per second, shown in bits per second. */
export function formatByteRate(bytesPerSecond: number, decimals = 1): string {
  return formatBitRate(bytesPerSecond * 8, decimals);
}

export default formatBytes;
