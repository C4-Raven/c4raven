import React, { useCallback, useEffect, useState } from 'react';
import {
    ActionIcon,
    Alert,
    Badge,
    Box,
    Button,
    CopyButton,
    Group,
    Modal,
    NumberInput,
    Pagination,
    Select,
    SimpleGrid,
    Skeleton,
    Stack,
    Switch,
    Text,
    TextInput,
    Tooltip,
} from '@mantine/core';
import {
    Add20Regular,
    ArrowClockwise20Regular,
    ArrowImport20Regular,
    ArrowSwap20Regular,
    Checkmark20Regular,
    Copy20Regular,
    Delete20Regular,
    Dismiss20Regular,
    Eye20Regular,
    EyeOff20Regular,
    Key20Regular,
    LockOpen20Regular,
    QrCode20Regular,
    Router20Regular,
    Warning20Regular,
} from '@fluentui/react-icons';
import { notifications } from '@mantine/notifications';
import { QRCode } from 'react-qrcode-logo';
import { t } from 'i18next';
import axios from '../axios_config';
import { apiRoutes } from '../apiRoutes';
import { PageHeader, Panel, Section } from '../components/ui';
import { isReadOnlyAdmin } from '../adminAccess';
import { ReadOnlyNotice } from '../components/ReadOnlyNotice';
import { READ_ONLY_LABEL, ReadOnlyTip } from '../components/ReadOnlyTip';
import { EmptyState } from './ServerRequests/shared';
import { MeshtasticOffNotice } from './MeshtasticOffNotice';

/** A stored channel as GET /api/meshtastic/channel lists it (region and preset as names). */
export interface Channel {
    name: string;
    psk: string | null;
    uplink_enabled: boolean;
    downlink_enabled: boolean;
    position_precision: number;
    lora_region: string;
    lora_hop_limit: number;
    lora_tx_enabled: boolean;
    lora_tx_power: number;
    lora_sx126x_rx_boosted_gain: boolean;
    modem_preset: string;
    url: string;
}

const PAGE_SIZE = 12;
const ICON_16 = { width: 16, height: 16 };
const EMPTY_ICON = { width: 28, height: 28 };
/** Meshtastic keeps channel names to 11 bytes. */
export const MAX_NAME_LENGTH = 11;

/** The server's LORA_REGIONS names, in protobuf order. */
const REGIONS = [
    'UNSET', 'US', 'EU_433', 'EU_868', 'CN', 'JP', 'ANZ', 'KR', 'TW', 'RU', 'IN', 'NZ_865', 'TH', 'LORA_24',
    'UA_433', 'UA_868', 'MY_433', 'MY_919', 'SG_923',
];

const MODEM_PRESETS = [
    'LONG_FAST', 'LONG_SLOW', 'VERY_LONG_SLOW', 'MEDIUM_SLOW', 'MEDIUM_FAST', 'SHORT_SLOW', 'SHORT_FAST',
    'LONG_MODERATE', 'SHORT_TURBO',
];

/** "LONG_FAST" -> "Long Fast" */
const presetLabel = (preset: string) =>
    preset.toLowerCase().split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

function precisionLabel(bits: number): string {
    switch (bits) {
        case 0: return t('Not shared');
        case 11: return t('Low (about 12 km)');
        case 16: return t('Medium (about 360 m)');
        case 32: return t('Exact');
        default: return t('{{bits}} bits', { bits });
    }
}

const PRECISIONS = [32, 16, 11, 0];

interface NewChannel {
    name: string;
    psk: string;
    uplink_enabled: boolean;
    downlink_enabled: boolean;
    position_precision: number;
    lora_region: string;
    lora_hop_limit: number;
    lora_tx_enabled: boolean;
    lora_tx_power: number;
    lora_sx126x_rx_boosted_gain: boolean;
    modem_preset: string;
}

