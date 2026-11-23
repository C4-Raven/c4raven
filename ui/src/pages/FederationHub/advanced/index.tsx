/*
 * Advanced section of the Federation Hub page.
 *
 * Its job: keep EVERY function of the old FederationHub.tsx page and of the
 * native hub console reachable (fixed and clearer), so nothing is lost by the
 * simplified partner-card page. Checklist, from fedhub-inventory-current.md
 * (old function -> where it lives now):
 *
 * 1.1 Page load / polling / errors
 *   [x] loadAll (connections, metrics, plugins, CA groups, federations)  -> shared.tsx HubDataProvider.reload (+ active policy)
 *   [x] 10 s poll of connections + metrics                               -> HubDataProvider (errors now shown, not swallowed)
 *   [x] showError toast (only first failure, axios text)                 -> shared.tsx notifyFail / api.ts errorText (hub raw text) + HubErrorAlert (all failures)
 * 1.2 Header bar
 *   [x] Refresh                                                          -> Advanced header Refresh button
 *   [x] Open Native Admin Panel (hard-coded host name)                 -> Admin access tab, URL from GET /native_url
 *   [x] Get Admin Certificate                                            -> Admin access tab
 *   [x] Restart Broker (confirm, fixed 3 s reload)                       -> Broker tab: confirm (G9 note) + polls /overview until reachable
 * 1.3 Status tab
 *   [x] Tiles: connected clients, CPU, heap, messages dropped            -> Metrics tab (-1 shown as "Not reported", G21)
 *   [x] Fetched-but-hidden metrics (totals, bytes, rates, cores, heap
 *       allocated, channelInfos)                                         -> Metrics tab: all tiles + Channels table
 *   [x] Active connections table (+ remoteConnectionType, was hidden)    -> Connections tab (CA uid -> nickname, bridge marked)
 *   [x] Disconnect (no confirm)                                          -> Connections tab, WITH confirm
 *   [x] Per-connection counts footnote                                   -> Connections tab footnote
 *   [x] Registered plugins (raw JSON rows)                               -> Plugins tab as a table (+ read-only policy plugin routing)
 * 1.4 CA Groups tab
 *   [x] Explainer                                                        -> Certificates tab header text
 *   [x] Download our CA                                                  -> Certificates tab
 *   [x] Add CA modal (file + nickname)                                   -> Certificates tab; size/unique-nickname checks (G22) and
 *                                                                          "Also add to the active policy" (node + allGroups both ways
 *                                                                          with own CA, save+activate) so it isn't orphaned (#6)
 *   [x] CA table nickname/subject/hub group (alias/uid/issuer hidden)    -> Certificates tab: + sha256 (G7), uid copy, issuer,
 *                                                                          "In active policy"/"Not in policy" badges, empty state
 *   [x] Inline rename on blur, no revert                                 -> explicit Save/Undo per row (Enter saves), reverts on failure
 *   [x] Remove CA modal                                                  -> confirm with blip warning; removes its policy nodes/rules and
 *                                                                          re-activates FIRST, then deleteGroupCa (G11, api.deleteCaSafely);
 *                                                                          typed confirm for the hub's own CA
 * 1.5 Federations tab                                                    -> Policy editor tab
 *   [x] Federations table, row click opens                               -> + Active badge, unsaved-changes confirm on switch (#2)
 *   [x] (no create/delete before)                                        -> New policy; Delete policy (typed confirm when active, G10)
 *   [x] Outgoing table/create/edit/delete                                -> policy/NodesSection (all node types) + policy/OutgoingModal;
 *                                                                          Status = live connection state, not config.enabled (#10);
 *                                                                          delete confirms and removes touching rules (#7); host
 *                                                                          validation; G6 duplicate-dial warning
 *   [x] Outgoing token as plain TextInput                                -> PasswordInput (masked, reveal)
 *   [x] Group sets create/edit/delete (no confirm)                       -> policy/GroupSetsSection, delete confirm (also strips the set
 *                                                                          from rule filters); "Apply group sets only" ->
 *                                                                          POST /connections/groups (updateConnectionGroupSets)
 *   [x] Connection map diagram (drag/zoom persist, rule drawing)         -> PolicyTab, same FederationPolicyDiagram, keyed per policy (#5),
 *                                                                          local views kept fresh (#4)
 *   [x] Rule add/edit/delete (diagram only, no confirm, no group sets)   -> policy/RulesSection: table + modal, delete confirm, editable
 *                                                                          source/destination, group sets selectable (#8), suggestions
 *                                                                          from /known_groups/<node>, G12 warning for Raven sources
 *   [x] Raw policy JSON                                                  -> PolicyTab "Raw policy JSON" (hidden by default; tokens warning)
 *   [x] Save Policy (never activated)                                    -> "Save only" + "Save & apply" (POST /policy?activate=1) +
 *                                                                          "Apply saved version"; dirty badge, Discard, beforeunload,
 *                                                                          "changed on hub" notice, own-CA-missing warning (G1)
 *   [x] (unused before) export                                           -> Export saved version as JSON / YAML
 * 1.6 Admin certificate 2FA flow                                         -> Admin access tab; password masked + copy; password-fetch error
 *                                                                          shown (not an empty field); 403 on download restarts the flow
 * 1.7 Restart broker                                                     -> Broker tab (see 1.2)
 * 1.8 Diagram controls (zoom, reset, drag, two-click rules, rule list)   -> unchanged component inside Policy editor
 * Native-only functions
 *   [x] getBrokerConfig (was unused)                                     -> Broker tab, read-only, secrets shown as "hidden", G2 warning
 *   [x] generateJwtToken / token groups                                  -> Tokens tab (masked, reveal/copy, revoke) + token-group node
 *   [x] CA group interconnected / allowTokenAuth / enabled               -> NodesSection edit modal (G13)
 *   [x] getKnownGroupsForGraphNode                                       -> rule modal suggestions
 * Not covered here (by design): updateBrokerConfig (G4: unsafe round-trip,
 * host-level edit instead) and policy/core + policy/plugins partial saves
 * (Save & apply sends the whole model, which covers both).
 */
