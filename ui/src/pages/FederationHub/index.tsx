import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
    ActionIcon,
    Alert,
    Badge,
    Box,
    Button,
    Checkbox,
    Code,
    Group,
    Modal,
    SimpleGrid,
    Skeleton,
    Stack,
    Table,
    Text,
    Tooltip,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { Add20Regular, ArrowClockwise20Regular, ArrowDownload20Regular, ArrowSwap20Regular, Certificate20Regular, CheckmarkCircle20Regular, Delete20Regular, Dismiss20Regular, Organization20Regular, PlugDisconnected20Regular, Share20Regular, Wand20Regular, Warning20Regular } from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import FederationPolicyDiagram, { type DiagramEntity, type DiagramNodeInfo } from '../../components/FederationPolicyDiagram';
import { PageHeader, Panel, Section } from '../../components/ui';
import { isReadOnlyAdmin } from '../../adminAccess';
import { ReadOnlyNotice } from '../../components/ReadOnlyNotice';
import { ReadOnlyTip } from '../../components/ReadOnlyTip';
import { EmptyState, extractError, PulseDot } from '../ServerRequests/shared';
import { AdvancedSection } from './advanced';
import { deletePartner, getActivePolicy, getOverview, patchPartner, reconnectPartner, repairHub } from './api';
import CheckPanel from './CheckPanel';
import ConnectWizard, { HandoffButtons, useHandoffActions } from './ConnectWizard';
import { explainError, hubDown, ourAddressOf, problemColor, problemText, shortFingerprint, type PartnerErrorView } from './helpers';
import { EMPTY_ICON, ServerErrorAlert } from './parts';
import PartnerCard from './PartnerCard';
import PartnerEditor from './PartnerEditor';
import { createRefresher, POLICY_EVERY_N_POLLS } from './refresh';
import type { ActivePolicy, Overview, Partner } from './types';

const POLL_MS = 10_000;
/** React already escapes; i18next must not turn "&" or "'" in names into entities. */
const RAW = { interpolation: { escapeValue: false } } as const;

function HowItWorks() {
    const { t } = useTranslation();
    const items = [
        {
            icon: <Certificate20Regular />,
            title: t('1. Trust'),
            body: t('Both servers swap certificate authority (CA) files, so each one knows the other is genuine.'),
        },
        {
            icon: <ArrowSwap20Regular />,
            title: t('2. Connect'),
            body: t('One side dials the other. Only one side should, or the hub drops the duplicate connection.'),
        },
        {
            icon: <Share20Regular />,
            title: t('3. Share'),
            body: t('Choose what flows each way. Everything is the usual choice; this server\'s Groups page decides what leaves it.'),
        },
    ];
    return (
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="lg">
            {items.map((it) => (
                <Group key={it.title} gap="sm" wrap="nowrap" align="flex-start">
                    <Box c="var(--raven-accent)" style={{ display: 'flex', flexShrink: 0 }} aria-hidden>{it.icon}</Box>
                    <div>
                        <Text fw={600} size="sm">{it.title}</Text>
                        <Text size="sm" c="dimmed">{it.body}</Text>
                    </div>
                </Group>
            ))}
        </SimpleGrid>
    );
}

function StatusItem({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <Stack gap={2} style={{ minWidth: 0 }}>
            <Text size="xs" c="dimmed">{label}</Text>
            <div>{children}</div>
        </Stack>
    );
}

