import { useEffect, useRef, useState } from 'react';
import { Badge, Group, Stack, Text, Tooltip } from '@mantine/core';
import { DataTable, type DataTableSortStatus } from 'mantine-datatable';
import { useTranslation } from 'react-i18next';
import { Organization12Regular, Phone20Regular } from '@fluentui/react-icons';
import axios from '@/axios_config';
import { apiRoutes } from '@/apiRoutes';
import { PageHeader, Panel } from '@/components/ui';
import { EmptyState, formatDateTime, formatRelative, parseDate, PulseDot } from './ServerRequests/shared';
import { federatedFromLabel } from './eudFederation';
import { errorText, NO_ESCAPE, notifyFailure, DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS, PANEL_TABLE } from './pageUtils';

interface EUD {
    callsign: string | null;
    device: string | null;
    platform: string | null;
    username: string | null;
    uid: string;
    version: string | null;
    last_event_time: string | null;
    last_status: string | null;
    federated_from: string | null;
    federated_from_name: string | null;
}

const POLL_MS = 3000;

function orDash(value: string | null | undefined) {
    return value ? <Text size="sm">{value}</Text> : <Text size="sm" c="dimmed">—</Text>;
}

export default function EUDs() {
    const { t } = useTranslation();

    const [euds, setEuds] = useState<EUD[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
    const [sortStatus, setSortStatus] = useState<DataTableSortStatus<EUD>>({
        columnAccessor: 'last_event_time',
        direction: 'desc',
    });
    const [loading, setLoading] = useState(false);

    const inFlight = useRef(false);
    const lastBody = useRef<string>('');
    const latestRequest = useRef(0);

    function load(poll: boolean) {
        if (poll && inFlight.current) { return; }
        inFlight.current = true;
        const requestId = ++latestRequest.current;
        if (!poll) { setLoading(true); }
        axios.get(apiRoutes.eudList, {
            params: {
                page,
                per_page: pageSize,
                sort_by: sortStatus.columnAccessor,
                sort_direction: sortStatus.direction,
            },
        }).then((r) => {
            // A newer load (e.g. the user changed page) owns the table now
            if (requestId !== latestRequest.current || r.status !== 200) { return; }
            const body = JSON.stringify(r.data);
            if (poll && body === lastBody.current) { return; }
            lastBody.current = body;
            setEuds(r.data.results ?? []);
            setTotal(r.data.total ?? 0);
            setPage(r.data.current_page ?? 1);
        }).catch((err) => {
            if (!poll) { notifyFailure(t('Couldn\'t load devices'), errorText(err)); }
        }).finally(() => {
            inFlight.current = false;
            if (!poll) { setLoading(false); }
        });
    }

    useEffect(() => {
        load(false);
        const timer = window.setInterval(() => load(true), POLL_MS);
        return () => window.clearInterval(timer);
    }, [page, pageSize, sortStatus]);

    return (
        <>
            <PageHeader
                title={t('EUDs')}
                description={t('TAK clients that have connected to this server. Status refreshes every few seconds.')}
            />

            <Panel p={0}>
                <DataTable
                    {...PANEL_TABLE}
                    highlightOnHover
                    verticalSpacing="sm"
                    records={euds}
                    idAccessor="uid"
                    columns={[
                        {
                            accessor: 'callsign',
                            title: t('Callsign'),
                            sortable: true,
                            render: (e) => (
                                <Group gap={6} wrap="nowrap">
                                    <Text size="sm" fw={500}>{e.callsign}</Text>
                                    {e.federated_from && (
                                        <Tooltip
                                            withArrow
                                            label={t('Federation Hub server ID: {{id}}', { id: e.federated_from, ...NO_ESCAPE })}
                                        >
                                            <Badge
                                                size="sm"
                                                color="indigo"
                                                variant="light"
                                                leftSection={<Organization12Regular />}
                                            >
                                                {t('Federated from {{server}}', {
                                                    server: federatedFromLabel(e.federated_from, e.federated_from_name),
                                                    ...NO_ESCAPE,
                                                })}
                                            </Badge>
                                        </Tooltip>
                                    )}
                                </Group>
                            ),
                        },
                        {
                            accessor: 'last_status',
                            title: t('Status'),
                            sortable: true,
                            render: (e) => (e.last_status === 'Connected'
                                ? (
                                    <Badge color="green" variant="light" leftSection={<PulseDot active />}>
                                        {t('Connected')}
                                    </Badge>
                                )
                                : (
                                    <Badge color="gray" variant="light" leftSection={<PulseDot active={false} />}>
                                        {t('Disconnected')}
                                    </Badge>
                                )),
                        },
                        { accessor: 'device', title: t('Device'), sortable: true, render: (e) => orDash(e.device) },
                        { accessor: 'platform', title: t('Platform'), sortable: true, render: (e) => orDash(e.platform) },
                        { accessor: 'version', title: t('Version'), sortable: true, render: (e) => orDash(e.version) },
                        { accessor: 'username', title: t('Username'), render: (e) => e.username || '—' },
                        {
                            accessor: 'uid',
                            title: t('UID'),
                            render: (e) => (
                                <Text size="xs" c="dimmed" ff="monospace" truncate maw={240} title={e.uid}>
                                    {e.uid}
                                </Text>
                            ),
                        },
                        {
                            accessor: 'last_event_time',
                            title: t('Last Event Time'),
                            sortable: true,
                            render: (e) => {
                                if (!e.last_event_time) { return '—'; }
                                if (!parseDate(e.last_event_time)) { return e.last_event_time; }
                                return (
                                    <Stack gap={0}>
                                        <Text size="sm">{formatDateTime(e.last_event_time)}</Text>
                                        <Text size="xs" c="dimmed">{formatRelative(e.last_event_time)}</Text>
                                    </Stack>
                                );
                            },
                        },
                    ]}
                    page={page}
                    onPageChange={setPage}
                    totalRecords={total}
                    recordsPerPage={pageSize}
                    recordsPerPageOptions={PAGE_SIZE_OPTIONS}
                    onRecordsPerPageChange={(size) => {
                        setPage(1);
                        setPageSize(size);
                    }}
                    sortStatus={sortStatus}
                    onSortStatusChange={setSortStatus}
                    fetching={loading}
                    minHeight={euds.length === 0 ? 320 : undefined}
                    emptyState={(
                        <EmptyState
                            icon={<Phone20Regular />}
                            title={t('No devices yet')}
                            description={t('Devices appear here once they connect to the server.')}
                        />
                    )}
                />
            </Panel>
        </>
    );
}