const NEW_CHANNEL_DEFAULTS: NewChannel = {
    name: '',
    psk: '',
    uplink_enabled: true,
    downlink_enabled: true,
    position_precision: 32,
    lora_region: 'UNSET',
    lora_hop_limit: 3,
    // A radio that takes this channel's URL also takes its LoRa settings: TX off would silence it
    lora_tx_enabled: true,
    lora_tx_power: 0,
    lora_sx126x_rx_boosted_gain: false,
    modem_preset: 'LONG_FAST',
};

/** Client-side checks; the server still has the last word. Returns field -> message. */
export function validateChannel(form: NewChannel): Partial<Record<keyof NewChannel, string>> {
    const errors: Partial<Record<keyof NewChannel, string>> = {};
    const name = form.name.trim();
    if (name === '') {
        errors.name = t('Enter a name');
    } else if (new TextEncoder().encode(name).length > MAX_NAME_LENGTH) {
        errors.name = t('Meshtastic allows at most {{count}} characters', { count: MAX_NAME_LENGTH });
    }
    const psk = form.psk.trim();
    if (psk !== '') {
        const body = psk.replace(/=+$/, '');
        const bytes = Math.floor((body.length * 3) / 4);
        if (!/^[A-Za-z0-9+/_-]+$/.test(body) || ![1, 16, 32].includes(bytes)) {
            errors.psk = t('The key must be base64 for 1, 16 or 32 bytes. Press Generate for a new one.');
        }
    }
    return errors;
}

function apiError(err: any): string {
    return err?.response?.data?.error || err?.message || t('Something went wrong');
}

function notifySuccess(message: string) {
    notifications.show({ message, color: 'green', icon: <Checkmark20Regular /> });
}

