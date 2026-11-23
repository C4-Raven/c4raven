import { useEffect, useState } from 'react';
import {
    Alert,
    Anchor,
    Button,
    Center,
    Group,
    LoadingOverlay,
    Modal,
    Select,
    Stack,
    Table,
    Text,
    Title,
} from '@mantine/core';
import {
    ArrowDownload16Regular,
    Checkmark20Regular,
    Dismiss20Regular,
    LinkDismiss20Regular,
    LockClosed20Regular,
} from '@fluentui/react-icons';
import { notifications } from '@mantine/notifications';
import axios from 'axios';
import { t } from 'i18next';
import { apiRoutes } from '../apiRoutes';
import bytes_formatter from '../bytes_formatter';
import { TakGovLogo } from '../images/takgov_logo';
import { isReadOnlyAdmin } from '../adminAccess';
import { PageHeader, Panel } from '../components/ui';
import { ReadOnlyNotice } from '../components/ReadOnlyNotice';
import { ReadOnlyTip } from '../components/ReadOnlyTip';

export interface TakGovPlugin {
    apk_hash: string;
    apk_size_bytes: number;
    apk_type: string;
    apk_url: string;
    description: string;
    display_name: string;
    icon_url: string;
    identifier: string;
    os_requirement: string;
    package_name: string;
    platform: string;
    revision_code: number;
    tak_prerequisite: string;
    version: string;
    product?: string;
}

const TAKGOV_DEVICE_PAGE = 'https://tak.gov/register-device';
const ALL_PLUGINS = 'All Plugins';
// Every account has the CIV products. FMS-case products (AUS, MNWG, ...) are added when
// the server finds the linked account really has them (GET /api/takgov/products).
const CIV_PRODUCTS = ['ATAK-CIV', 'WinTAK-CIV', 'TAKX-CIV'];

/** One row per plugin build: TAK.gov repeats CIV plugins under other products. */
function pluginKey(p: TakGovPlugin) {
    return `${p.package_name}|${p.version}|${p.revision_code}`;
}
const VERSIONS = ['5.0.0', '5.1.0', '5.2.0', '5.3.0', '5.4.0', '5.5.0', '5.5.1', '5.6.0', '5.7.0', '5.8.0'];

function notifyError(title: string, err: any) {
    notifications.show({ title, message: err?.response?.data?.error, color: 'red', icon: <Dismiss20Regular /> });
}

function notifySuccess(message: string) {
    notifications.show({ title: t('Success'), message, color: 'green', icon: <Checkmark20Regular /> });
}