import React, { useEffect, useRef, useState } from 'react';
import { OPEN_ADVANCED_EVENT } from '../parts';
import { ActionIcon, Alert, Badge, Box, Button, Tabs, Tooltip } from '@mantine/core';
import { ArrowClockwise20Regular, ArrowRouting20Regular, Certificate20Regular, ChevronDown20Regular, ChevronUp20Regular, DataBarVertical20Regular, Eye20Regular, Key20Regular, Options20Regular, PlugConnected20Regular, PuzzlePiece20Regular, Server20Regular, ShieldLock20Regular } from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import { Section } from '../../../components/ui';
import { isReadOnlyAdmin } from '../../../adminAccess';
import { HubDataProvider, HubErrorAlert, useHubData } from './shared';
import ConnectionsTab from './ConnectionsTab';
import MetricsTab from './MetricsTab';
import CertificatesTab from './CertificatesTab';
import PolicyTab from './PolicyTab';
import TokensTab from './TokensTab';
import PluginsTab from './PluginsTab';
import BrokerTab from './BrokerTab';
import AdminAccessTab from './AdminAccessTab';

/** Tab order, icon and body. Labels are in AdvancedTabs (they need t()). */
const TAB_DEFS = {
    connections: [PlugConnected20Regular, ConnectionsTab],
    metrics: [DataBarVertical20Regular, MetricsTab],
    certificates: [Certificate20Regular, CertificatesTab],
    policy: [ArrowRouting20Regular, PolicyTab],
    tokens: [Key20Regular, TokensTab],
    plugins: [PuzzlePiece20Regular, PluginsTab],
    broker: [Server20Regular, BrokerTab],
    admin: [ShieldLock20Regular, AdminAccessTab],
} as const;
type TabValue = keyof typeof TAB_DEFS;
const TABS = Object.keys(TAB_DEFS) as TabValue[];
/** Tabs whose bodies a read-only admin can't use (they call routes the server refuses them): a notice instead. */
const FULL_ADMIN_TABS: readonly TabValue[] = ['tokens', 'admin'];

function FullAdminOnly() {
    const { t } = useTranslation();
    return (
        <Alert color="gray" variant="light" icon={<Eye20Regular />}>
            {t('Only a full administrator can view this.')}
        </Alert>
    );
}
const TAB_ICON = { width: 16, height: 16 };

function RefreshButton() {
    const { t } = useTranslation();
    const { reload, loading } = useHubData();
    return (
        <Tooltip label={t('Refresh')}>
            <ActionIcon variant="default" loading={loading} onClick={() => reload()} aria-label={t('Refresh')}>
                <ArrowClockwise20Regular style={{ width: 16, height: 16 }} />
            </ActionIcon>
        </Tooltip>
    );
}