function HowItWorks() {
    const items = [
        {
            icon: <Add20Regular />,
            title: t('1. Add a channel'),
            body: t('Create a new channel here, or import one your radios already use from its meshtastic.org URL.'),
        },
        {
            icon: <QrCode20Regular />,
            title: t('2. Share it with radios'),
            body: t('Scan the channel\'s QR code, or open its URL, in the Meshtastic app on the phone paired with each radio.'),
        },
        {
            icon: <ArrowSwap20Regular />,
            title: t('3. Bridge to TAK'),
            body: t('Positions and chat flow between the mesh and TAK devices through this server, on channels with uplink and downlink on.'),
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

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <Stack gap={2} style={{ minWidth: 0 }}>
            <Text size="xs" c="dimmed">{label}</Text>
            <Text size="sm" component="div" style={{ overflowWrap: 'anywhere' }}>{children}</Text>
        </Stack>
    );
}

function OnOff({ on }: { on: boolean }) {
    return (
        <Badge variant="light" color={on ? 'teal' : 'gray'} tt="none">
            {on ? t('On') : t('Off')}
        </Badge>
    );
}

function CopyIcon({ value, label }: { value: string; label: string }) {
    return (
        <CopyButton value={value}>
            {({ copied, copy }) => (
                <Tooltip label={copied ? t('Copied') : label}>
                    <ActionIcon variant="subtle" size="sm" aria-label={label} onClick={copy} color={copied ? 'teal' : undefined}>
                        {copied ? <Checkmark20Regular style={ICON_16} /> : <Copy20Regular style={ICON_16} />}
                    </ActionIcon>
                </Tooltip>
            )}
        </CopyButton>
    );
}

function PskField({ psk, hidden }: { psk: string | null; hidden?: boolean }) {
    const [shown, setShown] = useState(false);
    if (hidden) {
        // Read-only admins get neither the key nor the share URL from the server
        return <Text size="sm" c="dimmed">{t('Hidden')}</Text>;
    }
    if (!psk) {
        return (
            <Badge variant="light" color="yellow" tt="none" leftSection={<LockOpen20Regular style={{ width: 12, height: 12 }} />}>
                {t('No encryption')}
            </Badge>
        );
    }
    return (
        <Group gap={4} wrap="nowrap">
            <Text size="sm" ff="monospace" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                {shown ? psk : '••••••••••••'}
            </Text>
            <Tooltip label={shown ? t('Hide key') : t('Show key')}>
                <ActionIcon variant="subtle" size="sm" aria-label={shown ? t('Hide key') : t('Show key')} onClick={() => setShown((s) => !s)}>
                    {shown ? <EyeOff20Regular style={ICON_16} /> : <Eye20Regular style={ICON_16} />}
                </ActionIcon>
            </Tooltip>
            <CopyIcon value={psk} label={t('Copy key')} />
        </Group>
    );
}

function ChannelCard({ channel, readOnly, onQr, onDelete }: {
    channel: Channel;
    readOnly: boolean;
    onQr: () => void;
    onDelete: () => void;
}) {
    return (
        <Panel p="lg" style={{ display: 'flex', flexDirection: 'column', gap: 14 }} data-testid="meshtastic-channel">
            <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
                <Stack gap={4} style={{ minWidth: 0 }}>
                    <Text fw={600} size="lg" style={{ overflowWrap: 'anywhere' }}>{channel.name || t('(no name)')}</Text>
                    <Group gap={6} wrap="wrap">
                        <Badge variant="light" color="blue" tt="none">{presetLabel(channel.modem_preset || 'LONG_FAST')}</Badge>
                        <Badge variant="light" color={channel.lora_region === 'UNSET' ? 'yellow' : 'gray'} tt="none">
                            {channel.lora_region === 'UNSET' ? t('Region not set') : channel.lora_region}
                        </Badge>
                    </Group>
                </Stack>
            </Group>

            <Detail label={t('Key (PSK)')}>
                <PskField psk={channel.psk} hidden={readOnly} />
            </Detail>

            <SimpleGrid cols={{ base: 2, xs: 3 }} spacing="sm" verticalSpacing="sm">
                <Detail label={t('Uplink (mesh to TAK)')}><OnOff on={channel.uplink_enabled} /></Detail>
                <Detail label={t('Downlink (TAK to mesh)')}><OnOff on={channel.downlink_enabled} /></Detail>
                <Detail label={t('Region')}>{channel.lora_region}</Detail>
                <Detail label={t('Hop limit')}>{channel.lora_hop_limit}</Detail>
                <Detail label={t('Position precision')}>{precisionLabel(channel.position_precision)}</Detail>
                <Detail label={t('Transmit')}>
                    {channel.lora_tx_enabled
                        ? (channel.lora_tx_power
                            ? t('On, {{power}} dBm', { power: channel.lora_tx_power })
                            : t('On, region maximum'))
                        : t('Off')}
                </Detail>
            </SimpleGrid>

            <Group justify="space-between" gap="xs" mt="auto" pt="xs" style={{ borderTop: '1px solid var(--raven-border)' }}>
                <Group gap="xs">
                    <Button size="xs" variant="default" leftSection={<QrCode20Regular style={ICON_16} />} onClick={onQr} disabled={!channel.url}>
                        {t('Show QR')}
                    </Button>
                    <CopyButton value={channel.url ?? ''}>
                        {({ copied, copy }) => (
                            <Button
                                size="xs"
                                variant="default"
                                disabled={!channel.url}
                                leftSection={copied ? <Checkmark20Regular style={ICON_16} /> : <Copy20Regular style={ICON_16} />}
                                onClick={copy}
                            >
                                {copied ? t('Copied') : t('Copy URL')}
                            </Button>
                        )}
                    </CopyButton>
                </Group>
                <Tooltip label={readOnly ? READ_ONLY_LABEL : t('Delete channel')}>
                    <span style={{ display: 'inline-flex' }}>
                        <Button
                            size="xs"
                            variant="subtle"
                            color="red"
                            disabled={readOnly}
                            leftSection={<Delete20Regular style={ICON_16} />}
                            onClick={onDelete}
                        >
                            {t('Delete')}
                        </Button>
                    </span>
                </Tooltip>
            </Group>
        </Panel>
    );
}

function QrModal({ channel, onClose }: { channel: { name: string; url: string } | null; onClose: () => void }) {
    return (
        <Modal opened={channel !== null} onClose={onClose} title={t('Share {{name}}', { name: channel?.name ?? '', interpolation: { escapeValue: false } })}>
            {channel && (
                <Stack gap="md" align="center">
                    {/* QR codes need a white quiet zone in both themes to scan. */}
                    <Box p="md" bg="#fff" style={{ border: '1px solid var(--raven-border)', borderRadius: 'var(--mantine-radius-md)' }}>
                        <QRCode value={channel.url} size={280} quietZone={10} ecLevel="L" qrStyle="squares" />
                    </Box>
                    <Text size="sm" c="dimmed" ta="center">
                        {t('Scan it in the Meshtastic app on the phone paired with the radio, or send the URL and open it there.')}
                    </Text>
                    <TextInput
                        w="100%"
                        readOnly
                        label={t('Channel URL')}
                        value={channel.url}
                        onFocus={(e) => e.currentTarget.select()}
                        rightSection={<CopyIcon value={channel.url} label={t('Copy URL')} />}
                        styles={{ input: { fontFamily: 'monospace', fontSize: 12 } }}
                    />
                </Stack>
            )}
        </Modal>
    );
}

function ImportModal({ opened, onClose, onAdded }: { opened: boolean; onClose: () => void; onAdded: () => void }) {
    const [url, setUrl] = useState('');
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (opened) {
            setUrl('');
            setError('');
        }
    }, [opened]);

    const submit = (e: React.FormEvent) => {
        e.preventDefault();
        if (url.trim() === '') {
            setError(t('Paste the channel URL'));
            return;
        }
        setSaving(true);
        axios
            .post(apiRoutes.meshtasticChannels, { url: url.trim() })
            .then(() => {
                notifySuccess(t('Channel added'));
                onAdded();
                onClose();
            })
            .catch((err) => setError(apiError(err)))
            .finally(() => setSaving(false));
    };

    return (
        <Modal opened={opened} onClose={onClose} title={t('Import a channel from its URL')}>
            <form onSubmit={submit}>
                <Stack gap="md">
                    <TextInput
                        data-autofocus
                        label={t('Channel URL')}
                        description={t('The meshtastic.org link the Meshtastic app shares. A link with several channels adds them all.')}
                        placeholder="https://meshtastic.org/e/#CgMSAQESDAgBOAFAA0gBUB5oAQ"
                        value={url}
                        error={error || undefined}
                        onChange={(e) => {
                            setError('');
                            setUrl(e.currentTarget.value);
                        }}
                    />
                    <Group justify="flex-end" gap="xs">
                        <Button variant="default" onClick={onClose}>{t('Cancel')}</Button>
                        <Button type="submit" loading={saving} leftSection={<ArrowImport20Regular />}>{t('Import')}</Button>
                    </Group>
                </Stack>
            </form>
        </Modal>
    );
}

