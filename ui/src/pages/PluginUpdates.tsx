import { useEffect, useState } from 'react';
import {
    ActionIcon, Badge, Box, Button, Center, FileInput, Group, Image, Modal, Pagination, Select, Stack, Switch, Text,
    TextInput, ThemeIcon,
} from '@mantine/core';
import { DataTable } from 'mantine-datatable';
import { useTranslation } from 'react-i18next';
import axios from 'axios';
import {
    ArrowClockwise20Regular, ArrowUpload20Regular, Delete20Regular, Image20Regular, PuzzlePiece20Regular,
} from '@fluentui/react-icons';
import { apiRoutes } from '@/apiRoutes';
import { PageHeader, Panel } from '@/components/ui';
import { ReadOnlyNotice } from '@/components/ReadOnlyNotice';
import { ReadOnlyTip } from '@/components/ReadOnlyTip';
import { isReadOnlyAdmin } from '@/adminAccess';
import { EmptyState, ICON_16 } from './ServerRequests/shared';
import { errorText, flattenedErrors, NO_ESCAPE, notifyFailure, notifySuccess } from './pageUtils';

interface PluginPackage {
    name: string;
    description: string | null;
    package_name: string;
    icon: string | null;
    version: string | null;
    revision_code: string | number | null;
    atak_version: string | null;
    platform: string | null;
    os_requirement: string | null;
    install_on_enrollment: boolean;
    install_on_connection: boolean;
}

type InstallFlag = 'install_on_enrollment' | 'install_on_connection';

const ATAK_VERSIONS = ['5.5.0', '5.5.1', '5.6.0', '5.7.0', '5.8.0'];

