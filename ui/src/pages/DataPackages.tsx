import { useEffect, useRef, useState } from 'react';
import {
    ActionIcon, Badge, Box, Button, Center, FileButton, Group, Modal, Stack, Switch, Text, Tooltip,
    useComputedColorScheme,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { DataTable, type DataTableSortStatus } from 'mantine-datatable';
import { useTranslation } from 'react-i18next';
import { QRCode } from 'react-qrcode-logo';
import {
    ArrowDownload20Regular, ArrowUpload20Regular, Box20Regular, Delete20Regular, Eye12Regular, LockClosed12Regular,
    QrCode20Regular,
} from '@fluentui/react-icons';
import axios from '@/axios_config';
import { apiRoutes } from '@/apiRoutes';
import { formatBytes } from '@/bytes_formatter';
import { PageHeader, Panel } from '@/components/ui';
import { ItemGroupBadges, ItemGroupsModal } from '@/components/ItemGroups';
import { EmptyState, formatDateTime, formatRelative, ICON_16, parseDate } from './ServerRequests/shared';
import {
    errorText, flattenedErrors, NO_ESCAPE, notifyFailure, notifySuccess, DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS, PANEL_TABLE,
} from './pageUtils';

interface DataPackage {
    filename: string;
    hash: string;
    size: number;
    submission_user: string | null;
    submission_time: string | null;
    install_on_enrollment: boolean;
    install_on_connection: boolean;
    private: boolean;
    groups: string[];
    eud: { callsign: string | null } | null;
    creator_uid?: string | null;
    expiration?: string | null;
    keywords?: string | null;
    mime_type?: string | null;
    tool?: string | null;
}

type InstallFlag = 'install_on_enrollment' | 'install_on_connection';

const isAdmin = () => localStorage.getItem('administrator') === 'true';

async function blobError(err: any): Promise<string | undefined> {
    const data = err?.response?.data;
    try {
        if (data instanceof Blob) {
            const parsed = JSON.parse(await data.text());
            return typeof parsed?.error === 'string' ? parsed.error : undefined;
        }
        return data?.error;
    } catch {
        return undefined;
    }
}

function saveBlob(blob: Blob, filename: string) {
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = href;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
}

export default function DataPackages() {
    const { t } = useTranslation();
    const admin = isAdmin();
    const scheme = useComputedColorScheme('dark');

    const [packages, setPackages] = useState<DataPackage[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
    const [sortStatus, setSortStatus] = useState<DataTableSortStatus<DataPackage>>({
        columnAccessor: 'filename',
        direction: 'asc',
    });
    const [loading, setLoading] = useState(false);

    const [downloading, setDownloading] = useState<string | null>(null);
    const downloadingRef = useRef(false);

    const [qrPackage, setQrPackage] = useState<DataPackage | null>(null);
    const [deletePackage, setDeletePackage] = useState<DataPackage | null>(null);
    const [groupsPackage, setGroupsPackage] = useState<DataPackage | null>(null);

    function getPackages() {
        setLoading(true);
        axios.get(apiRoutes.dataPackageList, {
            params: {
                page,
                per_page: pageSize,
                sort_by: sortStatus.columnAccessor,
                sort_direction: sortStatus.direction,
            },
        }).then((r) => {
            if (r.status === 200) {
                setPackages(r.data.results ?? []);
                setTotal(r.data.total ?? 0);
                setPage(r.data.current_page ?? 1);
            }
        }).catch((err) => {
            notifications.show({ title: t('Failed to get data packages'), message: errorText(err), color: 'red' });
        }).finally(() => setLoading(false));
    }

    useEffect(() => {
        getPackages();
    }, [page, pageSize, sortStatus]);

    function upload(file: File | null) {
        if (!file) { return; }
        const body = new FormData();
        body.append('file', file);
        axios.post(apiRoutes.dataPackageList, body, { headers: { 'Content-Type': 'multipart/form-data' } })
            .then((r) => {
                if (r.status === 200) {
                    getPackages();
                    notifySuccess(t('Data package uploaded'), t('Success'));
                } else {
                    notifyFailure(t('Couldn\'t upload the data package'), r.data?.error);
                }
            })
            .catch((err) => notifyFailure(t('Couldn\'t upload the data package'), errorText(err)));
    }

    function setInstall(pkg: DataPackage, flag: InstallFlag, value: boolean) {
        axios.patch(apiRoutes.dataPackageList, {
            hash: pkg.hash,
            install_on_enrollment: flag === 'install_on_enrollment' ? value : pkg.install_on_enrollment,
            install_on_connection: flag === 'install_on_connection' ? value : pkg.install_on_connection,
        }).then((r) => {
            if (r.status === 200) { getPackages(); }
        }).catch((err) => notifyFailure(t('Couldn\'t save the data package'), flattenedErrors(err)));
    }

    function download(pkg: DataPackage) {
        if (downloadingRef.current) { return; }
        downloadingRef.current = true;
        setDownloading(pkg.hash);
        axios.get(apiRoutes.dataPackageDownload, { params: { hash: pkg.hash }, responseType: 'blob' })
            .then((r) => saveBlob(r.data as Blob, pkg.filename))
            .catch(async (err) => {
                const message = await blobError(err);
                notifyFailure(t('Failed to download data package'), message || t('The data package could not be downloaded'));
            })
            .finally(() => {
                downloadingRef.current = false;
                setDownloading(null);
            });
    }

    function confirmDelete() {
        const pkg = deletePackage;
        setDeletePackage(null);
        if (!pkg) { return; }
        axios.delete(apiRoutes.dataPackageDelete, { params: { hash: pkg.hash } })
            .then((r) => {
                if (r.status === 200) {
                    getPackages();
                    notifySuccess(t('Data package deleted'), t('Success'));
                } else {
                    notifyFailure(t('Delete Failed'), r.data?.error, false);
                }
            })
            .catch((err) => notifyFailure(t('Delete Failed'), errorText(err), false));
    }

    /** Why an install switch is locked, or null when it can be changed. */
    function lockReason(pkg: DataPackage): string | null {
        if (!admin) { return t('Only an administrator can change this'); }
        if (pkg.private) { return t("Private data packages can't be installed automatically"); }
        if (pkg.filename.endsWith('_CONFIG.zip')) {
            return t("Configuration data packages can't be installed automatically");
        }
        return null;
    }

    function installSwitch(pkg: DataPackage, flag: InstallFlag, label: string) {
        const reason = lockReason(pkg);
        const control = (
            <Switch
                aria-label={label}
                checked={!!pkg[flag]}
                disabled={reason !== null}
                onChange={(e) => setInstall(pkg, flag, e.currentTarget.checked)}
            />
        );
        return (
            <Center>
                {reason === null ? control : (
                    <Tooltip label={reason} withinPortal>
                        <span style={{ display: 'inline-flex' }}>{control}</span>
                    </Tooltip>
                )}
            </Center>
        );
    }

    function qrValue(hash: string): string {
        const url = `${window.location.protocol}//${window.location.hostname}:8443/Marti/api/sync/metadata/${hash}/tool`;
        return `tak://com.atakmap.app/import?url=${encodeURIComponent(url)}`;
    }

    const uploadButton = (
        <FileButton onChange={upload}>
            {(props) => (
                <Button {...props} leftSection={<ArrowUpload20Regular />}>{t('Upload Data Package')}</Button>
            )}
        </FileButton>
    );

    const privateColor = scheme === 'dark' ? 'var(--mantine-color-orange-4)' : 'var(--mantine-color-orange-9)';

    return (
        <>
            <PageHeader
                title={t('Data Packages')}
                description={t('Files shared with TAK clients. Packages can be limited to groups and installed automatically when a device enrolls or connects.')}
                actions={uploadButton}
            />

            <Panel p={0}>
                <DataTable
                    {...PANEL_TABLE}
                    highlightOnHover
                    verticalSpacing="sm"
                    records={packages}
                    idAccessor="hash"
                    columns={[
                        {
                            accessor: 'filename',
                            title: t('File Name'),
                            sortable: true,
                            render: (p) => (
                                <Text size="sm" fw={500} maw={320} style={{ overflowWrap: 'anywhere' }}>
                                    {p.filename}
                                </Text>
                            ),
                        },
                        {
                            accessor: 'private',
                            title: t('Visibility'),
                            render: (p) => (p.private
                                ? (
                                    <Badge
                                        color="orange"
                                        variant="light"
                                        leftSection={<LockClosed12Regular />}
                                        styles={{ root: { color: privateColor } }}
                                    >
                                        {t('Private')}
                                    </Badge>
                                )
                                : (
                                    <Badge color="gray" variant="light" leftSection={<Eye12Regular />}>
                                        {t('Public')}
                                    </Badge>
                                )),
                        },
                        {
                            accessor: 'groups',
                            title: t('Groups'),
                            render: (p) => (
                                <ItemGroupBadges
                                    groups={p.groups}
                                    onEdit={admin ? () => setGroupsPackage(p) : undefined}
                                />
                            ),
                        },
                        {
                            accessor: 'size',
                            title: t('Size'),
                            sortable: true,
                            textAlign: 'right',
                            render: (p) => <Text size="sm" style={{ whiteSpace: 'nowrap' }}>{formatBytes(p.size)}</Text>,
                        },
                        {
                            accessor: 'submission_user',
                            title: t('Uploaded by'),
                            sortable: true,
                            render: (p) => {
                                const callsign = p.eud?.callsign;
                                if (!p.submission_user && !callsign) { return '—'; }
                                return (
                                    <Stack gap={0}>
                                        <Text size="sm">{p.submission_user || '—'}</Text>
                                        {callsign && <Text size="xs" c="dimmed">{callsign}</Text>}
                                    </Stack>
                                );
                            },
                        },
                        {
                            accessor: 'submission_time',
                            title: t('Upload Time'),
                            sortable: true,
                            render: (p) => {
                                if (!p.submission_time) { return '—'; }
                                if (!parseDate(p.submission_time)) { return p.submission_time; }
                                return (
                                    <Stack gap={0}>
                                        <Text size="sm">{formatDateTime(p.submission_time)}</Text>
                                        <Text size="xs" c="dimmed">{formatRelative(p.submission_time)}</Text>
                                    </Stack>
                                );
                            },
                        },
                        {
                            accessor: 'install_on_enrollment',
                            title: t('Install on Enrollment'),
                            textAlign: 'center',
                            render: (p) => installSwitch(p, 'install_on_enrollment', t('Install on Enrollment')),
                        },
                        {
                            accessor: 'install_on_connection',
                            title: t('Install on Connection'),
                            textAlign: 'center',
                            render: (p) => installSwitch(p, 'install_on_connection', t('Install on Connection')),
                        },
                        {
                            accessor: 'actions',
                            title: t('Actions'),
                            textAlign: 'right',
                            render: (p) => (
                                <Group gap={4} justify="flex-end" wrap="nowrap">
                                    <Tooltip label={t('Download')}>
                                        <ActionIcon
                                            variant="subtle"
                                            aria-label={t('Download')}
                                            loading={downloading === p.hash}
                                            onClick={() => download(p)}
                                        >
                                            <ArrowDownload20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip label={t('QR Code')}>
                                        <ActionIcon variant="subtle" aria-label={t('QR Code')} onClick={() => setQrPackage(p)}>
                                            <QrCode20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip label={t('Delete')}>
                                        <ActionIcon
                                            variant="subtle"
                                            color="red"
                                            aria-label={t('Delete')}
                                            onClick={() => setDeletePackage(p)}
                                        >
                                            <Delete20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                </Group>
                            ),
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
                    minHeight={packages.length === 0 ? 320 : undefined}
                    emptyState={(
                        <EmptyState
                            icon={<Box20Regular />}
                            title={t('No data packages yet')}
                            description={t('Upload a data package to share it with TAK clients.')}
                            action={uploadButton}
                        />
                    )}
                />
            </Panel>

            {/* QR code */}
            <Modal centered opened={qrPackage !== null} onClose={() => setQrPackage(null)} title={qrPackage?.filename}>
                <Stack align="center" gap="md">
                    <Text size="sm" c="dimmed" ta="center">{t('Scan with ATAK to import this data package.')}</Text>
                    <Box p="xs" bg="#fff" style={{ borderRadius: 'var(--mantine-radius-md)' }}>
                        <QRCode
                            value={qrPackage ? qrValue(qrPackage.hash) : ''}
                            size={320}
                            ecLevel="L"
                            quietZone={10}
                            qrStyle="dots"
                        />
                    </Box>
                </Stack>
            </Modal>

            {/* Delete */}
            <Modal opened={deletePackage !== null} onClose={() => setDeletePackage(null)} title={t('Are you sure?')}>
                <Text size="sm">
                    {deletePackage
                        ? t("Delete {{filename}}? This can't be undone.", { filename: deletePackage.filename, ...NO_ESCAPE })
                        : ''}
                </Text>
                <Group justify="flex-end" mt="md">
                    <Button variant="default" onClick={() => setDeletePackage(null)}>{t('Cancel')}</Button>
                    <Button color="red" leftSection={<Delete20Regular />} onClick={confirmDelete}>{t('Delete')}</Button>
                </Group>
            </Modal>

            {/* Groups */}
            <ItemGroupsModal
                opened={groupsPackage !== null}
                onClose={() => setGroupsPackage(null)}
                title={`${t('Groups')}: ${groupsPackage?.filename ?? ''}`}
                endpoint={apiRoutes.dataPackageGroups}
                identifier={{ hash: groupsPackage?.hash ?? '' }}
                groups={groupsPackage?.groups ?? []}
                onSaved={getPackages}
            />
        </>
    );
}