function NewChannelModal({ opened, onClose, onAdded }: {
    opened: boolean;
    onClose: () => void;
    onAdded: (channel: { name: string; url: string } | null) => void;
}) {
    const [form, setForm] = useState<NewChannel>(NEW_CHANNEL_DEFAULTS);
    const [errors, setErrors] = useState<Partial<Record<keyof NewChannel, string>>>({});
    const [serverError, setServerError] = useState('');
    const [saving, setSaving] = useState(false);
    const [generating, setGenerating] = useState(false);

    useEffect(() => {
        if (opened) {
            setForm(NEW_CHANNEL_DEFAULTS);
            setErrors({});
            setServerError('');
        }
    }, [opened]);

    const setField = <K extends keyof NewChannel>(key: K, value: NewChannel[K]) => {
        setForm((f) => ({ ...f, [key]: value }));
        setErrors((e) => ({ ...e, [key]: undefined }));
        setServerError('');
    };

    const generatePsk = () => {
        setGenerating(true);
        axios
            .get(apiRoutes.meshtasticPsk)
            .then((r) => setField('psk', r.data.psk))
            .catch((err) => setServerError(apiError(err)))
            .finally(() => setGenerating(false));
    };

    const submit = (e: React.FormEvent) => {
        e.preventDefault();
        const found = validateChannel(form);
        if (Object.keys(found).length > 0) {
            setErrors(found);
            return;
        }
        setSaving(true);
        const payload = { ...form, name: form.name.trim(), psk: form.psk.trim(), url: '' };
        axios
            .post(apiRoutes.meshtasticChannels, payload)
            .then((r) => {
                notifySuccess(t('Channel added'));
                onClose();
                onAdded(r.data?.url ? { name: payload.name, url: r.data.url } : null);
            })
            .catch((err) => setServerError(apiError(err)))
            .finally(() => setSaving(false));
    };

    return (
        <Modal opened={opened} onClose={onClose} title={t('New channel')} size="lg">
            <form onSubmit={submit}>
                <Stack gap="md">
                    {serverError && (
                        <Alert color="red" variant="light" icon={<Warning20Regular />} title={t('The channel was not added')}>
                            {serverError}
                        </Alert>
                    )}
                    <TextInput
                        data-autofocus
                        label={t('Name')}
                        description={t('Up to {{count}} characters. Every radio on the channel uses the same name.', { count: MAX_NAME_LENGTH })}
                        value={form.name}
                        error={errors.name}
                        onChange={(e) => setField('name', e.currentTarget.value)}
                    />
                    <TextInput
                        label={t('Key (PSK)')}
                        description={t('Base64. Generate a random 256-bit key, or leave it empty for an unencrypted channel.')}
                        value={form.psk}
                        error={errors.psk}
                        onChange={(e) => setField('psk', e.currentTarget.value)}
                        styles={{ input: { fontFamily: 'monospace' } }}
                        rightSectionWidth={110}
                        rightSection={
                            <Button
                                size="compact-xs"
                                variant="subtle"
                                loading={generating}
                                leftSection={<Key20Regular style={{ width: 14, height: 14 }} />}
                                onClick={generatePsk}
                            >
                                {t('Generate')}
                            </Button>
                        }
                    />

                    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                        <Switch
                            label={t('Uplink')}
                            description={t('Radios send what they hear on the mesh to the server')}
                            checked={form.uplink_enabled}
                            onChange={(e) => setField('uplink_enabled', e.currentTarget.checked)}
                        />
                        <Switch
                            label={t('Downlink')}
                            description={t('The server sends TAK positions and chat out to the mesh')}
                            checked={form.downlink_enabled}
                            onChange={(e) => setField('downlink_enabled', e.currentTarget.checked)}
                        />
                    </SimpleGrid>

                    <Select
                        label={t('Position precision')}
                        description={t('How exactly radios on this channel share their location')}
                        data={PRECISIONS.map((bits) => ({ value: String(bits), label: precisionLabel(bits) }))}
                        allowDeselect={false}
                        value={String(form.position_precision)}
                        onChange={(v) => setField('position_precision', Number(v ?? 32))}
                    />

                    <Text size="xs" c="dimmed" tt="uppercase" fw={600} mt="xs">{t('Radio settings')}</Text>
                    <Text size="xs" c="dimmed" mt={-10}>
                        {t('A radio that takes this channel also takes these. They must match the other radios on your mesh.')}
                    </Text>
                    <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
                        <Select
                            label={t('Region')}
                            description={t('The LoRa band your country allows')}
                            data={REGIONS}
                            searchable
                            allowDeselect={false}
                            value={form.lora_region}
                            onChange={(v) => setField('lora_region', v ?? 'UNSET')}
                        />
                        <Select
                            label={t('Modem preset')}
                            description={t('Range against speed; Long Fast is the usual choice')}
                            data={MODEM_PRESETS.map((p) => ({ value: p, label: presetLabel(p) }))}
                            allowDeselect={false}
                            value={form.modem_preset}
                            onChange={(v) => setField('modem_preset', v ?? 'LONG_FAST')}
                        />
                        <NumberInput
                            label={t('Hop limit')}
                            description={t('How many times a message is relayed (1 to 7)')}
                            min={1}
                            max={7}
                            clampBehavior="strict"
                            allowDecimal={false}
                            value={form.lora_hop_limit}
                            onChange={(v) => setField('lora_hop_limit', Number(v) || 3)}
                        />
                        <NumberInput
                            label={t('Transmit power (dBm)')}
                            description={t('0 uses the region\'s legal maximum')}
                            min={0}
                            max={30}
                            clampBehavior="strict"
                            allowDecimal={false}
                            value={form.lora_tx_power}
                            onChange={(v) => setField('lora_tx_power', Number(v) || 0)}
                        />
                        <Switch
                            label={t('Transmit')}
                            description={t('Off makes radios listen only')}
                            checked={form.lora_tx_enabled}
                            onChange={(e) => setField('lora_tx_enabled', e.currentTarget.checked)}
                        />
                        <Switch
                            label={t('Boosted receive gain')}
                            description={t('SX126x radios only; slightly better range for more power')}
                            checked={form.lora_sx126x_rx_boosted_gain}
                            onChange={(e) => setField('lora_sx126x_rx_boosted_gain', e.currentTarget.checked)}
                        />
                    </SimpleGrid>

                    <Group justify="flex-end" gap="xs" mt="xs">
                        <Button variant="default" onClick={onClose}>{t('Cancel')}</Button>
                        <Button type="submit" loading={saving} leftSection={<Add20Regular />}>{t('Add channel')}</Button>
                    </Group>
                </Stack>
            </form>
        </Modal>
    );
}

