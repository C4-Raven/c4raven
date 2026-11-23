import { useEffect, useRef, useState } from 'react';
import {
    ActionIcon, Badge, Box, Button, Center, Group, Modal, MultiSelect, PasswordInput, Select, Stack, Text, TextInput,
    Tooltip,
} from '@mantine/core';
import { DataTable, type DataTableSortStatus } from 'mantine-datatable';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import axios from 'axios';
import sortBy from 'lodash.sortby';
import { QRCode } from 'react-qrcode-logo';
import {
    Add20Regular, ArrowSync20Regular, Delete20Regular, Edit20Regular, LockClosed12Regular, Mail20Regular,
    QrCode20Regular, Save20Regular,
} from '@fluentui/react-icons';
import { apiRoutes } from '@/apiRoutes';
import { PageHeader, Panel } from '@/components/ui';
import { EmptyState, formatDateTime, formatRelative, ICON_16 } from './ServerRequests/shared';
import {
    errorText, NO_ESCAPE, notifyFailure, notifySuccess, DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS, PANEL_TABLE,
} from './pageUtils';
import { missionRequestBody, type MissionForm } from './missionForm';

interface Mission {
    name: string;
    description: string | null;
    owner: { callsign?: string | null } | null;
    defaultRole: { type?: string | null } | null;
    tool: string | null;
    createTime: string | null;
    passwordProtected: boolean;
    qr_code: string;
    creatorUid: string | null;
    password?: string | null;
    hashtags: string[] | string | null;
    groups: { id: number | string }[] | null;
}

interface Option {
    value: string;
    label: string;
}

const EMPTY_FORM: MissionForm = {
    name: '',
    description: '',
    tool: 'public',
    default_role: 'MISSION_SUBSCRIBER',
    password: '',
    creator_uid: null,
    groups: [],
    hashtags: null,
};

const ROLE_COLORS: Record<string, string> = {
    MISSION_OWNER: 'grape',
    MISSION_SUBSCRIBER: 'blue',
    MISSION_READ_ONLY: 'gray',
    MISSION_READONLY_SUBSCRIBER: 'gray',
};

const isAdmin = () => localStorage.getItem('administrator') === 'true';

/** Value used for client-side re-sorting by a column accessor. */
function sortValue(m: Mission, accessor: string): unknown {
    switch (accessor) {
        case 'default_role':
            return m.defaultRole?.type ?? '';
        case 'password_protected':
            return m.passwordProtected ? 1 : 0;
        case 'callsign':
            return m.owner?.callsign ?? '';
        case 'creation_time':
            return m.createTime ?? '';
        default:
            return (m as unknown as Record<string, unknown>)[accessor] ?? '';
    }
}

