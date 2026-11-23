import React from 'react';
import { Center, Group, Stack, Text, ThemeIcon } from '@mantine/core';
import { format, formatDistanceToNowStrict, isValid, parseISO } from 'date-fns';
import axios from '../../axios_config';
import { Panel } from '@/components/ui';
import classes from './ServerRequests.module.css';

/** "delete_old_data" / "deleteOldData" -> "Delete old data". */
export function humanize(value: string | null | undefined): string {
    if (!value) { return ''; }
    const spaced = value
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
        .replace(/[_\-.]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .toLowerCase();
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function parseDate(value: string | null | undefined): Date | null {
    if (!value) { return null; }
    const d = parseISO(value);
    return isValid(d) ? d : null;
}

/** "Fri 31 Oct 2026, 14:05" in the browser's local time zone. */
export function formatDateTime(value: string | Date | null | undefined, withSeconds = false): string {
    const d = value instanceof Date ? value : parseDate(value);
    if (!d) { return '—'; }
    return format(d, withSeconds ? 'EEE d MMM yyyy, HH:mm:ss' : 'EEE d MMM yyyy, HH:mm');
}

/** "in 3 minutes" / "5 hours ago". */
export function formatRelative(value: string | Date | null | undefined): string {
    const d = value instanceof Date ? value : parseDate(value);
    if (!d) { return ''; }
    return formatDistanceToNowStrict(d, { addSuffix: true });
}

/** Pull a human-readable error out of an axios error, including blob bodies. */
export async function extractError(err: any): Promise<string | undefined> {
    const data = err?.response?.data;
    try {
        if (data instanceof Blob) {
            const text = await data.text();
            try {
                return JSON.parse(text)?.error ?? JSON.parse(text)?.message;
            } catch {
                return text.slice(0, 300) || undefined;
            }
        }
        return data?.error ?? data?.message ?? err?.message;
    } catch {
        return err?.message;
    }
}

function filenameFromDisposition(header: string | undefined): string | null {
    if (!header) { return null; }
    const star = /filename\*\s*=\s*(?:UTF-8|utf-8)?''([^;]+)/.exec(header);
    if (star) {
        try {
            return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''));
        } catch {
            // fall through to the plain filename parameter
        }
    }
    const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
    return plain ? plain[1].trim() : null;
}

/**
 * Download through the shared axios instance (so the session cookie, CSRF
 * header and the 401 -> /login interceptor all apply) and hand the blob to
 * a temporary <a download>. Throws on failure; use extractError() on it.
 */
export async function downloadFile(url: string, params: Record<string, unknown>, fallbackName: string) {
    const r = await axios.get(url, { params, responseType: 'blob' });
    const name = filenameFromDisposition(r.headers['content-disposition']) ?? fallbackName;
    const href = URL.createObjectURL(r.data as Blob);
    const a = document.createElement('a');
    a.href = href;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
}

/** Fluent icons render at 20px; these shrink them for badges and small buttons. */
export const ICON_12 = { width: 12, height: 12 };
export const ICON_16 = { width: 16, height: 16 };

/** DataTable props for a flat table sitting inside one Panel (no own border or background). */
export const flatTable = {
    backgroundColor: 'transparent',
    borderColor: 'var(--raven-border)',
    rowBorderColor: 'var(--raven-border)',
} as const;

/** Red tint for rows whose `success` is false (audit and admin event tables). */
export function failedRowColor(e: { success: boolean | null }) {
    return e.success === false
        ? { light: 'rgba(250, 82, 82, 0.08)', dark: 'rgba(250, 82, 82, 0.12)' }
        : undefined;
}

/**
 * Titled Panel. No longer used by the Server Requests tabs (they use the
 * components/ui Section + Panel); kept for MissionDetail and FederationHub.
 */
export function Section({ title, description, actions, children, p = 'lg' }: {
    title?: React.ReactNode;
    description?: React.ReactNode;
    actions?: React.ReactNode;
    children: React.ReactNode;
    p?: string;
}) {
    return (
        <Panel p={p}>
            {(title || actions) && (
                <Stack gap={2} mb="md">
                    <Group justify="space-between" gap="sm">
                        {title && <Text fw={600} size="lg">{title}</Text>}
                        {actions}
                    </Group>
                    {description && <Text size="sm" c="dimmed">{description}</Text>}
                </Stack>
            )}
            {children}
        </Panel>
    );
}

export function EmptyState({ icon, title, description, color = 'gray', action }: {
    icon: React.ReactNode;
    title: React.ReactNode;
    description?: React.ReactNode;
    color?: string;
    action?: React.ReactNode;
}) {
    return (
        <Center py={48} px="md">
            <Stack align="center" gap="xs" maw={460} ta="center">
                <ThemeIcon size={52} variant="light" color={color} className={classes.emptyIcon}>{icon}</ThemeIcon>
                <Text fw={600} mt="xs">{title}</Text>
                {description && <Text size="sm" c="dimmed">{description}</Text>}
                {action && <div style={{ pointerEvents: 'auto' }}>{action}</div>}
            </Stack>
        </Center>
    );
}

/** Small pulsing live dot (the existing raven-pulse animation). */
export function PulseDot({ active, color = 'var(--mantine-color-green-5)' }: { active: boolean; color?: string }) {
    return (
        <span
            aria-hidden
            style={{
                display: 'inline-block',
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: active ? color : 'var(--mantine-color-gray-6)',
                animation: active ? 'raven-pulse 1.5s ease-in-out infinite' : undefined,
                flexShrink: 0,
            }}
        />
    );
}
