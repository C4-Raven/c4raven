import React, { useEffect, useRef, useState } from 'react';
import { Text, SimpleGrid, Group, Tooltip } from '@mantine/core';
import { AreaChart, type AreaChartProps } from '@mantine/charts';
import { Clock20Regular, DeveloperBoard20Regular, HardDrive20Regular, Phone20Regular, Ram20Regular } from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import { parseISO, intervalToDuration, formatDuration, format } from 'date-fns';
import { PageHeader, Panel, Section, StatTile } from '@/components/ui';
import { PulseDot } from '../ServerRequests/shared';
import { versions } from '../../_versions';
import axios from '../../axios_config';
import { apiRoutes } from '../../apiRoutes';
import { formatByteRate, formatBytes } from '../../bytes_formatter';
import MessageTraffic from './MessageTraffic';
import { EMPTY_STATUS, parseServerStatus, type ServerStatus } from './serverStatus';
import '@mantine/charts/styles.css';

const POLL_INTERVAL_MS = 1000;
const HISTORY_LIMIT = 300; // 5 minutes of history at the poll interval above

type UsagePoint = { time: number; cpu: number; memory: number; disk: number; netRecv: number; netSent: number };

/** Props shared by every resource chart: flat fills, time x-axis, x grid only. */
const chartProps: Omit<AreaChartProps, 'data' | 'series'> = {
    h: 120,
    dataKey: 'time',
    curveType: 'monotone',
    withDots: false,
    withGradient: false,
    withTooltip: true,
    withXAxis: true,
    withYAxis: true,
    gridAxis: 'x',
    strokeWidth: 2,
    fillOpacity: 0.2,
    tooltipProps: { labelFormatter: (v) => (typeof v === 'number' ? format(new Date(v), 'HH:mm:ss') : v) },
    xAxisProps: { tickFormatter: (v: number) => format(new Date(v), 'HH:mm:ss'), minTickGap: 40 },
};

/** One outlined chart panel; the current value sits on the panel's title row. */
function ChartPanel({ label, value, detail, color, children }: {
    label: string;
    value: React.ReactNode;
    detail?: React.ReactNode;
    color: string;
    children: React.ReactNode;
}) {
    return (
        <Panel
          title={label}
          actions={(
              <Group gap="xs" align="baseline" wrap="nowrap">
                  {detail && <Text size="xs" c="dimmed">{detail}</Text>}
                  <Text size="sm" fw={600} c={color}>{value}</Text>
              </Group>
          )}
        >
            {children}
        </Panel>
    );
}

function usageColor(percent: number): string {
    if (percent >= 90) {return 'red';}
    if (percent >= 75) {return 'orange';}
    return 'teal';
}