/** Meshtastic channels the server bridges: list, create, import, share by QR/URL and delete. */
export default function Meshtastic() {
    const readOnly = isReadOnlyAdmin();
    const [channels, setChannels] = useState<Channel[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState('');
    const [turnedOff, setTurnedOff] = useState(false);

    const [qrChannel, setQrChannel] = useState<{ name: string; url: string } | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<Channel | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [importOpen, setImportOpen] = useState(false);
    const [newOpen, setNewOpen] = useState(false);

    const load = useCallback(() => {
        setLoading(true);
        axios
            .get(apiRoutes.meshtasticChannels, {
                params: { page, per_page: PAGE_SIZE, sort_by: 'name', sort_direction: 'asc' },
            })
            .then((r) => {
                setChannels(r.data?.results ?? []);
                setTotal(r.data?.total ?? 0);
                setLoadError('');
            })
            .catch((err) => {
                if (err?.response?.status === 404) {
                    // Turned off on the server since the page checked
                    setTurnedOff(true);
                } else {
                    setLoadError(apiError(err));
                }
            })
            .finally(() => setLoading(false));
    }, [page]);

    useEffect(load, [load]);

    const removeChannel = () => {
        if (!deleteTarget) {return;}
        setDeleting(true);
        axios
            .delete(apiRoutes.meshtasticChannels, { params: { url: deleteTarget.url } })
            .then(() => {
                notifySuccess(t('Channel deleted'));
                setDeleteTarget(null);
                // The last card on a later page: step back a page
                if (channels.length === 1 && page > 1) {
                    setPage(page - 1);
                } else {
                    load();
                }
            })
            .catch((err) =>
                notifications.show({ title: t('Couldn\'t delete the channel'), message: apiError(err), color: 'red', icon: <Dismiss20Regular /> }))
            .finally(() => setDeleting(false));
    };

    if (turnedOff) {
        return (
            <Stack gap="lg">
                <PageHeader title={t('Meshtastic')} />
                <MeshtasticOffNotice />
            </Stack>
        );
    }

    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const writeActions = (
        <>
            <ReadOnlyTip>
                <Button variant="default" disabled={readOnly} leftSection={<ArrowImport20Regular />} onClick={() => setImportOpen(true)}>
                    {t('Import from URL')}
                </Button>
            </ReadOnlyTip>
            <ReadOnlyTip>
                <Button disabled={readOnly} leftSection={<Add20Regular />} onClick={() => setNewOpen(true)}>
                    {t('Add New Channel')}
                </Button>
            </ReadOnlyTip>
        </>
    );

    return (
        <Stack gap="lg">
            <PageHeader
                title={t('Meshtastic')}
                description={t('Bridge Meshtastic radio channels with TAK devices')}
                actions={(
                    <>
                        <Tooltip label={t('Refresh')}>
                            <ActionIcon variant="default" size="lg" aria-label={t('Refresh')} onClick={load}>
                                <ArrowClockwise20Regular />
                            </ActionIcon>
                        </Tooltip>
                        {writeActions}
                    </>
                )}
            />

            <ReadOnlyNotice />

            <HowItWorks />

            {loadError && (
                <Alert color="red" variant="light" icon={<Warning20Regular />} title={t('Couldn\'t load channels')}>
                    {loadError}
                </Alert>
            )}

            <Section
                title={t('Channels')}
                actions={!loading && total > 0 ? (
                    <Text size="sm" c="dimmed">{t('{{count}} channel(s)', { count: total })}</Text>
                ) : undefined}
            >
                {loading && channels.length === 0 ? (
                    <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }} spacing="md">
                        <Skeleton height={260} />
                        <Skeleton height={260} />
                    </SimpleGrid>
                ) : channels.length === 0 && !loadError ? (
                    <Panel p="lg">
                        <EmptyState
                            icon={<Router20Regular style={EMPTY_ICON} />}
                            color="blue"
                            title={t('No channels yet')}
                            description={t('Add a channel so this server can relay positions and chat between Meshtastic radios and TAK devices.')}
                            action={<Group justify="center" gap="xs" mt="sm">{writeActions}</Group>}
                        />
                    </Panel>
                ) : (
                    <Stack gap="md">
                        <SimpleGrid cols={{ base: 1, md: 2, xl: 3 }} spacing="md">
                            {channels.map((c) => (
                                <ChannelCard
                                    key={c.url}
                                    channel={c}
                                    readOnly={readOnly}
                                    onQr={() => setQrChannel({ name: c.name, url: c.url })}
                                    onDelete={() => setDeleteTarget(c)}
                                />
                            ))}
                        </SimpleGrid>
                        {pages > 1 && (
                            <Group justify="center">
                                <Pagination total={pages} value={page} onChange={setPage} />
                            </Group>
                        )}
                    </Stack>
                )}
            </Section>

            <QrModal channel={qrChannel} onClose={() => setQrChannel(null)} />

            <ImportModal opened={importOpen && !readOnly} onClose={() => setImportOpen(false)} onAdded={load} />

            <NewChannelModal
                opened={newOpen && !readOnly}
                onClose={() => setNewOpen(false)}
                onAdded={(created) => {
                    load();
                    // Straight on to sharing it with the radios
                    if (created) {setQrChannel(created);}
                }}
            />

            <Modal
                opened={deleteTarget !== null && !readOnly}
                onClose={() => setDeleteTarget(null)}
                title={t('Delete channel {{name}}?', { name: deleteTarget?.name ?? '', interpolation: { escapeValue: false } })}
            >
                <Text size="sm" mb="md">
                    {t('The server stops bridging this channel between the mesh and TAK. Radios keep the channel until it is removed from them. This can\'t be undone, but you can import the channel again from its URL.')}
                </Text>
                <Group justify="flex-end" gap="xs">
                    <Button variant="default" onClick={() => setDeleteTarget(null)}>{t('Cancel')}</Button>
                    <Button color="red" loading={deleting} leftSection={<Delete20Regular />} onClick={removeChannel}>
                        {t('Delete')}
                    </Button>
                </Group>
            </Modal>
        </Stack>
    );
}
