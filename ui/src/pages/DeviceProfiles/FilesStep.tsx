import React, { useEffect, useState } from 'react';
import {
    ActionIcon,
    Alert,
    Badge,
    Button,
    FileButton,
    Group,
    Stack,
    Text,
    TextInput,
    Tooltip,
} from '@mantine/core';
import {
    ArrowDownload20Regular,
    ArrowUndo20Regular,
    ArrowUpload20Regular,
    Delete20Regular,
    Document20Regular,
    DocumentMultiple20Regular,
    ErrorCircle20Regular,
    Info20Regular,
} from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import axios from '../../axios_config';
import { apiRoutes } from '../../apiRoutes';
import { downloadFile } from '../ServerRequests/shared';
import { isReadOnlyAdmin } from '../../adminAccess';
import {
    cleanFolder,
    formatBytes,
    RESERVED_FOLDERS,
    zipPathFor,
    type PendingFile,
    type ProfileFile,
} from './types';
import classes from './DeviceProfiles.module.css';

export interface FileLimits {
    max_file_bytes: number;
    max_total_bytes: number;
    max_files: number;
}

/** Server limits for profile files, loaded once per editor open. */
export function useFileLimits(opened: boolean): FileLimits | null {
    const [limits, setLimits] = useState<FileLimits | null>(null);
    useEffect(() => {
        if (!opened) { return; }
        axios.get(`${apiRoutes.deviceProfileSets}/files/limits`)
            .then((r) => setLimits(r.data))
            .catch(() => setLimits(null));
    }, [opened]);
    return limits;
}

/** What the zip will hold once the pending changes are saved, by zip path (later entries win). */
export function filesAfterSave(existing: ProfileFile[], pending: PendingFile[], removed: number[]) {
    const out = new Map<string, { zip_path: string; size: number; pending: boolean }>();
    for (const f of existing) {
        if (!removed.includes(f.id)) { out.set(f.zip_path.toLowerCase(), { zip_path: f.zip_path, size: f.size, pending: false }); }
    }
    for (const p of pending) {
        const path = zipPathFor(p.file.name, p.folder);
        out.set(path.toLowerCase(), { zip_path: path, size: p.file.size, pending: true });
    }
    return [...out.values()];
}

/** Checks the pending files against the server's limits, so the admin hears before saving. */
export function filesProblem(
    existing: ProfileFile[],
    pending: PendingFile[],
    removed: number[],
    limits: FileLimits | null,
    t: (k: string, o?: any) => string,
): string | undefined {
    for (const p of pending) {
        const folder = cleanFolder(p.folder);
        if (folder && RESERVED_FOLDERS.includes(folder.split('/')[0].toLowerCase())) {
            return t('The "{{folder}}" folder is reserved for the server', { folder: folder.split('/')[0] });
        }
        if (p.file.size === 0) { return t('{{name}} is empty', { name: p.file.name }); }
    }
    if (!limits) { return undefined; }
    const after = filesAfterSave(existing, pending, removed);
    const big = pending.find((p) => p.file.size > limits.max_file_bytes);
    if (big) {
        return t('{{name}} is larger than the {{max}} limit', { name: big.file.name, max: formatBytes(limits.max_file_bytes) });
    }
    if (after.length > limits.max_files) {
        return t('A profile can have at most {{count}} files', { count: limits.max_files });
    }
    const total = after.reduce((sum, f) => sum + f.size, 0);
    if (total > limits.max_total_bytes) {
        return t("A profile's files can add up to at most {{max}}", { max: formatBytes(limits.max_total_bytes) });
    }
    return undefined;
}