function AdvancedTabs({ initialTab = 'connections' }: { initialTab?: TabValue }) {
    const { t } = useTranslation();
    const { connections } = useHubData();
    const tabs = TABS;
    const readOnly = isReadOnlyAdmin();
    const [tab, setTab] = useState<TabValue>(initialTab);
    // Mount each tab the first time it's opened, then keep it (the policy draft survives tab switches).
    const [visited, setVisited] = useState<Set<TabValue>>(() => new Set([initialTab]));
    useEffect(() => {
        setTab(initialTab);
        setVisited((prev) => (prev.has(initialTab) ? prev : new Set(prev).add(initialTab)));
    }, [initialTab]);
    const labels: Record<TabValue, string> = {
        connections: t('Connections'),
        metrics: t('Metrics'),
        certificates: t('Certificates'),
        policy: t('Policy editor'),
        tokens: t('Tokens'),
        plugins: t('Plugins'),
        broker: t('Broker'),
        admin: t('Admin access'),
    };
    function change(v: string | null) {
        if (!v || !(tabs as string[]).includes(v)) { return; }
        const value = v as TabValue;
        setTab(value);
        setVisited((prev) => (prev.has(value) ? prev : new Set(prev).add(value)));
    }
    return (
        <>
            <HubErrorAlert />
            <Tabs value={tab} onChange={change}>
                <Tabs.List mb="lg">
                    {tabs.map((v) => {
                        const Icon = TAB_DEFS[v][0];
                        return (
                            <Tabs.Tab
                                key={v}
                                value={v}
                                leftSection={<Icon style={TAB_ICON} />}
                                rightSection={v === 'connections' ? <Badge size="xs" variant="light">{connections.length}</Badge> : undefined}
                            >
                                {labels[v]}
                            </Tabs.Tab>
                        );
                    })}
                </Tabs.List>
                {tabs.map((v) => {
                    const Body = readOnly && FULL_ADMIN_TABS.includes(v) ? FullAdminOnly : TAB_DEFS[v][1];
                    return (
                        <div key={v} style={{ display: tab === v ? undefined : 'none' }}>
                            {visited.has(v) && <Body />}
                        </div>
                    );
                })}
            </Tabs>
        </>
    );
}

export function AdvancedSection(): React.JSX.Element {
    const { t } = useTranslation();
    const [opened, setOpened] = useState(false);
    const [everOpened, setEverOpened] = useState(false);
    const [initialTab, setInitialTab] = useState<TabValue>('connections');
    const anchor = useRef<HTMLDivElement>(null);
    function toggle() {
        setOpened((o) => !o);
        setEverOpened(true);
    }
    // Other parts of the page link here (openAdvanced in ../parts), e.g. after a half-applied change.
    useEffect(() => {
        const onOpen = (e: Event) => {
            const tab = (e as CustomEvent<{ tab?: string }>).detail?.tab;
            if (tab && (TABS as string[]).includes(tab)) { setInitialTab(tab as TabValue); }
            setOpened(true);
            setEverOpened(true);
            anchor.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
        };
        window.addEventListener(OPEN_ADVANCED_EVENT, onOpen);
        return () => window.removeEventListener(OPEN_ADVANCED_EVENT, onOpen);
    }, []);
    return (
        <HubDataProvider active={opened}>
            <div ref={anchor} id="fedhub-advanced" />
            <Section
                mb={0}
                divider
                title={<span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}><Options20Regular />{t('Advanced')}</span>}
                description={t('Everything the Federation Hub can do, including raw connections, metrics, trusted CAs, the full policy editor, tokens, plugins, broker settings and admin access.')}
                actions={(
                    <>
                        {opened && <RefreshButton />}
                        <Button variant="default" size="xs" onClick={toggle} rightSection={opened ? <ChevronUp20Regular style={{ width: 14, height: 14 }} /> : <ChevronDown20Regular style={{ width: 14, height: 14 }} />} aria-expanded={opened}>
                            {opened ? t('Hide') : t('Show')}
                        </Button>
                    </>
                )}
            >
                <Box display={opened ? undefined : 'none'}>
                    {everOpened && <AdvancedTabs initialTab={initialTab} />}
                </Box>
            </Section>
        </HubDataProvider>
    );
}

export default AdvancedSection;