function StatusStrip({ overview, onRepair, repairing, repairError, readOnly = false }: {
    overview: Overview;
    /** Read-only admin: Repair and "Download our CA" (ca_self is refused to them) are shown disabled. */
    readOnly?: boolean;
    /** Also the retry after a failed repair. */
    onRepair: () => void;
    repairing: boolean;
    repairError?: PartnerErrorView;
}) {
    const { t } = useTranslation();
    const actions = useHandoffActions();
    const { hub, own, bridge } = overview;
    const bridgeOk = bridge.enabled && own.connected;
    const bridgeText = !bridge.enabled
        ? t('Turned off in settings')
        : own.connected
            ? t('Connected')
            : bridge.service_state === 'active'
                ? t('Running, not connected')
                : t('Service {{state}}', { state: bridge.service_state || t('unknown'), interpolation: { escapeValue: false } });
    return (
        <Panel>
            <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }} spacing="md">
                <StatusItem label={t('Hub')}>
                    <Group gap={6} wrap="nowrap">
                        <PulseDot active={hub.reachable} color="var(--mantine-color-green-5)" />
                        <Text size="sm" fw={600}>{hub.reachable ? t('Reachable') : t('Not answering')}</Text>
                    </Group>
                    <Text size="xs" c="dimmed">{t('Active policy')} <Code>{hub.active_policy || t('none')}</Code></Text>
                </StatusItem>
                <StatusItem label={t('This hub\'s certificate')}>
                    <Group gap={6} wrap="nowrap">
                        <Text size="sm" fw={600} truncate>{own.nickname || t('Unnamed')}</Text>
                        {readOnly ? (
                            <ReadOnlyTip>
                                <ActionIcon variant="subtle" size="sm" aria-label={t('Download our CA')} disabled>
                                    <ArrowDownload20Regular style={{ width: 14, height: 14 }} />
                                </ActionIcon>
                            </ReadOnlyTip>
                        ) : (
                            <Tooltip label={t('Download our CA')}>
                                <ActionIcon variant="subtle" size="sm" aria-label={t('Download our CA')} onClick={actions.ownCa}>
                                    <ArrowDownload20Regular style={{ width: 14, height: 14 }} />
                                </ActionIcon>
                            </Tooltip>
                        )}
                    </Group>
                </StatusItem>
                <StatusItem label={t('Partners connect to')}>
                    {ourAddressOf(hub) ? <Code>{ourAddressOf(hub)}</Code> : <Text size="sm" c="dimmed">{t('Unknown: set the hub\'s address in settings')}</Text>}
                </StatusItem>
                <StatusItem label={t('This Raven\'s bridge')}>
                    <Group gap={6} wrap="nowrap">
                        <PulseDot active={bridgeOk} color="var(--mantine-color-green-5)" />
                        <Text size="sm" fw={600}>{bridgeText}</Text>
                    </Group>
                    <Text size="xs" c="dimmed">{t('Carries this server\'s devices to and from the hub')}</Text>
                </StatusItem>
            </SimpleGrid>
            {overview.problems?.length > 0 && (
                <Stack gap="xs" mt="md">
                    {overview.problems.filter((code) => code !== 'hub_unreachable' && code !== 'hub_error').map((code) => (
                        <Alert key={code} color={problemColor(code)} variant="light" icon={<Warning20Regular />} py="xs">
                            <Group justify="space-between" wrap="wrap" gap="sm">
                                <Text size="sm" style={{ flex: 1, minWidth: 200 }}>{problemText(code, t)}</Text>
                                {(code === 'own_ca_missing' || (code === 'no_active_policy' && !overview.problems.includes('own_ca_missing'))) && (
                                    <ReadOnlyTip>
                                        <Button size="xs" color="red" leftSection={<Wand20Regular style={{ width: 14, height: 14 }} />} loading={repairing} disabled={readOnly} onClick={onRepair}>
                                            {t('Repair')}
                                        </Button>
                                    </ReadOnlyTip>
                                )}
                            </Group>
                        </Alert>
                    ))}
                    {!readOnly && repairError && <ServerErrorAlert view={repairError} title={t('Repair failed')} onRetry={onRepair} />}
                </Stack>
            )}
        </Panel>
    );
}