export default function FilesStep({ profileId, existing, pending, removed, limits, error, onChange }: {
    /** Saved profile id, for downloads. Null while creating. */
    profileId: number | null;
    existing: ProfileFile[];
    pending: PendingFile[];
    removed: number[];
    limits: FileLimits | null;
    error?: string;
    onChange: (change: { pending?: PendingFile[]; removed?: number[] }) => void;
}) {
    const { t } = useTranslation();
    // Read-only admins see every control switched off (the server refuses them file downloads too)
    const readOnly = isReadOnlyAdmin();
    const [folder, setFolder] = useState('');

    function addFiles(files: File[]) {
        if (!files.length) { return; }
        const added = files.map((file) => ({
            key: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2)}`,
            file,
            folder,
        }));
        // A newly picked file at the same path replaces an earlier pick
        const paths = new Set(added.map((p) => zipPathFor(p.file.name, p.folder).toLowerCase()));
        onChange({ pending: [...pending.filter((p) => !paths.has(zipPathFor(p.file.name, p.folder).toLowerCase())), ...added] });
    }

    const pendingPaths = new Set(pending.map((p) => zipPathFor(p.file.name, p.folder).toLowerCase()));
    const kept = filesAfterSave(existing, pending, removed);
    const total = kept.reduce((sum, f) => sum + f.size, 0);
    const empty = existing.length === 0 && pending.length === 0;

    return (
        <Stack gap="md">
            <Text size="sm" c="dimmed">
                {t('Attach files that should arrive with these settings: .pref files, map sources, imagery, plugins, certificates or anything else ATAK can import from a data package.')}
            </Text>
            {error && (
                <Alert color="red" variant="light" icon={<ErrorCircle20Regular />} py="xs">{error}</Alert>
            )}

            <div>
                <Group align="flex-end" gap="sm" wrap="wrap">
                    <TextInput
                        style={{ flex: 1, minWidth: 200 }}
                        label={t('Folder in the package')}
                        description={t('Optional, e.g. maps. Leave empty for the top level.')}
                        placeholder={t('Top level')}
                        value={folder}
                        disabled={readOnly}
                        onChange={(e) => setFolder(e.currentTarget.value)}
                    />
                    <FileButton onChange={addFiles} multiple disabled={readOnly}>
                        {(props) => (
                            <Button {...props} variant="default" disabled={readOnly} leftSection={<ArrowUpload20Regular />}>
                                {t('Add files')}
                            </Button>
                        )}
                    </FileButton>
                </Group>
                {limits && (
                    <Text size="xs" c="dimmed" mt="xs">
                        {t('Up to {{count}} files, {{file}} each, {{total}} in total.', {
                            count: limits.max_files,
                            file: formatBytes(limits.max_file_bytes),
                            total: formatBytes(limits.max_total_bytes),
                        })}
                    </Text>
                )}
            </div>

            {empty ? (
                <Group gap="sm" wrap="nowrap" py="xs">
                    <span className={classes.icon}><DocumentMultiple20Regular /></span>
                    <Text size="sm" c="dimmed">
                        {t('No files yet. Files are optional; a profile can be settings only.')}
                    </Text>
                </Group>
            ) : (
                <div>
                    <Group gap="xs" mb="xs">
                        <Text fw={600} size="sm">{t('Files in this profile')}</Text>
                        <Badge variant="light" color="gray">{kept.length}</Badge>
                        <Text size="xs" c="dimmed">{formatBytes(total)}</Text>
                    </Group>
                    <div className={classes.rows}>
                        {existing.map((f) => {
                            const isRemoved = removed.includes(f.id);
                            const isReplaced = !isRemoved && pendingPaths.has(f.zip_path.toLowerCase());
                            return (
                                <Group key={f.id} gap="sm" wrap="nowrap" justify="space-between" style={{ opacity: isRemoved || isReplaced ? 0.55 : 1 }}>
                                    <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
                                        <span className={classes.icon}><Document20Regular /></span>
                                        <div style={{ minWidth: 0 }}>
                                            <Text size="sm" ff="monospace" td={isRemoved ? 'line-through' : undefined} className={classes.mono}>{f.zip_path}</Text>
                                            <Text size="xs" c="dimmed">
                                                {formatBytes(f.size)}
                                                {isRemoved ? ` · ${t('Removed when you save')}` : ''}
                                                {isReplaced ? ` · ${t('Replaced when you save')}` : ''}
                                            </Text>
                                        </div>
                                    </Group>
                                    <Group gap={4} wrap="nowrap">
                                        {profileId !== null && (
                                            <Tooltip label={t('Download')}>
                                                <ActionIcon
                                                    variant="subtle"
                                                    color="gray"
                                                    disabled={readOnly}
                                                    aria-label={t('Download {{name}}', { name: f.filename })}
                                                    onClick={() => downloadFile(`${apiRoutes.deviceProfileSets}/${profileId}/files/${f.id}`, {}, f.filename).catch(() => undefined)}
                                                >
                                                    <ArrowDownload20Regular />
                                                </ActionIcon>
                                            </Tooltip>
                                        )}
                                        {isRemoved ? (
                                            <Tooltip label={t('Keep this file')}>
                                                <ActionIcon variant="subtle" color="gray" disabled={readOnly} aria-label={t('Keep {{name}}', { name: f.filename })} onClick={() => onChange({ removed: removed.filter((id) => id !== f.id) })}>
                                                    <ArrowUndo20Regular />
                                                </ActionIcon>
                                            </Tooltip>
                                        ) : (
                                            <Tooltip label={t('Remove')}>
                                                <ActionIcon variant="subtle" color="red" disabled={readOnly} aria-label={t('Remove {{name}}', { name: f.filename })} onClick={() => onChange({ removed: [...removed, f.id] })}>
                                                    <Delete20Regular />
                                                </ActionIcon>
                                            </Tooltip>
                                        )}
                                    </Group>
                                </Group>
                            );
                        })}
                        {pending.map((p) => (
                            <Group key={p.key} gap="sm" wrap="nowrap" justify="space-between">
                                <Group gap="sm" wrap="nowrap" style={{ minWidth: 0 }}>
                                    <span className={classes.icon}><ArrowUpload20Regular /></span>
                                    <div style={{ minWidth: 0 }}>
                                        <Text size="sm" ff="monospace" className={classes.mono}>{zipPathFor(p.file.name, p.folder)}</Text>
                                        <Text size="xs" c="dimmed">{formatBytes(p.file.size)} · {t('Uploaded when you save')}</Text>
                                    </div>
                                </Group>
                                <Tooltip label={t('Remove')}>
                                    <ActionIcon variant="subtle" color="red" aria-label={t('Remove {{name}}', { name: p.file.name })} onClick={() => onChange({ pending: pending.filter((x) => x.key !== p.key) })}>
                                        <Delete20Regular />
                                    </ActionIcon>
                                </Tooltip>
                            </Group>
                        ))}
                    </div>
                </div>
            )}

            <Alert variant="light" color="blue" icon={<Info20Regular />}>
                {t('Files are sent with the settings, whenever this profile is sent. If two profiles for the same device have a file at the same path, the more specific profile wins, as with settings.')}
            </Alert>
        </Stack>
    );
}
