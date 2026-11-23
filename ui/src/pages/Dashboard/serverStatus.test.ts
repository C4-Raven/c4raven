import { describe, expect, it } from 'vitest';
import { EMPTY_STATUS, parseServerStatus } from './serverStatus';

describe('parseServerStatus', () => {
  it('maps a status reply', () => {
    const s = parseServerStatus({
      online_euds: 3,
      cpu_percent: 12.5,
      memory: { used: 1, total: 4, percent: 25 },
      disk_usage: { used: 2, free: 8, total: 10, percent: 20 },
      server_version: '2.5.5',
      python_version: '3.12.3',
      server_uptime: 60,
      system_uptime: 3600,
      uname: { node: 'raven', system: 'Linux', release: '6.17', version: '#41', machine: 'x86_64' },
      os_release: {
        NAME: 'Ubuntu',
        PRETTY_NAME: 'Ubuntu 25.10',
        VERSION: '25.10',
        VERSION_CODENAME: 'questing',
      },
      network: { bytes_recv: 100, bytes_sent: 50 },
    });
    expect(s.onlineEuds).toBe(3);
    expect(s.cpuPercent).toBe(12.5);
    expect(s.disk).toEqual({ used: 2, free: 8, total: 10, percent: 20 });
    expect(s.server).toMatchObject({
      version: '2.5.5',
      pythonVersion: '3.12.3',
      uptimeSeconds: 60,
    });
    expect(s.host).toMatchObject({
      hostname: 'raven',
      kernel: 'Linux',
      arch: 'x86_64',
      uptimeSeconds: 3600,
    });
    expect(s.os.prettyName).toBe('Ubuntu 25.10');
    expect(s.netTotals).toEqual({ received: 100, sent: 50 });
  });

  it('falls back to empty values for a partial reply', () => {
    expect(parseServerStatus({})).toEqual(EMPTY_STATUS);
    expect(parseServerStatus(null)).toEqual(EMPTY_STATUS);
  });
});