/** Links the server to a TAK.gov account (device-code flow) and downloads TAK.gov plugins onto the server. */
export default function LinkTakGov() {
    const readOnly = isReadOnlyAdmin();
    // Requests can overlap (status check, plugin list, a download), so count them.
    const [pending, setPending] = useState(0);
    const [linked, setLinked] = useState(false);
    const [deviceCode, setDeviceCode] = useState('');
    const [userCode, setUserCode] = useState('');
    const [product, setProduct] = useState('ATAK-CIV');
    const [products, setProducts] = useState<string[]>(CIV_PRODUCTS);
    const [version, setVersion] = useState('5.5.0');
    const [plugins, setPlugins] = useState<TakGovPlugin[]>([]);
    const [unlinkOpen, setUnlinkOpen] = useState(false);

    function track<T>(request: Promise<T>): Promise<T> {
        setPending((n) => n + 1);
        return request.finally(() => setPending((n) => n - 1));
    }

    useEffect(() => {
        track(axios.get(apiRoutes.takgov))
            .then((r) => {
                if (r.status === 200) {
                    setLinked(!!r.data.tak_gov_account_linked);
                }
            })
            .catch((err) => notifyError(t('Couldn\'t check the TAK.gov link'), err));
    }, []);

    // Which products this account can use, for the chosen version
    useEffect(() => {
        if (!linked || readOnly) {
            return;
        }
        let cancelled = false;
        track(axios.get(apiRoutes.takgovProducts, { params: { product_version: version } }))
            .then((r) => {
                if (cancelled) {
                    return;
                }
                const list: string[] = Array.isArray(r.data?.products) && r.data.products.length ? r.data.products : CIV_PRODUCTS;
                setProducts(list);
                setProduct((current) => (current === ALL_PLUGINS || list.includes(current) ? current : 'ATAK-CIV'));
            })
            .catch(() => {
                if (!cancelled) {
                    setProducts(CIV_PRODUCTS);
                    setProduct((current) => (current === ALL_PLUGINS || CIV_PRODUCTS.includes(current) ? current : 'ATAK-CIV'));
                }
            });
        return () => {
            cancelled = true;
        };
    }, [linked, version]);

    useEffect(() => {
        if (!linked || readOnly) {
            return;
        }
        if (product === ALL_PLUGINS) {
            const requests = products.map((p) =>
                axios
                    .get(apiRoutes.takgovPlugins, { params: { product: p, product_version: version } })
                    .then((r) =>
                        Array.isArray(r.data)
                            ? (r.data as TakGovPlugin[]).map((plugin) => ({ ...plugin, product: p }))
                            : [],
                    )
                    .catch(() => [] as TakGovPlugin[]),
            );
            track(Promise.all(requests)).then((lists) => {
                // CIV first, so a plugin repeated under an FMS product keeps its CIV row only
                const seen = new Set<string>();
                setPlugins(lists.flat().filter((plugin) => {
                    const key = pluginKey(plugin);
                    if (seen.has(key)) {
                        return false;
                    }
                    seen.add(key);
                    return true;
                }));
            });
        } else {
            track(axios.get(apiRoutes.takgovPlugins, { params: { product, product_version: version } }))
                .then((r) => {
                    // TAK.gov can answer 200 with an error object instead of a list.
                    if (Array.isArray(r.data)) {
                        setPlugins(r.data);
                    } else {
                        setPlugins([]);
                        notifications.show({
                            title: t('Couldn\'t load the TAK.gov plugin list'),
                            message: r.data?.error ?? r.data?.message,
                            color: 'red',
                            icon: <Dismiss20Regular />,
                        });
                    }
                })
                .catch((err) => {
                    setPlugins([]);
                    notifyError(t('Couldn\'t load the TAK.gov plugin list'), err);
                });
        }
    }, [linked, product, version, products]);

    const getLinkCode = () => {
        track(axios.get(apiRoutes.takgovLink))
            .then((r) => {
                if (r.status === 200) {
                    setDeviceCode(r.data.device_code);
                    setUserCode(r.data.user_code);
                }
            })
            .catch((err) => notifyError(t('Couldn\'t get a code from TAK.gov'), err));
    };

    const linkAccount = () => {
        track(axios.get(apiRoutes.takgovToken, { params: { device_code: deviceCode } }))
            .then((r) => {
                if (r.status !== 200) {
                    return;
                }
                // While the code is still waiting to be entered on TAK.gov the server answers 200 with success: false.
                if (r.data?.success === false) {
                    notifications.show({
                        title: t('Account not linked yet'),
                        message: t('TAK.gov has not accepted the code yet. Enter it there, then press Finish linking again.'),
                        color: 'yellow',
                    });
                    return;
                }
                setLinked(true);
                notifySuccess(t('TAK.gov account linked'));
            })
            .catch((err) => notifyError(t('Couldn\'t finish linking with TAK.gov'), err));
    };

    const unlink = () => {
        setUnlinkOpen(false);
        track(axios.delete(apiRoutes.takgov))
            .then((r) => {
                if (r.status === 200) {
                    setLinked(false);
                    setUserCode('');
                    setDeviceCode('');
                    setPlugins([]);
                    notifySuccess(t('TAK.gov account unlinked'));
                }
            })
            .catch((err) => notifyError(t('Couldn\'t unlink the TAK.gov account'), err));
    };

    const download = (plugin: TakGovPlugin) => {
        track(axios.post(apiRoutes.takgovPlugin, { ...plugin, atak_version: version }))
            .then((r) => {
                if (r.status === 200) {
                    notifySuccess(t('Plugin downloaded to the server'));
                }
            })
            .catch((err) => notifyError(t('Couldn\'t download the plugin'), err));
    };

    const allMode = product === ALL_PLUGINS;

    const linkedView = (
        <>
            <Group gap="sm" mb="md">
                <ReadOnlyTip>
                    <Select
                        searchable
                        aria-label={t('Product')}
                        w="max-content"
                        allowDeselect={false}
                        disabled={readOnly}
                        data={[ALL_PLUGINS, ...products]}
                        value={product}
                        onChange={(v) => v && setProduct(v)}
                    />
                </ReadOnlyTip>
                <ReadOnlyTip>
                    <Select
                        aria-label={t('Version')}
                        w="max-content"
                        allowDeselect={false}
                        disabled={readOnly}
                        data={VERSIONS}
                        value={version}
                        onChange={(v) => v && setVersion(v)}
                    />
                </ReadOnlyTip>
            </Group>
            {readOnly ? (
                <Alert variant="light" color="gray" icon={<LockClosed20Regular />}>
                    {t('Only a full administrator can view this.')}
                </Alert>
            ) : (
                <Panel p={0}>
                    <Table.ScrollContainer minWidth={700}>
                        <Table highlightOnHover borderColor="var(--raven-border)">
                            <Table.Thead>
                                <Table.Tr>
                                    {allMode && <Table.Th>{t('Product')}</Table.Th>}
                                    <Table.Th>{t('Name')}</Table.Th>
                                    <Table.Th>{t('Description')}</Table.Th>
                                    <Table.Th>{t('Version')}</Table.Th>
                                    <Table.Th>{t('TAK Prerequisite')}</Table.Th>
                                    <Table.Th>{t('Size')}</Table.Th>
                                    <Table.Th />
                                </Table.Tr>
                            </Table.Thead>
                            <Table.Tbody>
                                {plugins.map((plugin, i) => (
                                    <Table.Tr key={`${plugin.product ?? ''}-${plugin.package_name}-${plugin.version}-${i}`}>
                                        {allMode && <Table.Td>{plugin.product}</Table.Td>}
                                        <Table.Td>{plugin.display_name}</Table.Td>
                                        <Table.Td>{plugin.description}</Table.Td>
                                        <Table.Td>{plugin.version}</Table.Td>
                                        <Table.Td>{plugin.tak_prerequisite}</Table.Td>
                                        <Table.Td>{bytes_formatter(plugin.apk_size_bytes)}</Table.Td>
                                        <Table.Td>
                                            <Button
                                                variant="default"
                                                size="xs"
                                                rightSection={<ArrowDownload16Regular />}
                                                onClick={() => download(plugin)}
                                            >
                                                {t('Download Plugin')}
                                            </Button>
                                        </Table.Td>
                                    </Table.Tr>
                                ))}
                            </Table.Tbody>
                        </Table>
                    </Table.ScrollContainer>
                </Panel>
            )}
        </>
    );

    // Device-code flow: the server asks TAK.gov for a short code, the admin
    // approves it on TAK.gov, then the server exchanges it for a token.
    const linkSteps = [
        {
            title: t('Get a one-time code'),
            body: (
                <ReadOnlyTip>
                    <Button disabled={readOnly} onClick={getLinkCode}>
                        {t('Get a code')}
                    </Button>
                </ReadOnlyTip>
            ),
            shown: true,
        },
        {
            title: t('Approve this server on TAK.gov'),
            body: (
                <Stack gap={6}>
                    <Text size="sm">
                        {t('Open the TAK.gov device page and type in this code. It stays valid for 3 minutes.')}
                    </Text>
                    <Anchor href={TAKGOV_DEVICE_PAGE} target="_blank" rel="noopener noreferrer" size="sm">
                        {TAKGOV_DEVICE_PAGE}
                    </Anchor>
                    <Text ff="monospace" fz={28} fw={600} lts={2}>
                        {userCode || '— — —'}
                    </Text>
                </Stack>
            ),
            shown: Boolean(userCode) || readOnly,
        },
        {
            title: t('Finish linking'),
            body: (
                <Stack gap={6} align="flex-start">
                    <Text size="sm">{t('Once TAK.gov has accepted the code, come back here and finish.')}</Text>
                    <ReadOnlyTip>
                        <Button disabled={readOnly} onClick={linkAccount}>
                            {t('Finish linking')}
                        </Button>
                    </ReadOnlyTip>
                </Stack>
            ),
            shown: Boolean(userCode) || readOnly,
        },
    ];

    const unlinkedView = (
        <Stack maw={640} mx="auto" gap="lg">
            <Center>
                <TakGovLogo width={160} height={162} />
            </Center>
            <Stack gap={4} align="center">
                <Title order={2}>{t('Connect this server to TAK.gov')}</Title>
                <Text c="dimmed" ta="center">
                    {t(
                        'A linked server can fetch plugins from your TAK.gov account and offer them to your devices. Sign in to TAK.gov in another browser tab before you start.',
                    )}
                </Text>
            </Stack>
            {linkSteps
                .filter((step) => step.shown)
                .map((step, i) => (
                    <Panel key={step.title} p="md">
                        <Group align="flex-start" wrap="nowrap" gap="md">
                            <Text fw={700} c="var(--raven-accent)" w={20}>
                                {i + 1}
                            </Text>
                            <Stack gap="xs" style={{ flex: 1 }}>
                                <Text fw={600}>{step.title}</Text>
                                {step.body}
                            </Stack>
                        </Group>
                    </Panel>
                ))}
        </Stack>
    );

    return (
        <>
            <LoadingOverlay visible={pending > 0} style={{ position: 'fixed' }} zIndex={1000} />
            <PageHeader
                title={t('Link TAK.gov Account')}
                actions={
                    linked && (
                        <ReadOnlyTip>
                            <Button
                                color="red"
                                disabled={readOnly}
                                leftSection={<LinkDismiss20Regular />}
                                onClick={() => setUnlinkOpen(true)}
                            >
                                {t('Unlink Account')}
                            </Button>
                        </ReadOnlyTip>
                    )
                }
            />
            <ReadOnlyNotice />
            {linked ? linkedView : unlinkedView}

            <Modal
                opened={unlinkOpen && !readOnly}
                onClose={() => setUnlinkOpen(false)}
                title={t('Unlink this server from TAK.gov? Plugins already downloaded stay on the server.')}
            >
                <Group justify="flex-end" gap="xs">
                    <Button variant="default" onClick={() => setUnlinkOpen(false)}>
                        {t('No')}
                    </Button>
                    <Button color="red" onClick={unlink}>
                        {t('Yes')}
                    </Button>
                </Group>
            </Modal>
        </>
    );
}
