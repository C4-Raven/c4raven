import React, { useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Code, Divider, Group, Loader, Stack, Table, Text, Title } from '@mantine/core';
import { ArrowClockwise20Regular, ArrowSync20Regular, Server20Regular, Warning20Regular } from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import axios from '../../../axios_config';
import { isReadOnlyAdmin } from '../../../adminAccess';
import { ReadOnlyTip } from '../../../components/ReadOnlyTip';
import { humanize } from '../../ServerRequests/shared';
import { endpoints } from './endpoints';
import { errorText, waitForHub } from './api';
import { isSecretKey } from './helpers';
import { ConfirmModal, notifyFail, notifyOk, useHubData } from './shared';
import { CopyIcon } from '../parts';

function renderValue(key: string, value: unknown, hidden: string) {
    if (isSecretKey(key)) { return <Text size="sm" c="dimmed" fs="italic">{hidden}</Text>; }
    if (value === null || value === undefined || value === '') { return <Text size="sm" c="dimmed">—</Text>; }
    if (typeof value === 'boolean') { return <Badge size="sm" variant="light" color={value ? 'green' : 'gray'}>{String(value)}</Badge>; }
    if (typeof value === 'object') { return <Code block>{JSON.stringify(value, null, 2)}</Code>; }
    return <Text size="sm" ff="monospace" style={{ wordBreak: 'break-all' }}>{String(value)}</Text>;
}

const BRIDGE_COMMAND = 'sudo systemctl restart fedhub_bridge';
const sleep = (ms: number) => new Promise((resolve) => { setTimeout(resolve, ms); });

/**
 * Raven's own Federation Hub bridge (fedhub_bridge.service): the process that
 * carries this server's users and messages to and from the hub. Restarting it
 * fixes federated users that stopped appearing or updating, without anyone
 * needing a shell. The server asks the bridge to exit and systemd starts it
 * again, so this waits for it to go away and come back.
 */
function BridgeControl() {
    const { t } = useTranslation();
    const readOnly = isReadOnlyAdmin();
    const [running, setRunning] = useState<boolean | null>(null);
    // The server can restart a stopped bridge through systemd (the updater's
    // fedhub_bridge-restart.path); without that, only a running one.
    const [canStart, setCanStart] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [confirm, setConfirm] = useState(false);
    const [busy, setBusy] = useState(false);
    const cancelled = useRef(false);
    useEffect(() => () => { cancelled.current = true; }, []);

    async function fetchRunning(): Promise<boolean | null> {
        try {
            const r = await axios.get(endpoints.bridge);
            if (!cancelled.current) { setCanStart(Boolean(r.data?.can_start)); }
            return Boolean(r.data?.running);
        } catch (err) {
            setError(await errorText(err));
            return null;
        }
    }

    async function refresh() {
        const value = await fetchRunning();
        if (!cancelled.current && value !== null) { setRunning(value); setError(null); }
    }
    useEffect(() => { refresh(); }, []);

    async function restart() {
        setConfirm(false);
        setBusy(true);
        try {
            await axios.post(endpoints.restartBridge);
        } catch (err) {
            notifyFail(t('Failed to restart the bridge'), err);
            setBusy(false);
            refresh();
            return;
        }
        // It drops off at once and systemd starts it again about 5 seconds
        // later. Don't take "running" as "back" until it has been seen gone,
        // or 15 seconds have passed in case the gap was missed.
        const started = Date.now();
        let seenDown = false;
        let back = false;
        while (!cancelled.current && Date.now() - started < 60000) {
            await sleep(1500);
            const value = await fetchRunning();
            if (value === false) { seenDown = true; }
            if (value === true && (seenDown || Date.now() - started > 15000)) { back = true; break; }
        }
        if (cancelled.current) { return; }
        setBusy(false);
        setRunning(back);
        if (back) {
            notifyOk(t('Bridge is back'), t('Federated users reappear as their next updates arrive.'));
        } else {
            notifyFail(t('The bridge hasn\'t come back yet'), t('On the server, run: {{cmd}}', { cmd: BRIDGE_COMMAND, interpolation: { escapeValue: false } }));
        }
    }

    return (
        <Stack gap="xs">
            <Group justify="space-between" wrap="wrap" gap="sm">
                <div style={{ maxWidth: 680 }}>
                    <Group gap="xs">
                        <Title order={5}>{t('Federation bridge')}</Title>
                        {running === null ? null : (
                            <Badge size="sm" variant="light" color={running ? 'green' : 'red'}>
                                {running ? t('Running') : t('Not running')}
                            </Badge>
                        )}
                    </Group>
                    <Text size="sm" c="dimmed">
                        {t('The bridge carries this server\'s users and messages to and from the hub. If federated users stop appearing or updating, restart it.')}
                    </Text>
                </div>
                <ReadOnlyTip>
                    <Button size="xs" color="red" variant="light" leftSection={<ArrowSync20Regular style={{ width: 14, height: 14 }} />} loading={busy} disabled={readOnly || (running === false && !canStart)} onClick={() => setConfirm(true)}>{t('Restart bridge')}</Button>
                </ReadOnlyTip>
            </Group>
            {busy && (
                <Alert color="blue" variant="light" icon={<Loader size={16} />}>
                    {t('Restarting the bridge. This takes about 10 seconds.')}
                </Alert>
            )}
            {error && <Alert color="red" variant="light">{error}</Alert>}
            {running === false && !busy && (
                <Alert color="orange" variant="light" icon={<Warning20Regular />}>
                    <Text size="sm">{t('The bridge isn\'t running, so it can\'t be restarted from here. On the server, run:')}</Text>
                    <Group gap={4} mt={4} wrap="nowrap">
                        <Code>{BRIDGE_COMMAND}</Code>
                        <CopyIcon value={BRIDGE_COMMAND} label={t('Copy command')} />
                    </Group>
                </Alert>
            )}
            <ConfirmModal opened={!readOnly && confirm} onClose={() => setConfirm(false)} onConfirm={restart} title={t('Restart the bridge?')} confirmLabel={t('Restart')}>
                <Text size="sm">{t('Federated users and messages pause for about 10 seconds while the bridge restarts. Partners and this server\'s own users stay connected.')}</Text>
            </ConfirmModal>
        </Stack>
    );
}

