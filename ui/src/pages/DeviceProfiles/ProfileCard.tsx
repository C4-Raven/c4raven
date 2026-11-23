import React from 'react';
import {
    ActionIcon,
    Badge,
    Group,
    Menu,
    Stack,
    Switch,
    Text,
    Tooltip,
} from '@mantine/core';
import {
    ArrowDownload20Regular,
    Copy20Regular,
    Delete20Regular,
    DocumentMultiple20Regular,
    Edit20Regular,
    Eye20Regular,
    MoreHorizontal20Regular,
    People20Regular,
    PeopleTeam20Regular,
    Person20Regular,
    Phone20Regular,
    PlugConnected20Regular,
    QrCode20Regular,
    Wrench20Regular,
} from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import { Panel } from '@/components/ui';
import { formatRelative } from '../ServerRequests/shared';
import { settingLabel, type Profile } from './types';
import classes from './DeviceProfiles.module.css';
import { isReadOnlyAdmin } from '../../adminAccess';
import { READ_ONLY_LABEL } from '../../components/ReadOnlyTip';

/** Fluent icon shrunk to badge size. */
const small = (icon: React.ReactNode) => <span className={classes.badgeIcon}>{icon}</span>;

const PREVIEW_COUNT = 3;

export default function ProfileCard({ profile, busy, onEdit, onDuplicate, onExport, onDelete, onToggle }: {
    profile: Profile;
    busy?: boolean;
    onEdit: () => void;
    onDuplicate: () => void;
    onExport: () => void;
    onDelete: () => void;
    onToggle: (enabled: boolean) => void;
}) {
    const readOnly = isReadOnlyAdmin();
    const { t } = useTranslation();
    const count = profile.settings.length;
    const names = profile.settings.slice(0, PREVIEW_COUNT).map((s) => settingLabel(s.key));
    const more = count - names.length;
    const fileCount = profile.files?.length ?? 0;
    const kindIcon = {
        group: small(<PeopleTeam20Regular />),
        user: small(<Person20Regular />),
        device: small(<Phone20Regular />),
    } as const;
    const kindLabel = { group: t('Group'), user: t('User'), device: t('Device') } as const;

    return (
        <Panel
            p="lg"
            style={{ opacity: profile.enabled ? 1 : 0.72, display: 'flex', flexDirection: 'column', gap: 12 }}
        >
            <Group justify="space-between" align="flex-start" wrap="nowrap" gap="sm">
                <Stack gap={2} style={{ minWidth: 0 }}>
                    <Group gap={8} wrap="wrap">
                        <Text fw={600} size="lg" style={{ wordBreak: 'break-word' }}>{profile.name}</Text>
                        {!profile.enabled && <Badge variant="light" color="gray" tt="none">{t('Off')}</Badge>}
                    </Group>
                    {profile.description
                        ? <Text size="sm" c="dimmed" lineClamp={2}>{profile.description}</Text>
                        : <Text size="sm" c="dimmed" fs="italic">{t('No description')}</Text>}
                </Stack>
                <Tooltip label={profile.enabled ? t('Being sent to devices. Click to pause.') : t('Paused. Click to start sending.')}>
                    <div>
                        <Switch
                            checked={profile.enabled}
                            disabled={busy || readOnly}
                            onChange={(e) => onToggle(e.currentTarget.checked)}
                            aria-label={t('Enabled')}
                            size="md"
                        />
                    </div>
                </Tooltip>
            </Group>

            <Stack gap={6}>
                <Group gap={6} wrap="wrap">
                    <Text size="xs" c="dimmed" w={44}>{t('Who')}</Text>
                    {profile.target_everyone ? (
                        <Badge variant="light" color="teal" tt="none" leftSection={small(<People20Regular />)}>{t('Everyone')}</Badge>
                    ) : profile.targets.length === 0 ? (
                        <Badge variant="light" color="red" tt="none">{t('Nobody yet')}</Badge>
                    ) : (
                        profile.targets.map((tg) => (
                            <Tooltip key={`${tg.kind}:${tg.value}`} label={`${kindLabel[tg.kind]} · ${tg.value}`}>
                                <Badge
                                    variant="light"
                                    color={tg.kind === 'group' ? 'blue' : tg.kind === 'user' ? 'grape' : 'cyan'}
                                    tt="none"
                                    leftSection={kindIcon[tg.kind]}
                                    maw={220}
                                >
                                    {tg.label || tg.value}
                                </Badge>
                            </Tooltip>
                        ))
                    )}
                </Group>
                <Group gap={6} wrap="wrap">
                    <Text size="xs" c="dimmed" w={44}>{t('When')}</Text>
                    {profile.tool && (
                        <Tooltip label={t('Sent only to devices that ask for this tool\'s profile')}>
                            <Badge variant="light" color="violet" tt="none" leftSection={small(<Wrench20Regular />)} maw={220}>
                                {t('Tool: {{name}}', { name: profile.tool })}
                            </Badge>
                        </Tooltip>
                    )}
                    {!profile.tool && profile.apply_on_enrollment && (
                        <Badge variant="light" color="indigo" tt="none" leftSection={small(<QrCode20Regular />)}>{t('On enrollment')}</Badge>
                    )}
                    {!profile.tool && profile.apply_on_connection && (
                        <Badge variant="light" color="orange" tt="none" leftSection={small(<PlugConnected20Regular />)}>{t('Every connection')}</Badge>
                    )}
                    {!profile.tool && !profile.apply_on_enrollment && !profile.apply_on_connection && (
                        <Badge variant="light" color="red" tt="none">{t('Never')}</Badge>
                    )}
                </Group>
            </Stack>

            <div>
                <Text size="sm" fw={600}>{count === 1 ? t('1 setting') : t('{{count}} settings', { count })}</Text>
                {count > 0 && (
                    <Text size="sm" c="dimmed" lineClamp={2}>
                        {names.join(', ')}{more > 0 ? ` ${t('and {{count}} more', { count: more })}` : ''}
                    </Text>
                )}
                {fileCount > 0 && (
                    <Group gap={6} mt={4} wrap="nowrap">
                        <span className={classes.icon}><DocumentMultiple20Regular /></span>
                        <Text size="sm" c="dimmed" lineClamp={1}>
                            {fileCount === 1 ? t('1 file') : t('{{count}} files', { count: fileCount })}
                            {': '}
                            {profile.files!.map((f) => f.zip_path).join(', ')}
                        </Text>
                    </Group>
                )}
            </div>

            <Group justify="space-between" align="center" mt="auto" gap="xs" wrap="nowrap">
                <Text size="xs" c="dimmed">
                    {profile.updated_at ? t('Updated {{when}}', { when: formatRelative(profile.updated_at) }) : ''}
                </Text>
                <Group gap={6} wrap="nowrap">
                    <Tooltip label={readOnly ? t('View') : t('Edit')}>
                        <ActionIcon variant="subtle" color="gray" size="lg" aria-label={readOnly ? t('View {{name}}', { name: profile.name }) : t('Edit {{name}}', { name: profile.name })} onClick={onEdit}>
                            {readOnly ? <Eye20Regular /> : <Edit20Regular />}
                        </ActionIcon>
                    </Tooltip>
                    <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                            <ActionIcon variant="subtle" color="gray" size="lg" aria-label={t('More actions')}>
                                <MoreHorizontal20Regular />
                            </ActionIcon>
                        </Menu.Target>
                        <Menu.Dropdown>
                            {readOnly && <Menu.Label>{READ_ONLY_LABEL}</Menu.Label>}
                            <Menu.Item leftSection={<Copy20Regular />} disabled={readOnly} onClick={onDuplicate}>{t('Duplicate')}</Menu.Item>
                            <Menu.Item leftSection={<ArrowDownload20Regular />} disabled={readOnly} onClick={onExport}>
                                {t('Export as .pref file')}
                            </Menu.Item>
                            <Menu.Divider />
                            <Menu.Item color="red" leftSection={<Delete20Regular />} disabled={readOnly} onClick={onDelete}>{t('Delete')}</Menu.Item>
                        </Menu.Dropdown>
                    </Menu>
                </Group>
            </Group>
        </Panel>
    );
}