export default function Missions() {
    const { t } = useTranslation();
    const admin = isAdmin();

    const [missions, setMissions] = useState<Mission[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
    const [sortStatus, setSortStatus] = useState<DataTableSortStatus<Mission>>({
        columnAccessor: 'name',
        direction: 'asc',
    });
    const [loading, setLoading] = useState(false);
    const loadingRef = useRef(false);

    const [devices, setDevices] = useState<Option[]>([]);
    const [groupOptions, setGroupOptions] = useState<Option[]>([]);

    const [formOpen, setFormOpen] = useState(false);
    const [editing, setEditing] = useState(false);
    const [form, setForm] = useState<MissionForm>(EMPTY_FORM);
    const [saving, setSaving] = useState(false);

    const [qrMission, setQrMission] = useState<Mission | null>(null);

    const [inviteMission, setInviteMission] = useState<Mission | null>(null);
    const [inviteUid, setInviteUid] = useState<string | null>(null);
    const [invitePassword, setInvitePassword] = useState('');
    const [inviting, setInviting] = useState(false);

    const [deleteMission, setDeleteMission] = useState<Mission | null>(null);
    const [deleting, setDeleting] = useState(false);

    const roleLabel = (type: string | null | undefined): string => {
        if (!type) { return '—'; }
        if (type === 'MISSION_SUBSCRIBER') { return t('Subscriber'); }
        if (type === 'MISSION_OWNER') { return t('Owner'); }
        if (type === 'MISSION_READ_ONLY') { return t('Read Only'); }
        const words = type.replace(/^MISSION_/, '').replace(/_/g, ' ').toLowerCase();
        return words.charAt(0).toUpperCase() + words.slice(1);
    };

    const roleOptions: Option[] = [
        { value: 'MISSION_SUBSCRIBER', label: t('Subscriber') },
        { value: 'MISSION_OWNER', label: t('Owner') },
        { value: 'MISSION_READ_ONLY', label: t('Read Only') },
    ];

    function getMissions() {
        if (loadingRef.current) { return; }
        loadingRef.current = true;
        setLoading(true);
        axios.get(apiRoutes.missionList, {
            params: {
                page,
                per_page: pageSize,
                sort_by: sortStatus.columnAccessor,
                sort_direction: sortStatus.direction,
            },
        }).then((r) => {
            if (r.status === 200) {
                setMissions(r.data.results ?? []);
                setTotal(r.data.total ?? 0);
                setPage(r.data.current_page ?? 1);
            }
        }).catch((err) => {
            notifyFailure(t('Failed to load Data Sync feeds'), errorText(err));
        }).finally(() => {
            loadingRef.current = false;
            setLoading(false);
        });
    }

    function getDevices() {
        axios.get(apiRoutes.eudList, { params: { all: true } })
            .then((r) => {
                const list = Array.isArray(r.data) ? r.data : [];
                setDevices(list.map((d: { uid: string; callsign: string | null }) => ({
                    value: d.uid,
                    label: d.callsign || d.uid,
                })));
            })
            .catch(() => undefined);
    }

    function getGroups() {
        axios.get(apiRoutes.groupListAll)
            .then((r) => {
                const list = Array.isArray(r.data) ? r.data : [];
                setGroupOptions(list.map((g: { id: number | string; name: string }) => ({
                    value: String(g.id),
                    label: g.name,
                })));
            })
            .catch((err) => notifyFailure(t("Couldn't load groups"), errorText(err)));
    }

    useEffect(() => {
        getMissions();
    }, [page, pageSize, sortStatus]);

    useEffect(() => {
        getDevices();
        getGroups();
    }, [formOpen]);

    useEffect(() => {
        if (inviteMission) {
            getDevices();
            getGroups();
        }
    }, [inviteMission]);

    function changeSort(status: DataTableSortStatus<Mission>) {
        const accessor = String(status.columnAccessor);
        const sorted = sortBy(missions, (m: Mission) => sortValue(m, accessor));
        setMissions(status.direction === 'desc' ? sorted.reverse() : sorted);
        setSortStatus(status);
    }

    function openCreate() {
        setEditing(false);
        setForm(EMPTY_FORM);
        setFormOpen(true);
    }

    function openEdit(m: Mission) {
        setEditing(true);
        setForm({
            name: m.name,
            description: m.description ?? '',
            tool: m.tool ?? 'public',
            default_role: m.defaultRole?.type ?? 'MISSION_SUBSCRIBER',
            // The server never returns the password: blank here means "keep the current one"
            password: '',
            creator_uid: m.creatorUid ?? null,
            groups: (m.groups ?? []).map((g) => String(g.id)),
            hashtags: m.hashtags ?? null,
        });
        setFormOpen(true);
    }

    function closeForm() {
        setFormOpen(false);
        setForm(EMPTY_FORM);
    }

    function saveMission(e: React.FormEvent) {
        e.preventDefault();
        const name = form.name;
        setSaving(true);
        axios.post(apiRoutes.missionList, missionRequestBody(form, editing)).then((r) => {
            if (r.status === 200) {
                getMissions();
                notifySuccess(t('Saved {{name}}', { name, ...NO_ESCAPE }), t('Success'));
            }
        }).catch((err) => {
            notifyFailure(t('Failed to save the feed'), errorText(err));
        }).finally(() => {
            setSaving(false);
            closeForm();
        });
    }

    function openInvite(m: Mission) {
        setInviteUid(null);
        setInvitePassword('');
        setInviting(false);
        setInviteMission(m);
    }

    function sendInvite() {
        const mission = inviteMission;
        if (!mission || !inviteUid) { return; }
        const callsign = devices.find((d) => d.value === inviteUid)?.label ?? inviteUid;
        setInviting(true);
        axios.post(apiRoutes.missionInvite, {
            mission_name: mission.name,
            uid: inviteUid,
            password: invitePassword || '',
        }).then((r) => {
            if (r.status === 200) {
                notifySuccess(
                    t('Invited {{callsign}} to {{name}}', { callsign, name: mission.name, ...NO_ESCAPE }),
                    t('Success'),
                );
            }
        }).catch((err) => {
            notifyFailure(t('Failed to send the feed invitation'), errorText(err));
        }).finally(() => setInviting(false));
    }

    function confirmDelete() {
        const mission = deleteMission;
        if (!mission) { return; }
        setDeleting(true);
        axios.delete(apiRoutes.missionList, { params: { name: mission.name } })
            .then((r) => {
                if (r.status === 200) {
                    setDeleteMission(null);
                    notifySuccess(t('Deleted {{name}}', { name: mission.name, ...NO_ESCAPE }), t('Success'));
                    getMissions();
                }
            })
            .catch((err) => notifyFailure(t('Failed to delete the feed'), errorText(err)))
            .finally(() => setDeleting(false));
    }

    const createButton = (
        <Button leftSection={<Add20Regular />} onClick={openCreate}>{t('Create feed')}</Button>
    );

    return (
        <>
            <PageHeader
                title={t('Data Sync')}
                description={t('Shared feeds of files, map items and layers that ATAK devices subscribe to in Data Sync.')}
                actions={createButton}
            />

            <Panel p={0}>
                <DataTable
                    {...PANEL_TABLE}
                    highlightOnHover
                    verticalSpacing="sm"
                    records={missions}
                    idAccessor="name"
                    columns={[
                        {
                            accessor: 'name',
                            title: t('Name'),
                            sortable: true,
                            render: (m) => (
                                <Text
                                    component={Link}
                                    to={`/missions/${encodeURIComponent(m.name)}`}
                                    fw={600}
                                    size="sm"
                                    c="var(--mantine-primary-color-filled)"
                                >
                                    {m.name}
                                </Text>
                            ),
                        },
                        {
                            accessor: 'description',
                            title: t('Description'),
                            sortable: true,
                            render: (m) => (
                                <Text size="sm" c="dimmed" lineClamp={2}>{m.description || '—'}</Text>
                            ),
                        },
                        {
                            accessor: 'callsign',
                            title: t('Owner'),
                            render: (m) => m.owner?.callsign || '—',
                        },
                        {
                            accessor: 'tool',
                            title: t('Tool'),
                            sortable: true,
                            render: (m) => (m.tool
                                ? <Badge color="gray" variant="light">{m.tool}</Badge>
                                : '—'),
                        },
                        {
                            accessor: 'default_role',
                            title: t('Default role'),
                            sortable: true,
                            render: (m) => {
                                const type = m.defaultRole?.type;
                                if (!type) { return '—'; }
                                return (
                                    <Badge variant="light" color={ROLE_COLORS[type] ?? 'gray'}>
                                        {roleLabel(type)}
                                    </Badge>
                                );
                            },
                        },
                        {
                            accessor: 'password_protected',
                            title: t('Access'),
                            sortable: true,
                            render: (m) => (m.passwordProtected
                                ? (
                                    <Badge color="gray" variant="light" leftSection={<LockClosed12Regular />}>
                                        {t('Password')}
                                    </Badge>
                                )
                                : <Text size="sm" c="dimmed">{t('Open')}</Text>),
                        },
                        {
                            accessor: 'creation_time',
                            title: t('Created'),
                            render: (m) => {
                                const relative = formatRelative(m.createTime);
                                if (!relative) { return '—'; }
                                return (
                                    <Tooltip label={formatDateTime(m.createTime)}>
                                        <Text size="sm" c="dimmed" span>{relative}</Text>
                                    </Tooltip>
                                );
                            },
                        },
                        {
                            accessor: 'actions',
                            title: '',
                            textAlign: 'right',
                            render: (m) => (
                                <Group gap={4} justify="flex-end" wrap="nowrap">
                                    <Tooltip label={t('Edit')}>
                                        <ActionIcon variant="subtle" aria-label={t('Edit')} onClick={() => openEdit(m)}>
                                            <Edit20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip label={t('Invite a device')}>
                                        <ActionIcon
                                            variant="subtle"
                                            aria-label={t('Invite a device')}
                                            onClick={() => openInvite(m)}
                                        >
                                            <Mail20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip label={t('QR Code')}>
                                        <ActionIcon variant="subtle" aria-label={t('QR Code')} onClick={() => setQrMission(m)}>
                                            <QrCode20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip label={admin ? t('Delete feed') : t('Only administrators can delete feeds')}>
                                        <span style={{ display: 'inline-flex' }}>
                                            <ActionIcon
                                                variant="subtle"
                                                color="red"
                                                disabled={!admin}
                                                aria-label={t('Delete {{name}}', { name: m.name, ...NO_ESCAPE })}
                                                onClick={() => setDeleteMission(m)}
                                            >
                                                <Delete20Regular style={ICON_16} />
                                            </ActionIcon>
                                        </span>
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
                    onSortStatusChange={changeSort}
                    fetching={loading}
                    minHeight={missions.length === 0 ? 320 : undefined}
                    emptyState={(
                        <EmptyState
                            icon={<ArrowSync20Regular />}
                            title={t('No Data Sync feeds yet')}
                            description={t('Create a feed here, or from ATAK Data Sync, to share files and map items with a team.')}
                            action={createButton}
                        />
                    )}
                />
            </Panel>

            {/* Create / edit */}
            <Modal opened={formOpen} onClose={closeForm} title={editing ? t('Edit feed') : t('Create feed')}>
                <form onSubmit={saveMission}>
                    <Stack gap="sm">
                        <TextInput
                            required
                            label={t('Name')}
                            placeholder={t('Feed name')}
                            disabled={editing}
                            value={form.name}
                            onChange={(e) => {
                                const name = e.currentTarget.value;
                                setForm((f) => ({ ...f, name }));
                            }}
                        />
                        <TextInput
                            label={t('Description')}
                            placeholder={t('Description')}
                            value={form.description}
                            onChange={(e) => {
                                const description = e.currentTarget.value;
                                setForm((f) => ({ ...f, description }));
                            }}
                        />
                        <MultiSelect
                            label={t('Groups')}
                            description={t('Groups whose members can see and subscribe to the feed')}
                            placeholder={t('Search')}
                            searchable
                            clearable
                            nothingFoundMessage={t('Nothing found...')}
                            data={groupOptions}
                            value={form.groups}
                            onChange={(groups) => setForm((f) => ({ ...f, groups }))}
                        />
                        <Select
                            required
                            label={t('Default role')}
                            description={t('The role new subscribers get')}
                            allowDeselect={false}
                            data={roleOptions}
                            value={form.default_role}
                            onChange={(value) => setForm((f) => ({ ...f, default_role: value ?? f.default_role }))}
                        />
                        <Select
                            required
                            searchable
                            label={t('Creator')}
                            description={t('The callsign of the device that owns this feed')}
                            placeholder={t('Search')}
                            nothingFoundMessage={t('Nothing found...')}
                            data={devices}
                            value={form.creator_uid}
                            onChange={(value) => setForm((f) => ({ ...f, creator_uid: value }))}
                        />
                        <PasswordInput
                            label={t('Password')}
                            description={t('Optional. Devices must enter it to subscribe')}
                            value={form.password}
                            onChange={(e) => {
                                const password = e.currentTarget.value;
                                setForm((f) => ({ ...f, password }));
                            }}
                        />
                        <Group justify="flex-end" mt="sm">
                            <Button variant="default" onClick={closeForm}>{t('Cancel')}</Button>
                            <Button
                                type="submit"
                                loading={saving}
                                leftSection={editing ? <Save20Regular /> : <Add20Regular />}
                            >
                                {editing ? t('Save changes') : t('Create feed')}
                            </Button>
                        </Group>
                    </Stack>
                </form>
            </Modal>

            {/* QR code */}
            <Modal opened={qrMission !== null} onClose={() => setQrMission(null)} title={qrMission?.name}>
                <Stack align="center" gap="md">
                    <Text size="sm" c="dimmed" ta="center">{t('Scan with ATAK to subscribe to this feed.')}</Text>
                    <Center>
                        <Box p="xs" bg="#fff" style={{ borderRadius: 'var(--mantine-radius-md)' }}>
                            <QRCode
                                value={qrMission?.qr_code ?? ''}
                                size={350}
                                ecLevel="L"
                                quietZone={10}
                                qrStyle="dots"
                                eyeRadius={10}
                            />
                        </Box>
                    </Center>
                </Stack>
            </Modal>

            {/* Invite */}
            <Modal
                opened={inviteMission !== null}
                onClose={() => setInviteMission(null)}
                title={inviteMission ? t('Invite a device to {{name}}', { name: inviteMission.name, ...NO_ESCAPE }) : ''}
            >
                <Stack gap="sm">
                    <Select
                        searchable
                        label={t('Callsign')}
                        placeholder={t('Search')}
                        nothingFoundMessage={t('Nothing found...')}
                        allowDeselect={false}
                        data={devices}
                        value={inviteUid}
                        onChange={setInviteUid}
                    />
                    {!admin && (
                        <PasswordInput
                            label={t('Password')}
                            disabled={!inviteMission?.passwordProtected}
                            value={invitePassword}
                            onChange={(e) => setInvitePassword(e.currentTarget.value)}
                        />
                    )}
                    <Group justify="flex-end" mt="sm">
                        <Button variant="default" onClick={() => setInviteMission(null)}>{t('Cancel')}</Button>
                        <Button
                            leftSection={<Mail20Regular />}
                            loading={inviting}
                            disabled={!inviteUid}
                            onClick={sendInvite}
                        >
                            {t('Invite')}
                        </Button>
                    </Group>
                </Stack>
            </Modal>

            {/* Delete */}
            <Modal opened={deleteMission !== null} onClose={() => setDeleteMission(null)} title={t('Delete feed')}>
                <Text size="sm">
                    {deleteMission
                        ? t("Delete {{name}}? Subscribed devices stop syncing it. This can't be undone.", {
                            name: deleteMission.name,
                            ...NO_ESCAPE,
                        })
                        : ''}
                </Text>
                <Group justify="flex-end" mt="md">
                    <Button variant="default" onClick={() => setDeleteMission(null)}>{t('Cancel')}</Button>
                    <Button color="red" leftSection={<Delete20Regular />} loading={deleting} onClick={confirmDelete}>
                        {t('Delete')}
                    </Button>
                </Group>
            </Modal>
        </>
    );
}