export default function BrokerTab() {
    const { t } = useTranslation();
    const readOnly = isReadOnlyAdmin();
    const { reload } = useHubData();
    const [config, setConfig] = useState<Record<string, unknown> | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [confirm, setConfirm] = useState(false);
    const [phase, setPhase] = useState<'idle' | 'restarting' | 'waiting'>('idle');
    const cancelled = useRef(false);
    useEffect(() => () => { cancelled.current = true; }, []);

    function loadConfig() {
        axios.get(endpoints.brokerConfig)
            .then((r) => { setConfig(r.data && typeof r.data === 'object' ? r.data : null); setError(null); })
            .catch(async (err) => setError(await errorText(err)));
    }
    useEffect(() => { loadConfig(); }, []);

    async function restart() {
        setConfirm(false);
        setPhase('restarting');
        try {
            await axios.post(endpoints.restartBroker, undefined, { timeout: 120000 });
        } catch (err: any) {
            // With the supervised unit the whole hub restarts and this call can drop (G9); keep waiting.
            if (err?.response && err.response.status < 500) {
                notifyFail(t('Failed to restart broker'), err);
                setPhase('idle');
                return;
            }
        }
        setPhase('waiting');
        const ok = await waitForHub(() => cancelled.current);
        if (cancelled.current) { return; }
        setPhase('idle');
        if (ok) {
            notifyOk(t('Broker is back'), t('Partners reconnect over the next few seconds.'));
            reload();
            loadConfig();
        } else {
            notifyFail(t('The hub hasn\'t come back yet'), t('Check the federation-hub service on the server.'));
        }
    }

    const entries = config ? Object.entries(config).sort(([a], [b]) => a.localeCompare(b)) : [];
    const timeout = typeof config?.clientTimeoutTime === 'number' ? config.clientTimeoutTime : null;

    return (
        <Stack gap="md">
            <BridgeControl />
            <Divider />
            <Group justify="space-between" wrap="wrap" gap="sm">
                <Text size="sm" c="dimmed" maw={680}>
                    {t('The broker carries federation traffic. Its settings are shown read-only. Changing them means editing federation-hub-broker.yml on the server and restarting.')}
                </Text>
                <Group gap="xs">
                    <Button size="xs" variant="default" leftSection={<ArrowClockwise20Regular style={{ width: 14, height: 14 }} />} onClick={loadConfig}>{t('Refresh')}</Button>
                    <ReadOnlyTip>
                        <Button size="xs" color="red" variant="light" leftSection={<Server20Regular style={{ width: 14, height: 14 }} />} loading={phase !== 'idle'} disabled={readOnly} onClick={() => setConfirm(true)}>{t('Restart broker')}</Button>
                    </ReadOnlyTip>
                </Group>
            </Group>

            {phase !== 'idle' && (
                <Alert color="blue" variant="light" icon={<Loader size={16} />}>
                    {phase === 'restarting' ? t('Restarting the broker…') : t('Waiting for the hub to answer again. This can take a minute.')}
                </Alert>
            )}
            {error && <Alert color="red" variant="light">{error}</Alert>}
            {timeout !== null && timeout <= 15 && (
                <Alert color="orange" variant="light" icon={<Warning20Regular />}>
                    {t('clientTimeoutTime is {{n}} seconds, so quiet federation links will be dropped. Raven\'s installer sets 3600.', { n: timeout, interpolation: { escapeValue: false } })}
                </Alert>
            )}

            {config && (
                <div>
                    <Title order={5} mb="xs">{t('Broker configuration')}</Title>
                    <Table.ScrollContainer minWidth={600}>
                        <Table striped withTableBorder>
                            <Table.Tbody>
                                {entries.map(([k, v]) => (
                                    <Table.Tr key={k}>
                                        <Table.Td w={280}><Text size="sm" fw={600}>{humanize(k)}</Text><Text size="xs" c="dimmed" ff="monospace">{k}</Text></Table.Td>
                                        <Table.Td>{renderValue(k, v, t('hidden'))}</Table.Td>
                                    </Table.Tr>
                                ))}
                            </Table.Tbody>
                        </Table>
                    </Table.ScrollContainer>
                </div>
            )}

            <ConfirmModal opened={!readOnly && confirm} onClose={() => setConfirm(false)} onConfirm={restart} title={t('Restart the broker?')} confirmLabel={t('Restart')}>
                <Text size="sm">{t('Every federation partner and this Raven\'s bridge disconnect until the broker is back, usually within a minute.')}</Text>
                <Text size="sm" c="dimmed">{t('Depending on how the hub service is installed, this can restart the whole Federation Hub, including its admin interface. This page waits until it answers again.')}</Text>
            </ConfirmModal>
        </Stack>
    );
}
