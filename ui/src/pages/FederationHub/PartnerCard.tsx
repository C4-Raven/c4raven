import React from 'react';
import { ActionIcon, Alert, Badge, Button, Group, Menu, SimpleGrid, Stack, Switch, Text, Tooltip } from '@mantine/core';
import { ArrowClockwise20Regular, ArrowDownLeft20Regular, ArrowDownload20Regular, ArrowUpRight20Regular, Delete20Regular, Edit20Regular, Info20Regular, MoreHorizontal20Regular, PlugDisconnected20Regular, Stethoscope20Regular, Warning20Regular } from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import { directionLabel, formatBytes, formatCount, isUnlinkedOutgoing, problemColor, problemText, shareSummary, unlinkedOutgoingHint } from './helpers';
import { Panel } from '../../components/ui';
import { ReadOnlyTip } from '../../components/ReadOnlyTip';
import { KindIcon, StatusBadge } from './parts';
import type { Partner } from './types';

export default function PartnerCard({ partner, busy, readOnly = false, onEdit, onCheck, onReconnect, onRemove, onToggle, onStopDialling, onInstructions }: {
    partner: Partner;
    busy?: boolean;
    /** Read-only admin: every control is shown but disabled (the More menu still opens, its items disabled). */
    readOnly?: boolean;
    onEdit: () => void;
    onCheck: () => void;
    onReconnect: () => void;
    onRemove: () => void;
    onToggle: (enabled: boolean) => void;
    /**
     * Both sides dial: drop OUR outgoing connection. For an outgoing-only
     * partner that removes it (keeping any CA); for a CA partner it switches
     * to "They connect to us".
     */
    onStopDialling?: () => void;
    /** Download the other admin's instructions for this partner (kind and direction as set up). */
    onInstructions?: () => void;
}) {
    const { t } = useTranslation();
    const share = shareSummary(partner.share, t);
    const receive = shareSummary(partner.receive, t);
    const traffic = partner.traffic ?? { msgs_out: 0, msgs_in: 0, bytes_out: 0, bytes_in: 0 };
    const unlinked = isUnlinkedOutgoing(partner);
    const bothDial = partner.problems?.includes('both_sides_dial');
    const otherProblems = (partner.problems ?? []).filter((code) => code !== 'both_sides_dial');

    return (
        <Panel
            p="lg"
            style={{ opacity: partner.enabled ? 1 : 0.72, display: 'flex', flexDirection: 'column', gap: 12 }}
        >
            <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
                <Group gap="sm" wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
                    <KindIcon kind={partner.kind} />
                    <Stack gap={4} style={{ minWidth: 0 }}>
                        <Text fw={700} size="lg" style={{ wordBreak: 'break-word', lineHeight: 1.2 }}>{partner.name}</Text>
                        <Group gap={6} wrap="wrap">
                            <StatusBadge status={partner.status} />
                            <Badge
                                variant="light"
                                color={partner.direction === 'we_dial' ? 'indigo' : 'grape'}
                                tt="none"
                                leftSection={partner.direction === 'we_dial' ? <ArrowUpRight20Regular style={{ width: 12, height: 12 }} /> : <ArrowDownLeft20Regular style={{ width: 12, height: 12 }} />}
                                maw={260}
                            >
                                {directionLabel(partner, t)}
                            </Badge>
                        </Group>
                    </Stack>
                </Group>
                <Tooltip label={readOnly ? (partner.enabled ? t('On') : t('Paused')) : partner.enabled ? t('On. Click to pause this partner.') : t('Paused. Click to turn it back on.')}>
                    <div>
                        <Switch
                            checked={partner.enabled}
                            disabled={busy || readOnly}
                            onChange={(e) => onToggle(e.currentTarget.checked)}
                            aria-label={t('Enable {{name}}', { name: partner.name, interpolation: { escapeValue: false } })}
                            size="md"
                        />
                    </div>
                </Tooltip>
            </Group>

            {!partner.ca && <Text size="xs" c="dimmed">{t('Not linked to a trusted certificate')}</Text>}

            <Stack gap={6}>
                <Group gap={6} wrap="wrap">
                    <Text size="xs" c="dimmed" w={70}>{t('We send')}</Text>
                    <Tooltip label={share.detail} multiline maw={300}>
                        <Badge variant="light" color={share.color} tt="none">{share.label}</Badge>
                    </Tooltip>
                </Group>
                <Group gap={6} wrap="wrap">
                    <Text size="xs" c="dimmed" w={70}>{t('We accept')}</Text>
                    <Tooltip label={receive.detail} multiline maw={300}>
                        <Badge variant="light" color={receive.color} tt="none">{receive.label}</Badge>
                    </Tooltip>
                    {partner.interconnected && (
                        <Tooltip label={t('Servers under this partner\'s certificate authority can also talk to each other.')} multiline maw={280}>
                            <Badge variant="light" color="gray" tt="none">{t('Interconnected')}</Badge>
                        </Tooltip>
                    )}
                </Group>
            </Stack>

            <SimpleGrid cols={2} spacing="xs">
                <div>
                    <Text size="xs" c="dimmed">{t('Sent')}</Text>
                    <Text size="sm" fw={600}>
                        {t('{{count}} messages', { count: formatCount(traffic.msgs_out), interpolation: { escapeValue: false } })}
                        <Text span size="xs" c="dimmed"> · {formatBytes(traffic.bytes_out)}</Text>
                    </Text>
                </div>
                <div>
                    <Text size="xs" c="dimmed">{t('Received')}</Text>
                    <Text size="sm" fw={600}>
                        {t('{{count}} messages', { count: formatCount(traffic.msgs_in), interpolation: { escapeValue: false } })}
                        <Text span size="xs" c="dimmed"> · {formatBytes(traffic.bytes_in)}</Text>
                    </Text>
                </div>
            </SimpleGrid>

            {bothDial && (
                <Alert color="red" variant="light" icon={<Warning20Regular />} py="xs">
                    <Stack gap={6}>
                        <Text size="xs">{problemText('both_sides_dial', t)}</Text>
                        {partner.direction === 'we_dial' && onStopDialling && (
                            <Group>
                                <ReadOnlyTip>
                                    <Button size="xs" color="red" variant="light" leftSection={<PlugDisconnected20Regular style={{ width: 14, height: 14 }} />} disabled={busy || readOnly} onClick={onStopDialling}>
                                        {unlinked ? t('Remove this outgoing connection') : t('Stop connecting to them')}
                                    </Button>
                                </ReadOnlyTip>
                            </Group>
                        )}
                    </Stack>
                </Alert>
            )}

            {unlinked && (
                <Alert color="yellow" variant="light" icon={<Info20Regular />} py="xs">
                    <Text size="xs">{unlinkedOutgoingHint(t)}</Text>
                </Alert>
            )}

            {otherProblems.length > 0 && (
                <Stack gap={4}>
                    {otherProblems.map((code) => (
                        <Text key={code} size="xs" c={problemColor(code) === 'red' ? 'red' : 'yellow.8'}>{problemText(code, t)}</Text>
                    ))}
                </Stack>
            )}

            <Group justify="space-between" align="center" mt="auto" gap="xs" wrap="nowrap">
                <Text size="xs" c="dimmed">
                    {partner.connections?.length
                        ? t('{{count}} live connections', { count: partner.connections.length, interpolation: { escapeValue: false } })
                        : t('No live connection')}
                </Text>
                <Group gap={6} wrap="nowrap">
                    {readOnly ? (
                        <>
                            <ReadOnlyTip>
                                <ActionIcon variant="subtle" size="lg" aria-label={t('Check connection to {{name}}', { name: partner.name, interpolation: { escapeValue: false } })} disabled>
                                    <Stethoscope20Regular />
                                </ActionIcon>
                            </ReadOnlyTip>
                            <ReadOnlyTip>
                                <ActionIcon variant="subtle" size="lg" aria-label={t('Edit {{name}}', { name: partner.name, interpolation: { escapeValue: false } })} disabled>
                                    <Edit20Regular />
                                </ActionIcon>
                            </ReadOnlyTip>
                        </>
                    ) : (
                        <>
                            <Tooltip label={t('Check connection')}>
                                <ActionIcon variant="subtle" size="lg" aria-label={t('Check connection to {{name}}', { name: partner.name, interpolation: { escapeValue: false } })} onClick={onCheck}>
                                    <Stethoscope20Regular />
                                </ActionIcon>
                            </Tooltip>
                            <Tooltip label={t('Edit')}>
                                <ActionIcon variant="subtle" size="lg" aria-label={t('Edit {{name}}', { name: partner.name, interpolation: { escapeValue: false } })} onClick={onEdit}>
                                    <Edit20Regular />
                                </ActionIcon>
                            </Tooltip>
                        </>
                    )}
                    <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                            <ActionIcon variant="subtle" size="lg" aria-label={t('More actions')}>
                                <MoreHorizontal20Regular />
                            </ActionIcon>
                        </Menu.Target>
                        <Menu.Dropdown>
                            <Menu.Item leftSection={<ArrowClockwise20Regular style={{ width: 16, height: 16 }} />} onClick={onReconnect} disabled={busy || readOnly}>
                                {t('Reconnect')}
                            </Menu.Item>
                            {onInstructions && (
                                <Menu.Item leftSection={<ArrowDownload20Regular style={{ width: 16, height: 16 }} />} onClick={onInstructions} disabled={readOnly}>
                                    {t('Instructions for the other admin')}
                                </Menu.Item>
                            )}
                            <Menu.Divider />
                            <Menu.Item color="red" leftSection={<Delete20Regular style={{ width: 16, height: 16 }} />} onClick={onRemove} disabled={readOnly}>{t('Remove')}</Menu.Item>
                        </Menu.Dropdown>
                    </Menu>
                </Group>
            </Group>
        </Panel>
    );
}
