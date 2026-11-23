import React, { useEffect, useMemo, useState } from 'react';
import {
    Alert,
    Badge,
    Divider,
    Group,
    SegmentedControl,
    Select,
    SimpleGrid,
    Skeleton,
    Stack,
    Table,
    Text,
    TextInput,
} from '@mantine/core';
import {
    Box20Regular,
    DocumentMultiple20Regular,
    ErrorCircle20Regular,
    Eye20Regular,
    Phone20Regular,
    PuzzlePiece20Regular,
    Settings20Regular,
} from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import axios from '../../axios_config';
import { Panel } from '@/components/ui';
import { findSetting } from './settingsCatalog';
import { apiErrorMessage, formatBytes, ON_CONNECT_KEY } from './types';
import classes from './DeviceProfiles.module.css';

/** GET ?username=&eud_uid=&phase=enrollment|connection|tool[&tool=] → PreviewResponse */
export const DEVICE_PROFILE_PREVIEW_URL = '/api/device-profiles/preview';
const USERS_URL = '/api/users/all';
const EUDS_URL = '/api/eud';

type Phase = 'enrollment' | 'connection' | 'tool';

interface PreviewSetting {
    key: string;
    value: string;
    value_class: string;
    profile_id: number | null;
    profile_name: string | null;
}

interface PreviewFile {
    id: number;
    filename: string;
    zip_path: string;
    size: number;
    profile_id: number | null;
    profile_name: string | null;
}

interface PreviewResponse {
    settings: PreviewSetting[];
    files?: PreviewFile[];
    data_packages: { name: string }[];
    plugins: { name: string }[];
}

interface UserOption {
    username: string;
    euds?: { uid: string }[];
}

interface EudOption {
    uid: string;
    callsign: string | null;
}

function displayValue(setting: PreviewSetting, sensitive: boolean, t: (k: string) => string): string {
    if (sensitive) { return '••••••'; }
    if (setting.value_class === 'class java.lang.Boolean') {
        return setting.value === 'true' ? t('On') : t('Off');
    }
    const def = findSetting(setting.key);
    const option = def?.options?.find((o) => o.value === setting.value);
    if (option) { return option.label; }
    if (setting.value === '' || setting.value === null || setting.value === undefined) { return '—'; }
    return def?.unit ? `${setting.value} ${def.unit}` : setting.value;
}

/** Small heading inside the results panel: icon, title, count. */
function ListHeading({ icon, title, count }: { icon: React.ReactNode; title: string; count: number }) {
    return (
        <Group gap="xs" mb="xs">
            <span className={classes.icon}>{icon}</span>
            <Text fw={600} size="sm">{title}</Text>
            <Badge size="sm" variant="light" color="gray">{count}</Badge>
        </Group>
    );
}

function NameList({ icon, title, items, empty }: {
    icon: React.ReactNode;
    title: string;
    items: { name: string }[];
    empty: string;
}) {
    return (
        <div>
            <ListHeading icon={icon} title={title} count={items.length} />
            {items.length ? (
                <Stack gap={4}>
                    {items.map((item) => (
                        <Text key={item.name} size="sm" className={classes.mono}>{item.name}</Text>
                    ))}
                </Stack>
            ) : (
                <Text size="sm" c="dimmed">{empty}</Text>
            )}
        </div>
    );
}