function DetailRow({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
    return (
        <Group justify="space-between" wrap="nowrap" gap="md" py={6}>
            <Text size="sm" c="dimmed" style={{ flexShrink: 0 }}>{label}</Text>
            <Tooltip label={value} disabled={!value} multiline maw={360} openDelay={400}>
                <Text size="sm" ff={mono ? 'monospace' : undefined} truncate ta="right">{value || '—'}</Text>
            </Tooltip>
        </Group>
    );
}

function compactDuration(seconds: number): string {
    if (!seconds) {
        return '—';
    }
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) {
        return `${days}d ${hours}h`;
    }
    return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function humanDuration(seconds: number, units?: Array<'days' | 'hours' | 'minutes'>): string {
    if (!seconds) {return '—';}
    const text = formatDuration(intervalToDuration({ start: 0, end: seconds * 1000 }), units ? { format: units } : undefined);
    return text || '< 1 minute';
}

export default function Dashboard() {
    const { t } = useTranslation();
    const [live, setLive] = useState(true);
    const [status, setStatus] = useState<ServerStatus>(EMPTY_STATUS);
    const [history, setHistory] = useState<UsagePoint[]>([]);
    const [network, setNetwork] = useState({ recvRate: 0, sentRate: 0 });
    const lastNet = useRef<{ received: number; sent: number; at: number } | null>(null);

    useEffect(() => {
        const poll = () => {
            axios.get(apiRoutes.serverStatus).then((r) => {
                if (r.status !== 200) {return;}
                const next = parseServerStatus(r.data);
                const at = Date.now();
                setLive(true);
                setStatus(next);

                // Byte counters only ever grow; the rate is the growth since the last reply.
                let recvRate = 0;
                let sentRate = 0;
                if (next.netTotals) {
                    const prev = lastNet.current;
                    const seconds = prev ? (at - prev.at) / 1000 : 0;
                    if (prev && seconds > 0) {
                        recvRate = Math.max(0, (next.netTotals.received - prev.received) / seconds);
                        sentRate = Math.max(0, (next.netTotals.sent - prev.sent) / seconds);
                    }
                    lastNet.current = { ...next.netTotals, at };
                    setNetwork({ recvRate, sentRate });
                }

                const point: UsagePoint = {
                    time: at,
                    cpu: next.cpuPercent,
                    memory: next.memory.percent,
                    disk: next.disk.percent,
                    netRecv: recvRate,
                    netSent: sentRate,
                };
                setHistory((prev) => [...prev, point].slice(-HISTORY_LIMIT));
            }).catch((err) => {
                console.log(err);
                setLive(false);
            });
        };

        poll();
        // Skip polls while the browser tab is hidden; the charts just resume.
        const timer = setInterval(() => {
            if (!document.hidden) {poll();}
        }, POLL_INTERVAL_MS);
        return () => clearInterval(timer);
    }, []);

    const { memory, disk, server, host, os } = status;
    const historyMinutes = Math.round(HISTORY_LIMIT * POLL_INTERVAL_MS / 6000) / 10;
    const memoryDetail = `${formatBytes(memory.used)} / ${formatBytes(memory.total)}`;
    const percentChart = (dataKey: 'cpu' | 'memory' | 'disk', color: string) => (
        <AreaChart
          {...chartProps}
          data={history}
          series={[{ name: dataKey, color: `${color}.6` }]}
          withLegend={false}
          yAxisProps={{ domain: [0, 100], width: 34, tickCount: 3 }}
          unit="%"
        />
    );

    return (
        <>
            <PageHeader
              title={t('Server Status')}
              description={[host.hostname, os.prettyName, server.version && `C4 RAVEN ${server.version}`].filter(Boolean).join(' · ')}
              actions={(
                  <Group gap={6}>
                      <PulseDot active color={live ? 'var(--mantine-color-green-5)' : 'var(--mantine-color-red-5)'} />
                      <Text size="xs" c="dimmed">{live ? t('Live') : t('Server not responding')}</Text>
                  </Group>
              )}
            />

            <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="md" mb="xl">
                <StatTile icon={<Phone20Regular />} label={t('Online EUDs')} value={status.onlineEuds} color="blue" />
                <StatTile icon={<DeveloperBoard20Regular />} label={t('CPU')} value={`${status.cpuPercent}%`} color={usageColor(status.cpuPercent)} />
                <StatTile
                  icon={<Ram20Regular />}
                  label={`${t('Memory')} · ${memoryDetail}`}
                  value={`${memory.percent}%`}
                  color={usageColor(memory.percent)}
                />
                <StatTile
                  icon={<HardDrive20Regular />}
                  label={`${t('Disk')} · ${formatBytes(disk.free)} ${t('free')}`}
                  value={`${disk.percent}%`}
                  color={usageColor(disk.percent)}
                />
                <StatTile
                  icon={<Clock20Regular />}
                  label={`${t('Uptime')} · ${t('C4 RAVEN')} ${humanDuration(server.uptimeSeconds, ['days', 'hours', 'minutes'])}`}
                  value={compactDuration(host.uptimeSeconds)}
                  color="violet"
                />
            </SimpleGrid>

            <MessageTraffic />

            <Section title={t('Resource usage')} description={t('Live · last {{minutes}} min', { minutes: historyMinutes })}>
                <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
                    <ChartPanel label={t('CPU')} value={`${status.cpuPercent}%`} color="orange">
                        {percentChart('cpu', 'orange')}
                    </ChartPanel>
                    <ChartPanel label={t('Memory')} value={`${memory.percent}%`} detail={memoryDetail} color="teal">
                        {percentChart('memory', 'teal')}
                    </ChartPanel>
                    <ChartPanel
                      label={t('Disk')}
                      value={`${disk.percent}%`}
                      detail={`${formatBytes(disk.used)} / ${formatBytes(disk.total)}`}
                      color="grape"
                    >
                        {percentChart('disk', 'grape')}
                    </ChartPanel>
                    <ChartPanel
                      label={t('Network')}
                      value={`↓ ${formatByteRate(network.recvRate, 1)}  ↑ ${formatByteRate(network.sentRate, 1)}`}
                      color="orange"
                    >
                        <AreaChart
                          {...chartProps}
                          data={history}
                          series={[
                              { name: 'netRecv', label: 'Download', color: 'orange.6' },
                              { name: 'netSent', label: 'Upload', color: 'yellow.6' },
                          ]}
                          withLegend
                          legendProps={{ verticalAlign: 'top', height: 24 }}
                          valueFormatter={(v) => formatByteRate(v, 1)}
                          yAxisProps={{ width: 60, tickCount: 3, tickFormatter: (v: number) => formatByteRate(v, 0) }}
                        />
                    </ChartPanel>
                </SimpleGrid>
            </Section>

            <Section title={t('Server details')}>
                <SimpleGrid cols={{ base: 1, md: 3 }} spacing="md">
                    <Panel title={t('C4 RAVEN')}>
                        <DetailRow label={t('Server version')} value={server.version} mono />
                        <DetailRow label={t('UI version')} value={versions.gitTag?.replace(/^v/, '') || versions.gitCommitHash?.slice(0, 10)} mono />
                        <DetailRow label={t('UI commit')} value={versions.gitCommitHash?.slice(0, 10)} mono />
                        <DetailRow label={t('UI built')} value={versions.versionDate && parseISO(versions.versionDate).toLocaleString()} />
                        <DetailRow label={t('Python')} value={server.pythonVersion} mono />
                        <DetailRow label={t('Running since')} value={server.startedAt} />
                    </Panel>
                    <Panel title={t('Operating system')}>
                        <DetailRow label={t('Name')} value={os.prettyName || os.name} />
                        <DetailRow label={t('Version')} value={os.version} />
                        <DetailRow label={t('Code name')} value={os.codename} />
                        <DetailRow label={t('Booted')} value={host.bootedAt} />
                    </Panel>
                    <Panel title={t('Kernel')}>
                        <DetailRow label={t('Hostname')} value={host.hostname} mono />
                        <DetailRow label={t('System')} value={host.kernel} />
                        <DetailRow label={t('Release')} value={host.kernelRelease} mono />
                        <DetailRow label={t('Architecture')} value={host.arch} mono />
                        <DetailRow label={t('Build')} value={host.kernelBuild} mono />
                    </Panel>
                </SimpleGrid>
            </Section>
        </>
    );
}
