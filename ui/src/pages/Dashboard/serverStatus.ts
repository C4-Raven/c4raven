import { parseISO } from 'date-fns';

/** What the dashboard shows from one /api/status reply. */
export interface ServerStatus {
  onlineEuds: number;
  cpuPercent: number;
  memory: { used: number; total: number; percent: number };
  disk: { used: number; free: number; total: number; percent: number };
  server: { version: string; pythonVersion: string; uptimeSeconds: number; startedAt: string };
  host: {
    hostname: string;
    kernel: string;
    kernelRelease: string;
    kernelBuild: string;
    arch: string;
    bootedAt: string;
    uptimeSeconds: number;
  };
  os: { name: string; prettyName: string; version: string; codename: string };
  /** Cumulative interface counters; rates come from the change between two replies. */
  netTotals: { received: number; sent: number } | null;
}

export const EMPTY_STATUS: ServerStatus = {
  onlineEuds: 0,
  cpuPercent: 0,
  memory: { used: 0, total: 0, percent: 0 },
  disk: { used: 0, free: 0, total: 0, percent: 0 },
  server: { version: '', pythonVersion: '', uptimeSeconds: 0, startedAt: '' },
  host: {
    hostname: '',
    kernel: '',
    kernelRelease: '',
    kernelBuild: '',
    arch: '',
    bootedAt: '',
    uptimeSeconds: 0,
  },
  os: { name: '', prettyName: '', version: '', codename: '' },
  netTotals: null,
};

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const when = (iso: unknown): string => {
  const text = str(iso);
  return text ? parseISO(text).toLocaleString() : '';
};

/** Reads an /api/status body; missing fields fall back to empty values. */
export function parseServerStatus(body: any): ServerStatus {
  const mem = body?.memory ?? {};
  const disk = body?.disk_usage ?? {};
  const uname = body?.uname ?? {};
  const os = body?.os_release ?? {};
  const net = body?.network;
  return {
    onlineEuds: num(body?.online_euds),
    cpuPercent: num(body?.cpu_percent),
    memory: { used: num(mem.used), total: num(mem.total), percent: num(mem.percent) },
    disk: {
      used: num(disk.used),
      free: num(disk.free),
      total: num(disk.total),
      percent: num(disk.percent),
    },
    server: {
      version: str(body?.server_version),
      pythonVersion: str(body?.python_version),
      uptimeSeconds: num(body?.server_uptime),
      startedAt: when(body?.server_start_time),
    },
    host: {
      hostname: str(uname.node),
      kernel: str(uname.system),
      kernelRelease: str(uname.release),
      kernelBuild: str(uname.version),
      arch: str(uname.machine),
      bootedAt: when(body?.system_boot_time),
      uptimeSeconds: num(body?.system_uptime),
    },
    os: {
      name: str(os.NAME),
      prettyName: str(os.PRETTY_NAME),
      version: str(os.VERSION),
      codename: str(os.VERSION_CODENAME),
    },
    netTotals: net ? { received: num(net.bytes_recv), sent: num(net.bytes_sent) } : null,
  };
}