/** :param tools: tool names used by profiles, for the tool picker */
export default function PreviewPanel({ tools = [] }: { tools?: string[] }) {
    const { t } = useTranslation();
    const [users, setUsers] = useState<UserOption[]>([]);
    const [usersUnavailable, setUsersUnavailable] = useState(false);
    const [euds, setEuds] = useState<EudOption[]>([]);
    const [username, setUsername] = useState<string | null>(null);
    const [eudUid, setEudUid] = useState<string | null>(null);
    const [phase, setPhase] = useState<Phase>('enrollment');
    const [tool, setTool] = useState<string | null>(null);
    const [data, setData] = useState<PreviewResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        axios.get<UserOption[]>(USERS_URL)
            .then((r) => setUsers(Array.isArray(r.data) ? r.data : []))
            // e.g. LDAP mode, where /api/users/all is refused: fall back to typing a username
            .catch(() => setUsersUnavailable(true));
        axios.get<EudOption[]>(EUDS_URL, { params: { all: true } })
            .then((r) => setEuds(Array.isArray(r.data) ? r.data : []))
            .catch(() => setEuds([]));
    }, []);

    const userOptions = useMemo(
        () => users.map((u) => ({ value: u.username, label: u.username })).sort((a, b) => a.label.localeCompare(b.label)),
        [users],
    );

    const deviceOptions = useMemo(() => {
        const owned = username ? users.find((u) => u.username === username)?.euds : undefined;
        const ownedUids = owned ? new Set(owned.map((e) => e.uid)) : null;
        return euds
            .filter((e) => !ownedUids || ownedUids.has(e.uid))
            .map((e) => ({ value: e.uid, label: e.callsign ? `${e.callsign} (${e.uid})` : e.uid }))
            .sort((a, b) => a.label.localeCompare(b.label));
    }, [euds, users, username]);

    // Drop a device that doesn't belong to the newly chosen user
    useEffect(() => {
        if (eudUid && !deviceOptions.some((o) => o.value === eudUid)) { setEudUid(null); }
    }, [deviceOptions, eudUid]);

    const hasSubject = !!(username || eudUid);

    // Keep the tool picker pointing at a tool that still exists
    useEffect(() => {
        if (tool && !tools.includes(tool)) { setTool(tools[0] ?? null); }
        if (!tool && tools.length) { setTool(tools[0]); }
    }, [tools, tool]);
    const waitingForTool = phase === 'tool' && !tool;

    useEffect(() => {
        if (!hasSubject || waitingForTool) {
            setData(null);
            setError(null);
            return undefined;
        }
        let cancelled = false;
        setLoading(true);
        setError(null);
        const params: Record<string, string> = { phase };
        if (username) { params.username = username; }
        if (eudUid) { params.eud_uid = eudUid; }
        if (phase === 'tool' && tool) { params.tool = tool; }
        axios.get<PreviewResponse>(DEVICE_PROFILE_PREVIEW_URL, { params })
            .then((r) => { if (!cancelled) { setData(r.data); } })
            .catch((err) => {
                if (!cancelled) {
                    setData(null);
                    setError(apiErrorMessage(err) ?? t('Could not load the preview.'));
                }
            })
            .finally(() => { if (!cancelled) { setLoading(false); } });
        return () => { cancelled = true; };
    }, [username, eudUid, phase, tool, hasSubject, waitingForTool, t]);

    const settings = useMemo(() => [...(data?.settings ?? [])].sort((a, b) => {
        const la = findSetting(a.key)?.label ?? a.key;
        const lb = findSetting(b.key)?.label ?? b.key;
        return la.localeCompare(lb);
    }), [data]);

    return (
        <Stack gap="md">
            <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm">
                {usersUnavailable ? (
                    <TextInput
                        label={t('User')}
                        placeholder={t('Username')}
                        value={username ?? ''}
                        onChange={(e) => setUsername(e.currentTarget.value.trim() || null)}
                    />
                ) : (
                    <Select
                        label={t('User')}
                        placeholder={t('Pick a user')}
                        data={userOptions}
                        value={username}
                        onChange={setUsername}
                        searchable
                        clearable
                        nothingFoundMessage={t('No users found')}
                    />
                )}
                <Select
                    label={t('Device (optional)')}
                    placeholder={deviceOptions.length ? t('Any device') : t('No devices')}
                    data={deviceOptions}
                    value={eudUid}
                    onChange={setEudUid}
                    searchable
                    clearable
                    disabled={!deviceOptions.length}
                    nothingFoundMessage={t('No devices found')}
                />
                <Stack gap={4}>
                    <Text size="sm" fw={500}>{t('When')}</Text>
                    <SegmentedControl
                        fullWidth
                        value={phase}
                        onChange={(v) => setPhase(v as Phase)}
                        data={[
                            { value: 'enrollment', label: t('On enrollment') },
                            { value: 'connection', label: t('Every connection') },
                            { value: 'tool', label: t('Tool') },
                        ]}
                    />
                </Stack>
            </SimpleGrid>

            {phase === 'tool' && (
                tools.length ? (
                    <Select
                        label={t('Tool')}
                        description={t('What a device gets from /Marti/api/device/profile/tool/<name>. Nothing at all (204) if no profile for the tool applies.')}
                        data={tools}
                        value={tool}
                        onChange={setTool}
                        allowDeselect={false}
                        maw={360}
                    />
                ) : (
                    <Text size="sm" c="dimmed">{t('No profile has a tool name yet.')}</Text>
                )
            )}

            {phase === 'connection' && (
                <Text size="xs" c="dimmed">
                    {t('Devices only fetch these when "{{setting}}" ({{key}}) is on in ATAK. Devices enrolled through this server have it switched on automatically. A device only downloads a profile again after it has been changed.', { setting: findSetting(ON_CONNECT_KEY)?.label ?? ON_CONNECT_KEY, key: ON_CONNECT_KEY, interpolation: { escapeValue: false } })}
                </Text>
            )}

            {!hasSubject ? (
                <Panel p="xl">
                    <Stack align="center" gap="xs" ta="center">
                        <span className={classes.icon}><Eye20Regular /></span>
                        <Text fw={600}>{t('Pick a user or a device')}</Text>
                        <Text size="sm" c="dimmed" maw={460}>
                            {t('See exactly which settings, data packages and plugins it would receive, and which profile each setting comes from.')}
                        </Text>
                    </Stack>
                </Panel>
            ) : error ? (
                <Alert color="red" icon={<ErrorCircle20Regular />} title={t('Preview failed')}>{error}</Alert>
            ) : loading && !data ? (
                <Stack gap="xs">
                    <Skeleton height={36} />
                    <Skeleton height={36} />
                    <Skeleton height={36} />
                </Stack>
            ) : data ? (
                <Stack gap="md" style={{ opacity: loading ? 0.6 : 1 }}>
                    <Panel>
                        <ListHeading icon={<Settings20Regular />} title={t('Settings')} count={settings.length} />
                        {settings.length ? (
                            <Table.ScrollContainer minWidth={320}>
                                <Table verticalSpacing="xs" highlightOnHover>
                                    <Table.Thead>
                                        <Table.Tr>
                                            <Table.Th>{t('Setting')}</Table.Th>
                                            <Table.Th>{t('Value')}</Table.Th>
                                            <Table.Th>{t('From profile')}</Table.Th>
                                        </Table.Tr>
                                    </Table.Thead>
                                    <Table.Tbody>
                                        {settings.map((s) => {
                                            const def = findSetting(s.key);
                                            return (
                                                <Table.Tr key={s.key}>
                                                    <Table.Td>
                                                        <Text size="sm" fw={500} style={{ wordBreak: 'break-word' }}>{def?.label ?? s.key}</Text>
                                                        {def && <Text size="xs" c="dimmed" ff="monospace" className={classes.mono}>{s.key}</Text>}
                                                    </Table.Td>
                                                    <Table.Td>
                                                        <Text size="sm" className={classes.mono}>
                                                            {displayValue(s, !!def?.sensitive, t)}
                                                        </Text>
                                                    </Table.Td>
                                                    <Table.Td>
                                                        {s.profile_name ? (
                                                            <Badge variant="light" color="blue" maw={160} style={{ textTransform: 'none' }}>
                                                                {s.profile_name}
                                                            </Badge>
                                                        ) : (
                                                            <Text size="xs" c="dimmed">—</Text>
                                                        )}
                                                    </Table.Td>
                                                </Table.Tr>
                                            );
                                        })}
                                    </Table.Tbody>
                                </Table>
                            </Table.ScrollContainer>
                        ) : (
                            <Text size="sm" c="dimmed">
                                    {phase === 'enrollment'
                                        ? t('No profile settings are sent on enrollment for this selection.')
                                        : phase === 'tool'
                                            ? t('No profile settings for this tool apply to this selection.')
                                            : t('No profile settings apply on connection for this selection.')}
                            </Text>
                        )}

                        <Divider my="md" />
                        <ListHeading icon={<DocumentMultiple20Regular />} title={t('Profile files')} count={data.files?.length ?? 0} />
                        {data.files?.length ? (
                            <Stack gap={6}>
                                {data.files.map((f) => (
                                    <Group key={f.zip_path} gap="sm" justify="space-between" wrap="nowrap">
                                        <div style={{ minWidth: 0 }}>
                                            <Text size="sm" ff="monospace" className={classes.mono}>{f.zip_path}</Text>
                                            <Text size="xs" c="dimmed">{formatBytes(f.size)}</Text>
                                        </div>
                                        {f.profile_name && (
                                            <Badge variant="light" color="blue" maw={160} style={{ textTransform: 'none', flexShrink: 0 }}>
                                                {f.profile_name}
                                            </Badge>
                                        )}
                                    </Group>
                                ))}
                            </Stack>
                        ) : (
                            <Text size="sm" c="dimmed">{t('No profile files go in at this point.')}</Text>
                        )}

                        {phase !== 'tool' && (<>
                            <Divider my="md" />
                            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                                <NameList
                                    icon={<Box20Regular />}
                                    title={t('Data packages')}
                                    items={data.data_packages ?? []}
                                    empty={t('No data packages are sent at this point.')}
                                />
                                <NameList
                                    icon={<PuzzlePiece20Regular />}
                                    title={t('Plugins')}
                                    items={data.plugins ?? []}
                                    empty={t('No plugins are sent at this point.')}
                                />
                            </SimpleGrid>
                        </>)}
                    </Panel>

                    <Group gap={6}>
                        <span className={classes.icon}><Phone20Regular /></span>
                        <Text size="xs" c="dimmed">
                            {phase === 'tool'
                                ? t('A tool profile carries only its own settings and files.')
                                : t('The server also adds its own connection details (update server, certificates) to every profile.')}
                        </Text>
                    </Group>
                </Stack>
            ) : null}
        </Stack>
    );
}