export default function PluginUpdates() {
    const { t } = useTranslation();
    const readOnly = isReadOnlyAdmin();

    const [plugins, setPlugins] = useState<PluginPackage[]>([]);
    const [page, setPage] = useState(1);
    const [totalPages, setTotalPages] = useState(1);
    const [loading, setLoading] = useState(true);

    const [uploadOpen, setUploadOpen] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [pluginType, setPluginType] = useState('plugin');
    const [apk, setApk] = useState<File | null>(null);
    const [icon, setIcon] = useState<File | null>(null);
    const [description, setDescription] = useState('');
    const [atakVersion, setAtakVersion] = useState('');

    const [deletePlugin, setDeletePlugin] = useState<PluginPackage | null>(null);

    function getPlugins() {
        setLoading(true);
        axios.get(apiRoutes.pluginPackages, { params: { page } })
            .then((r) => {
                if (r.status === 200) {
                    setPlugins(r.data.results ?? []);
                    setTotalPages(r.data.total_pages ?? 1);
                    setPage(r.data.current_page ?? 1);
                }
            })
            .catch((err) => notifyFailure(t('Couldn\'t load plugin data'), errorText(err)))
            .finally(() => setLoading(false));
    }

    useEffect(() => {
        getPlugins();
    }, [page]);

    function setInstall(p: PluginPackage, flag: InstallFlag, value: boolean) {
        axios.patch(apiRoutes.pluginPackages, {
            package_name: p.package_name,
            install_on_enrollment: flag === 'install_on_enrollment' ? value : p.install_on_enrollment,
            install_on_connection: flag === 'install_on_connection' ? value : p.install_on_connection,
            atak_version: p.atak_version,
        }).then((r) => {
            if (r.status === 200) { getPlugins(); }
        }).catch((err) => notifyFailure(t('Couldn\'t save the package'), errorText(err)));
    }

    function openUpload() {
        if (!readOnly) { setUploadOpen(true); }
    }

    function upload(e: React.FormEvent) {
        e.preventDefault();
        const body = new FormData();
        if (apk) { body.append('apk', apk); }
        body.append('icon', apk && icon ? icon : new File([], ''));
        body.append('description', description);
        body.append('platform', 'Android');
        body.append('plugin_type', pluginType);
        body.append('atak_version', atakVersion);
        setUploading(true);
        axios.post(apiRoutes.pluginPackages, body, { headers: { 'Content-Type': 'multipart/form-data' } })
            .then((r) => {
                if (r.status === 200) {
                    notifySuccess(t('Plugin uploaded'));
                    getPlugins();
                    setUploadOpen(false);
                }
            })
            .catch((err) => notifyFailure(t('Couldn\'t upload the plugin'), flattenedErrors(err)))
            .finally(() => setUploading(false));
    }

    function confirmDelete() {
        const p = deletePlugin;
        if (!p) { return; }
        // The same package can be uploaded once per ATAK version: name both so only this row goes
        axios.delete(apiRoutes.pluginPackages, {
            params: { package_name: p.package_name, atak_version: p.atak_version ?? '' },
        })
            .then((r) => {
                if (r.status === 200) {
                    notifySuccess(t('Plugin deleted'));
                    getPlugins();
                }
            })
            .catch((err) => notifyFailure(t('Couldn\'t delete the plugin'), errorText(err)))
            .finally(() => setDeletePlugin(null));
    }

    function installSwitch(p: PluginPackage, flag: InstallFlag, title: string) {
        return (
            <Center>
                <Switch
                    aria-label={`${title}: ${p.name}`}
                    checked={!!p[flag]}
                    disabled={readOnly}
                    onChange={(e) => setInstall(p, flag, e.currentTarget.checked)}
                />
            </Center>
        );
    }

    const uploadButton = (
        <ReadOnlyTip>
            <Button leftSection={<ArrowUpload20Regular />} disabled={readOnly} onClick={openUpload}>
                {t('Upload Plugin')}
            </Button>
        </ReadOnlyTip>
    );

    return (
        <>
            <PageHeader
                title={t('Plugins')}
                description={t('ATAK plugin and app APKs this server offers to devices through its plugin update repository')}
                actions={(
                    <>
                        <Button
                            variant="default"
                            leftSection={<ArrowClockwise20Regular />}
                            loading={loading}
                            onClick={getPlugins}
                        >
                            {t('Refresh')}
                        </Button>
                        {uploadButton}
                    </>
                )}
            />
            <ReadOnlyNotice />

            <Panel p={0}>
                <DataTable
                    backgroundColor="transparent"
                    borderColor="var(--raven-border)"
                    rowBorderColor="var(--raven-border)"
                    highlightOnHover
                    verticalSpacing="sm"
                    records={plugins}
                    idAccessor={(p) => `${p.package_name}|${p.atak_version ?? ''}`}
                    columns={[
                        {
                            accessor: 'name',
                            title: t('Name'),
                            render: (p) => (
                                <Group gap="sm" wrap="nowrap">
                                    {p.icon
                                        ? <Image src={p.icon} alt="" w={32} h={32} fit="contain" />
                                        : (
                                            <ThemeIcon size={32} variant="light" color="gray">
                                                <PuzzlePiece20Regular />
                                            </ThemeIcon>
                                        )}
                                    <Box miw={0}>
                                        <Text size="sm" fw={600} truncate>{p.name}</Text>
                                        <Text size="xs" c="dimmed" lineClamp={2}>{p.description || p.package_name}</Text>
                                    </Box>
                                </Group>
                            ),
                        },
                        {
                            accessor: 'version',
                            title: t('Version'),
                            render: (p) => (
                                <Stack gap={0}>
                                    <Text size="sm">{p.version}</Text>
                                    {p.revision_code !== undefined && p.revision_code !== null && p.revision_code !== '' && (
                                        <Text size="xs" c="dimmed">{t('Revision Code')}: {p.revision_code}</Text>
                                    )}
                                </Stack>
                            ),
                        },
                        {
                            accessor: 'atak_version',
                            title: t('ATAK Version'),
                            render: (p) => (p.atak_version
                                ? <Badge color="gray" variant="light">{p.atak_version}</Badge>
                                : <Text size="sm" c="dimmed">{t('5.4 or lower')}</Text>),
                        },
                        {
                            accessor: 'platform',
                            title: t('Platform'),
                            render: (p) => (
                                <Stack gap={0}>
                                    <Text size="sm">{p.platform}</Text>
                                    {p.os_requirement && (
                                        <Text size="xs" c="dimmed">{t('OS Requirement')}: {p.os_requirement}</Text>
                                    )}
                                </Stack>
                            ),
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
                            title: '',
                            width: 64,
                            textAlign: 'right',
                            render: (p) => (
                                <ReadOnlyTip>
                                    <ActionIcon
                                        variant="subtle"
                                        color="red"
                                        aria-label={t('Delete')}
                                        disabled={readOnly}
                                        onClick={() => { if (!readOnly) { setDeletePlugin(p); } }}
                                    >
                                        <Delete20Regular style={ICON_16} />
                                    </ActionIcon>
                                </ReadOnlyTip>
                            ),
                        },
                    ]}
                    fetching={loading}
                    minHeight={plugins.length === 0 ? 320 : undefined}
                    emptyState={loading ? <></> : (
                        <EmptyState
                            icon={<PuzzlePiece20Regular />}
                            title={t('No plugins yet')}
                            description={t('Upload an ATAK plugin APK to offer it to devices, on enrollment, on connection, or from the ATAK plugin update repository.')}
                            action={uploadButton}
                        />
                    )}
                />
                {totalPages > 1 && (
                    <Group justify="flex-end" p="sm" style={{ borderTop: '1px solid var(--raven-border)' }}>
                        <Pagination size="sm" withEdges total={totalPages} value={page} onChange={setPage} />
                    </Group>
                )}
            </Panel>

            {/* Upload */}
            <Modal opened={uploadOpen && !readOnly} onClose={() => setUploadOpen(false)} title={t('Upload Plugin')}>
                <form onSubmit={upload}>
                    <Stack gap="sm">
                        <Text size="sm" c="dimmed">
                            {t('The APK is offered to devices through the plugin update repository. Pick the ATAK version it was built against so only compatible devices see it.')}
                        </Text>
                        <Select
                            label={t('Plugin Type')}
                            allowDeselect={false}
                            value={pluginType}
                            onChange={(v) => setPluginType(v ?? 'plugin')}
                            data={[
                                { value: 'plugin', label: t('Plugin') },
                                { value: 'app', label: t('App') },
                            ]}
                        />
                        <FileInput
                            required
                            label={t('Plugin File')}
                            placeholder={t('Choose an .apk file')}
                            accept="application/vnd.android.package-archive"
                            leftSection={<PuzzlePiece20Regular style={ICON_16} />}
                            value={apk}
                            onChange={setApk}
                        />
                        <TextInput
                            label={t('Description')}
                            value={description}
                            onChange={(e) => setDescription(e.currentTarget.value)}
                        />
                        <FileInput
                            label={t('Icon')}
                            placeholder={t('Optional')}
                            accept="image/*"
                            leftSection={<Image20Regular style={ICON_16} />}
                            value={icon}
                            onChange={setIcon}
                        />
                        <Select
                            label={t('ATAK Version')}
                            clearable
                            value={atakVersion}
                            onChange={(v) => setAtakVersion(!v || v === 'Any' ? '' : v)}
                            data={[
                                { value: '', label: t('5.4 or lower') },
                                ...ATAK_VERSIONS.map((v) => ({ value: v, label: v })),
                            ]}
                        />
                        <Group justify="flex-end" mt="sm">
                            <Button variant="default" onClick={() => setUploadOpen(false)}>{t('Cancel')}</Button>
                            <Button type="submit" loading={uploading} leftSection={<ArrowUpload20Regular />}>
                                {t('Upload Plugin')}
                            </Button>
                        </Group>
                    </Stack>
                </form>
            </Modal>

            {/* Delete */}
            <Modal
                opened={deletePlugin !== null && !readOnly}
                onClose={() => setDeletePlugin(null)}
                title={t('Delete plugin')}
            >
                <Text size="sm">
                    {deletePlugin
                        ? t('Are you sure you want to delete {{deleteName}}?', { deleteName: deletePlugin.name, ...NO_ESCAPE })
                        : ''}
                </Text>
                <Text size="sm" c="dimmed" mt="xs">
                    {t('Devices will no longer be offered this plugin. Copies already installed stay on the device.')}
                </Text>
                <Group justify="flex-end" mt="md">
                    <Button variant="default" onClick={() => setDeletePlugin(null)}>{t('Cancel')}</Button>
                    <Button color="red" leftSection={<Delete20Regular />} onClick={confirmDelete}>{t('Delete')}</Button>
                </Group>
            </Modal>
        </>
    );
}