/** The overview answered, but the hub itself is down (hub.reachable false). */
function HubDownState({ overview, onRetry }: { overview: Overview; onRetry: () => void }) {
    const { t } = useTranslation();
    const code = overview.problems?.includes('hub_error') ? 'hub_error' : 'hub_unreachable';
    return (
        <Panel p="lg">
            <EmptyState
                icon={<PlugDisconnected20Regular style={EMPTY_ICON} />}
                color="red"
                title={code === 'hub_error' ? t('The Federation Hub answered with an error') : t('The Federation Hub is down')}
                description={problemText(code, t)}
                action={(
                    <Stack align="center" gap="xs" mt="sm">
                        {overview.error && <Code>{overview.error}</Code>}
                        <Text size="xs" c="dimmed">{t('Partners can\'t be shown or changed until it answers. The page keeps trying every 10 seconds. If it stays down, restart the federation hub service on the server.')}</Text>
                        <Button size="xs" variant="light" leftSection={<ArrowClockwise20Regular style={{ width: 14, height: 14 }} />} onClick={onRetry}>{t('Try again now')}</Button>
                    </Stack>
                )}
            />
        </Panel>
    );
}

export default function FederationHub() {
    const { t } = useTranslation();
    const readOnly = isReadOnlyAdmin();
    const isPhone = useMediaQuery('(max-width: 48em)');
    const [overview, setOverview] = useState<Overview | null>(null);
    const [loadError, setLoadError] = useState<string>();
    const [loading, setLoading] = useState(true);
    const [policy, setPolicy] = useState<ActivePolicy | null>(null);
    const [wizardOpen, setWizardOpen] = useState(false);
    const [editing, setEditing] = useState<Partner | null>(null);
    const [checking, setChecking] = useState<Partner | null>(null);
    const [removing, setRemoving] = useState<Partner | null>(null);
    const [keepCa, setKeepCa] = useState(false);
    const [removeBusy, setRemoveBusy] = useState(false);
    /** Partners with a request in flight (a Set, so two cards never clear each other). */
    const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(() => new Set());
    const [stopping, setStopping] = useState<Partner | null>(null);
    const [repairing, setRepairing] = useState(false);
    const [repairError, setRepairError] = useState<PartnerErrorView>();
    const [showMap, setShowMap] = useState(true);
    const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    const tRef = useRef(t);
    tRef.current = t;

    const showError = useCallback(async (title: string, err: unknown) => {
        notifications.show({ title, message: await extractError(err), icon: <Dismiss20Regular />, color: 'red' });
    }, []);

    // One refresher for the page's lifetime: stale responses are dropped, refreshes after a
    // mutation are queued rather than lost (see refresh.ts).
    const refresher = useMemo(() => createRefresher<{ overview: Overview; policy?: ActivePolicy | null }>({
        fetch: async (withPolicy) => {
            const [ov, pol] = await Promise.all([
                getOverview(),
                withPolicy ? getActivePolicy().catch(() => null) : Promise.resolve(undefined),
            ]);
            return { overview: ov, policy: pol };
        },
        apply: ({ value, withPolicy }) => {
            setOverview(value.overview);
            if (withPolicy) { setPolicy(value.policy ?? null); }
            setLoadError(undefined);
            setLoading(false);
        },
        fail: async (err) => {
            setLoadError(await extractError(err) ?? tRef.current('The Federation Hub could not be reached'));
            setLoading(false);
        },
    }), []);

    const load = useCallback((withPolicy = false) => refresher.request({ force: true, withPolicy }), [refresher]);

    /** Run a change on the hub: polls during it are discarded and a full reload follows it. */
    const mutate = useCallback(async <R,>(fn: () => Promise<R>): Promise<R> => {
        refresher.begin();
        try {
            return await fn();
        } finally {
            refresher.end();
        }
    }, [refresher]);

    /** A change made by the wizard or the editor (already applied): drop stale data and reload. */
    const afterChange = useCallback(() => { refresher.begin(); refresher.end(); }, [refresher]);

    useEffect(() => {
        refresher.request({ force: true, withPolicy: true });
        let tick = 0;
        const timer = setInterval(() => {
            if (document.hidden) { return; }
            tick += 1;
            refresher.request({ withPolicy: tick % POLICY_EVERY_N_POLLS === 0 });
        }, POLL_MS);
        return () => {
            clearInterval(timer);
            if (reconnectTimer.current) { clearTimeout(reconnectTimer.current); }
        };
    }, [refresher]);

    const markBusy = (id: string, busy: boolean) => setBusyIds((prev) => {
        const next = new Set(prev);
        if (busy) { next.add(id); } else { next.delete(id); }
        return next;
    });

    useEffect(() => { setShowMap(!isPhone); }, [isPhone]);

    const partners = useMemo(() => overview?.partners ?? [], [overview]);
    const allConnections = useMemo(
        () => [...partners.flatMap((p) => p.connections ?? []), ...(overview?.unattributed_connections ?? [])],
        [partners, overview],
    );

    // Friendly names, kinds and live status for the connection map's nodes (never certificate hashes).
    const mapNodeInfo = useCallback((e: DiagramEntity): DiagramNodeInfo | undefined => {
        if (!overview) { return undefined; }
        if (e.type === 'FederationGroup' && e.name && e.name === overview.own.uid) {
            return { isOwn: true, label: overview.own.nickname, status: overview.own.connected ? 'connected' : 'down' };
        }
        const p = partners.find((x) => (e.type === 'FederationGroup' && !!x.ca && x.ca.uid === e.name)
            || (e.type === 'FederationOutgoing' && !!x.outgoing && x.outgoing.name === e.name));
        if (!p) { return undefined; }
        const status = p.status === 'connected' ? 'connected' : p.status === 'waiting' ? 'waiting' : p.status === 'disabled' ? 'off' : 'down';
        return { label: p.name, kind: p.kind, status };
    }, [overview, partners]);

    const down = hubDown(overview);
    const handoffActions = useHandoffActions();

    function openPartner(id: string) {
        const p = partners.find((x) => x.id === id);
        setWizardOpen(false);
        if (p) {
            setEditing(p);
        } else {
            notifications.show({ color: 'yellow', title: t('Partner not found'), message: t('Refresh the page and look for it in the list.') });
        }
    }

    function replacePartner(id: string, p: Partner) {
        setOverview((o) => (o ? { ...o, partners: o.partners.map((x) => (x.id === id ? p : x)) } : o));
    }

    async function toggle(p: Partner, enabled: boolean) {
        markBusy(p.id, true);
        replacePartner(p.id, { ...p, enabled, status: enabled ? p.status : 'disabled' });
        try {
            await mutate(async () => {
                const saved = await patchPartner(p.id, { enabled });
                if (saved?.id) { replacePartner(p.id, saved); }
            });
        } catch (err) {
            replacePartner(p.id, p);
            showError(t('Failed to update partner'), err);
        } finally {
            markBusy(p.id, false);
        }
    }

    async function reconnect(p: Partner) {
        markBusy(p.id, true);
        try {
            const result = await reconnectPartner(p.id);
            notifications.show(result?.disconnected
                ? { color: 'green', title: t('Reconnecting'), message: t('{{name}}: {{count}} connection(s) dropped. They come back by themselves in a few seconds.', { name: p.name, count: result.disconnected, ...RAW }) }
                : { color: 'blue', title: t('Nothing to reconnect'), message: t('{{name}} has no live connection right now.', { name: p.name, ...RAW }) });
            if (reconnectTimer.current) { clearTimeout(reconnectTimer.current); }
            reconnectTimer.current = setTimeout(() => { load(); }, 3000);
        } catch (err) {
            showError(t('Failed to reconnect'), err);
        } finally {
            markBusy(p.id, false);
        }
    }

    /** Both sides dial: drop our outgoing connection (outgoing-only: remove it, keeping any CA). */
    async function stopDialling(p: Partner) {
        setStopping(null);
        markBusy(p.id, true);
        try {
            await mutate(async () => {
                if (p.ca) {
                    const saved = await patchPartner(p.id, { direction: 'they_dial' });
                    if (saved?.id) { replacePartner(p.id, saved); }
                    notifications.show({ color: 'green', icon: <CheckmarkCircle20Regular />, title: t('No longer connecting to them'), message: t('{{name}} now connects to us; this hub stopped dialling.', { name: p.name, ...RAW }) });
                } else {
                    const result = await deletePartner(p.id, true);
                    setOverview((o) => (o ? { ...o, partners: o.partners.filter((x) => x.id !== p.id) } : o));
                    notifications.show({ color: result?.warning ? 'yellow' : 'green', icon: <CheckmarkCircle20Regular />, title: t('Outgoing connection removed'), message: result?.warning ?? p.name });
                }
            });
        } catch (err) {
            const v = await explainError(err, partners, t);
            notifications.show({ color: 'red', icon: <Dismiss20Regular />, title: t('Failed to change the connection'), message: v.message });
        } finally {
            markBusy(p.id, false);
        }
    }

    async function confirmRemove() {
        if (!removing) { return; }
        setRemoveBusy(true);
        try {
            const result = await mutate(() => deletePartner(removing.id, keepCa));
            notifications.show(result?.warning
                ? { color: 'yellow', icon: <Warning20Regular />, title: t('Partner removed, certificate kept'), message: result.warning, autoClose: 12000 }
                : {
                    color: 'green',
                    icon: <CheckmarkCircle20Regular />,
                    title: t('Partner removed'),
                    message: result?.ca_removed ? t('{{name}} and its certificate were removed.', { name: removing.name, ...RAW }) : removing.name,
                });
            setOverview((o) => (o ? { ...o, partners: o.partners.filter((x) => x.id !== removing.id) } : o));
            setRemoving(null);
        } catch (err) {
            const v = await explainError(err, partners, t);
            notifications.show({ color: 'red', icon: <Dismiss20Regular />, title: t('Failed to remove partner'), message: v.message });
        } finally {
            setRemoveBusy(false);
        }
    }

    async function repair() {
        setRepairing(true);
        setRepairError(undefined);
        try {
            const result = await mutate(() => repairHub());
            notifications.show(result?.changed === false
                ? { color: 'blue', title: t('Nothing to repair'), message: t('This hub\'s certificate is already in the active policy.') }
                : { color: 'green', icon: <CheckmarkCircle20Regular />, title: t('Repaired'), message: t('This hub\'s certificate is back in the active policy.') });
        } catch (err) {
            setRepairError(await explainError(err, partners, t, t('Repair failed')));
        } finally {
            setRepairing(false);
        }
    }

    const entities = policy?.policy?.entities ?? [];
    const rules = policy?.policy?.rules ?? [];

    return (
        <Stack gap="lg">
            <PageHeader
                title={t('Federation Hub')}
                description={t('Share devices, chat and map data with other TAK servers')}
                actions={(
                    <>
                        <Tooltip label={t('Refresh')}>
                            <ActionIcon variant="default" size="lg" aria-label={t('Refresh')} onClick={() => load(true)}>
                                <ArrowClockwise20Regular />
                            </ActionIcon>
                        </Tooltip>
                            <Button leftSection={<Add20Regular />} onClick={() => setWizardOpen(true)} disabled={!overview || down}>
                                {t('Connect a server')}
                            </Button>
                    </>
                )}
            />

            <ReadOnlyNotice />

            <HowItWorks />

            {loadError && (
                <Alert color="red" variant="light" icon={<Warning20Regular />} title={t('Can\'t reach the Federation Hub')}>
                    {loadError}
                    <Text size="xs" mt={4}>{t('The page keeps trying every 10 seconds.')}</Text>
                </Alert>
            )}

            {loading ? (
                <Skeleton height={110} />
            ) : overview && (
                <StatusStrip overview={overview} readOnly={readOnly} onRepair={repair} repairing={repairing} repairError={repairError} />
            )}

            {loading ? (
                <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
                    <Skeleton height={240} />
                    <Skeleton height={240} />
                </SimpleGrid>
            ) : overview && down ? (
                <HubDownState overview={overview} onRetry={() => load(true)} />
            ) : partners.length === 0 ? (
                <Panel p="lg">
                    <EmptyState
                        icon={<Organization20Regular style={EMPTY_ICON} />}
                        color="blue"
                        title={t('Not connected to any other server yet')}
                        description={t('Connect another Raven, a TAK Server or a federation hub so devices on both sides can see each other. The guide walks you and the other admin through it.')}
                        action={(
                            <Stack align="center" gap="sm" mt="sm">
                                    <Button leftSection={<Add20Regular />} onClick={() => setWizardOpen(true)} disabled={!overview}>
                                        {t('Connect a server')}
                                    </Button>
                                <HandoffButtons size="xs" />
                            </Stack>
                        )}
                    />
                </Panel>
            ) : (
                <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
                    {partners.map((p) => (
                        <PartnerCard
                            key={p.id}
                            partner={p}
                            busy={busyIds.has(p.id)}
                            readOnly={readOnly}
                            onEdit={() => setEditing(p)}
                            onCheck={() => setChecking(p)}
                            onReconnect={() => reconnect(p)}
                            onRemove={() => { setKeepCa(false); setRemoving(p); }}
                            onToggle={(enabled) => toggle(p, enabled)}
                            onStopDialling={() => setStopping(p)}
                            onInstructions={() => handoffActions.handoff({ partnerId: p.id, kind: p.kind })}
                        />
                    ))}
                </SimpleGrid>
            )}

            {(overview?.unattributed_connections?.length ?? 0) > 0 && (
                <Section
                    mb={0}
                    title={t('Unrecognised connections')}
                    description={t('Connected to the hub but not matched to a partner above. This is often this Raven\'s own bridge, or a server whose certificate was trusted outside this page. Disconnect them from Advanced, Connections.')}
                >
                    <Panel p={0}>
                        <Table.ScrollContainer minWidth={560}>
                            <Table>
                                <Table.Thead>
                                    <Table.Tr>
                                        <Table.Th>{t('Server')}</Table.Th>
                                        <Table.Th>{t('Address')}</Table.Th>
                                        <Table.Th>{t('Direction')}</Table.Th>
                                        <Table.Th>{t('Certificate')}</Table.Th>
                                    </Table.Tr>
                                </Table.Thead>
                                <Table.Tbody>
                                    {overview!.unattributed_connections.map((c) => (
                                        <Table.Tr key={c.connectionId}>
                                            <Table.Td><Text size="sm" style={{ wordBreak: 'break-all' }}>{c.remoteServerId || c.connectionId}</Text></Table.Td>
                                            <Table.Td><Text size="sm">{c.remoteAddress || '—'}</Text></Table.Td>
                                            <Table.Td>
                                                <Badge variant="light" tt="none" color={c.localConnectionType === 'CLIENT' ? 'indigo' : 'grape'}>
                                                    {c.localConnectionType === 'CLIENT' ? t('We dialled') : t('They dialled')}
                                                </Badge>
                                            </Table.Td>
                                            <Table.Td>
                                                <Text size="xs" c="dimmed" style={{ wordBreak: 'break-all' }}>
                                                    {(c.groupIdentities ?? []).map((g) => {
                                                        const m = /-([0-9a-fA-F:]{40,})$/.exec(g);
                                                        return m ? shortFingerprint(m[1]) : g;
                                                    }).join(', ') || '—'}
                                                </Text>
                                            </Table.Td>
                                        </Table.Tr>
                                    ))}
                                </Table.Tbody>
                            </Table>
                        </Table.ScrollContainer>
                    </Panel>
                </Section>
            )}

            <Section
                mb={0}
                divider
                title={t('Connection map')}
                description={t('The policy the hub is enforcing right now. To change it by hand, use the policy editor under Advanced.')}
                actions={(
                    <Button size="xs" variant="subtle" onClick={() => setShowMap((v) => !v)} aria-expanded={showMap}>
                        {showMap ? t('Hide map') : t('Show map')}
                    </Button>
                )}
            >
                {showMap && (policy ? (
                    <FederationPolicyDiagram
                        key={`${policy.name ?? ''}|${policy.modifiedDate ?? ''}|${entities.length}|${rules.length}`}
                        entities={entities}
                        rules={rules}
                        views={policy.views?.graph}
                        knownGroups={[]}
                        connections={allConnections}
                        nodeInfo={mapNodeInfo}
                        readOnly
                    />
                ) : (
                    <Text size="sm" c="dimmed">{t('No active policy to show.')}</Text>
                ))}
            </Section>

            <AdvancedSection />

            <ConnectWizard
                opened={wizardOpen}
                onClose={() => { setWizardOpen(false); afterChange(); }}
                overview={overview}
                onCreated={afterChange}
                onOpenPartner={openPartner}
            />

            <PartnerEditor
                partner={readOnly ? null : editing}
                overview={overview}
                onClose={() => setEditing(null)}
                onOpenPartner={openPartner}
                onSaved={(p) => {
                    if (editing && p?.id) { replacePartner(editing.id, p); }
                    setEditing(null);
                    afterChange();
                }}
            />

            <Modal
                opened={!readOnly && checking !== null}
                onClose={() => setChecking(null)}
                size="lg"
                fullScreen={isPhone}
                title={<Text fw={700}>{t('Check connection to {{name}}', { name: checking?.name ?? '', ...RAW })}</Text>}
            >
                {checking && <CheckPanel partnerId={checking.id} />}
            </Modal>

            <Modal
                opened={!readOnly && stopping !== null}
                onClose={() => setStopping(null)}
                title={<Text fw={700}>{stopping && !stopping.ca ? t('Remove this outgoing connection?') : t('Stop connecting to {{name}}?', { name: stopping?.name ?? '', ...RAW })}</Text>}
                centered
            >
                <Stack gap="sm">
                    <Text size="sm">
                        {stopping && !stopping.ca
                            ? t('This hub stops dialling {{address}} and the outgoing connection is removed from the active policy, which is applied straight away. Certificates stay trusted.', { address: stopping.outgoing ? `${stopping.outgoing.host}:${stopping.outgoing.port}` : '', ...RAW })
                            : t('This hub stops dialling {{name}} and waits for them instead. The change is applied on the hub straight away; their certificate and sharing settings stay as they are.', { name: stopping?.name ?? '', ...RAW })}
                    </Text>
                    <Alert color="yellow" variant="light" icon={<Warning20Regular />} py="xs">
                        {t('From now on the OTHER side must be the one that connects. If they don\'t connect to us, the link stays down until they do.')}
                    </Alert>
                </Stack>
                <Group justify="flex-end" mt="lg" gap="sm">
                    <Button variant="default" onClick={() => setStopping(null)}>{t('Cancel')}</Button>
                    <Button color="red" leftSection={<PlugDisconnected20Regular style={{ width: 16, height: 16 }} />} onClick={() => stopping && stopDialling(stopping)}>
                        {stopping && !stopping.ca ? t('Remove outgoing connection') : t('Stop connecting')}
                    </Button>
                </Group>
            </Modal>

            <Modal
                opened={!readOnly && removing !== null}
                onClose={() => setRemoving(null)}
                title={<Text fw={700}>{t('Remove {{name}}?', { name: removing?.name ?? '', ...RAW })}</Text>}
                centered
            >
                <Stack gap="sm">
                    <Text size="sm">
                        {t('The hub stops exchanging data with this partner straight away, and the change is activated on the hub.')}
                    </Text>
                    <Checkbox
                        label={t('Keep their certificate trusted')}
                        description={t('Useful if you plan to reconnect them soon.')}
                        checked={keepCa}
                        onChange={(e) => setKeepCa(e.currentTarget.checked)}
                    />
                    <Alert color="yellow" variant="light" icon={<Warning20Regular />} py="xs">
                        {keepCa
                            ? t('Their certificate stays trusted, so other partners are not interrupted.')
                            : t('Their certificate is removed too. The hub then restarts its federation listener, so every other partner disconnects for a few seconds and reconnects by itself.')}
                    </Alert>
                </Stack>
                <Group justify="flex-end" mt="lg" gap="sm">
                    <Button variant="default" onClick={() => setRemoving(null)}>{t('Cancel')}</Button>
                    <Button color="red" leftSection={<Delete20Regular style={{ width: 16, height: 16 }} />} loading={removeBusy} onClick={confirmRemove}>
                        {t('Remove partner')}
                    </Button>
                </Group>
            </Modal>
        </Stack>
    );
}
