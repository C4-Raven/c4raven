/** Small building blocks shared by the Federation Hub page, cards and wizard. */
import React, { useState } from 'react';
import { ActionIcon, Alert, Anchor, Badge, Button, Code, CopyButton, Group, Stack, Text, ThemeIcon, Tooltip } from '@mantine/core';
import { Checkmark20Regular, Copy20Regular, ErrorCircle20Regular, Live20Regular, Open20Regular, Organization20Regular, Play20Regular, Server20Regular } from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import { isReadOnlyAdmin } from '../../adminAccess';
import { ReadOnlyTip } from '../../components/ReadOnlyTip';
import { extractError, PulseDot } from '../ServerRequests/shared';
import { activateFederation } from './api';
import { formatFingerprint, kindLabel, shortFingerprint, statusView, type PartnerErrorView } from './helpers';
import type { PartnerKind, PartnerStatus } from './types';

/** Window event the Advanced section listens for: opens it (optionally on a tab) and scrolls to it. */
export const OPEN_ADVANCED_EVENT = 'fedhub:open-advanced';

/** Icon size inside an EmptyState's 52px tile. */
export const EMPTY_ICON = { width: 28, height: 28 };

export function openAdvanced(tab?: string) {
    window.dispatchEvent(new CustomEvent(OPEN_ADVANCED_EVENT, { detail: { tab } }));
}

/**
 * A partner-route error, explained: "Open existing partner" for *_exists,
 * activate-a-policy buttons for no_active_policy (then `onRetry`), and a
 * pointer to Advanced when the hub may be left half changed.
 */
export function ServerErrorAlert({ view, title, onOpenPartner, onRetry, children }: {
    view: PartnerErrorView;
    title: string;
    onOpenPartner?: (id: string) => void;
    onRetry?: () => void;
    children?: React.ReactNode;
}) {
    const { t } = useTranslation();
    const readOnly = isReadOnlyAdmin();
    const [activating, setActivating] = useState<string | null>(null);
    const [activateError, setActivateError] = useState<string>();

    async function activate(name: string) {
        setActivating(name);
        setActivateError(undefined);
        try {
            await activateFederation(name);
            onRetry?.();
        } catch (err) {
            setActivateError(await extractError(err) ?? t('The policy could not be activated'));
        } finally {
            setActivating(null);
        }
    }

    return (
        <Alert color="red" variant="light" icon={<ErrorCircle20Regular />} title={title}>
            <Stack gap="xs">
                <Text size="sm">{view.message}</Text>
                {view.existingPartnerId && onOpenPartner && (
                    <Group>
                        <Button size="xs" variant="light" color="red" leftSection={<Open20Regular style={{ width: 14, height: 14 }} />} onClick={() => onOpenPartner(view.existingPartnerId!)}>
                            {t('Open existing partner')}
                        </Button>
                    </Group>
                )}
                {view.policies.length > 0 && (
                    <Group gap="xs" wrap="wrap">
                        {view.policies.map((name) => (
                            <ReadOnlyTip key={name}>
                                <Button
                                    size="xs"
                                    variant="light"
                                    leftSection={<Play20Regular style={{ width: 14, height: 14 }} />}
                                    loading={activating === name}
                                    disabled={readOnly || (activating !== null && activating !== name)}
                                    onClick={() => activate(name)}
                                >
                                    {onRetry ? t('Activate "{{name}}" and try again', { name, interpolation: { escapeValue: false } }) : t('Activate "{{name}}"', { name, interpolation: { escapeValue: false } })}
                                </Button>
                            </ReadOnlyTip>
                        ))}
                    </Group>
                )}
                {activateError && <Text size="xs" c="red">{activateError}</Text>}
                {view.halfChanged && (
                    <Text size="xs">
                        {t('The hub may be left half changed.')}{' '}
                        <Anchor component="button" type="button" size="xs" onClick={() => openAdvanced('policy')}>
                            {t('Check the policy under Advanced')}
                        </Anchor>
                    </Text>
                )}
                {children}
            </Stack>
        </Alert>
    );
}

export function CopyIcon({ value, label }: { value: string; label: string }) {
    const { t } = useTranslation();
    return (
        <CopyButton value={value} timeout={1500}>
            {({ copied, copy }) => (
                <Tooltip label={copied ? t('Copied') : label} withArrow>
                    <ActionIcon
                        variant="subtle"
                        color={copied ? 'teal' : 'gray'}
                        size="sm"
                        onClick={copy}
                        aria-label={label}
                    >
                        {copied ? <Checkmark20Regular style={{ width: 14, height: 14 }} /> : <Copy20Regular style={{ width: 14, height: 14 }} />}
                    </ActionIcon>
                </Tooltip>
            )}
        </CopyButton>
    );
}

/**
 * SHA-256 fingerprint, shortened, with the full value on hover and a copy
 * button. Every Raven hub CA has the same name (G7), so this is the only
 * reliable way to tell certificates apart.
 */
export function Fingerprint({ sha256, label }: { sha256: string | null | undefined; label?: string }) {
    const { t } = useTranslation();
    if (!sha256) { return <Text size="xs" c="dimmed">{t('No certificate')}</Text>; }
    const full = formatFingerprint(sha256);
    return (
        <Group gap={4} wrap="nowrap" style={{ minWidth: 0 }}>
            {label && <Text size="xs" c="dimmed" style={{ flexShrink: 0 }}>{label}</Text>}
            <Tooltip label={<span style={{ wordBreak: 'break-all' }}>{full}</span>} multiline maw={360} withArrow>
                <Code style={{ fontSize: 11, whiteSpace: 'nowrap' }}>{shortFingerprint(sha256)}</Code>
            </Tooltip>
            <CopyIcon value={full} label={t('Copy SHA-256 fingerprint')} />
        </Group>
    );
}

/** host:port in monospace with a copy button. */
export function CopyAddress({ value }: { value: string }) {
    const { t } = useTranslation();
    return (
        <Group gap={4} wrap="nowrap">
            <Code style={{ fontSize: 13 }}>{value}</Code>
            <CopyIcon value={value} label={t('Copy address')} />
        </Group>
    );
}

export function KindIcon({ kind }: { kind: PartnerKind }) {
    const { t } = useTranslation();
    const icon = kind === 'raven_hub'
        ? <Organization20Regular />
        : kind === 'other'
            ? <Live20Regular />
            : <Server20Regular />;
    return (
        <Tooltip label={kindLabel(kind, t)}>
            <ThemeIcon variant="light" size="lg" color={kind === 'raven_hub' ? 'indigo' : kind === 'other' ? 'cyan' : 'gray'} aria-label={kindLabel(kind, t)}>
                {icon}
            </ThemeIcon>
        </Tooltip>
    );
}

export function StatusBadge({ status }: { status: PartnerStatus }) {
    const { t } = useTranslation();
    const v = statusView(status, t);
    return (
        <Tooltip label={v.description} multiline maw={280}>
            <Badge
                variant="light"
                color={v.color}
                tt="none"
                leftSection={v.pulse
                    ? <PulseDot active color={`var(--mantine-color-${v.color}-5)`} />
                    : <span aria-hidden style={{ display: 'inline-block', width: 8, height: 8, background: `var(--mantine-color-${v.color}-6)` }} />}
            >
                {v.label}
            </Badge>
        </Tooltip>
    );
}
