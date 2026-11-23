import { useEffect, useState } from 'react';
import { Button, Group, Modal, Stack, Switch, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { t } from 'i18next';
import axios from '../axios_config';
import { apiRoutes } from '../apiRoutes';
import { ADMIN_AREAS, type AdminArea } from '../adminAccess';

interface AdminAreasModalProps {
    /** The read-only admin being edited; null keeps the modal closed. */
    user: { username: string; admin_view_areas?: string[] } | null;
    onClose: () => void;
    onSaved: () => void;
}

/** A superadmin switches admin pages on or off for one read-only admin. */
export function AdminAreasModal({ user, onClose, onSaved }: AdminAreasModalProps) {
    const [areas, setAreas] = useState<AdminArea[]>([]);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        setAreas((user?.admin_view_areas ?? []) as AdminArea[]);
    }, [user]);

    const toggle = (area: AdminArea, on: boolean) =>
        setAreas((prev) => (on ? [...prev.filter((a) => a !== area), area] : prev.filter((a) => a !== area)));

    function save() {
        if (!user) {return;}
        setSaving(true);
        axios.put(apiRoutes.userAdminView, { username: user.username, areas })
            .then(() => {
                notifications.show({ message: t('Admin pages updated for {{username}}', { username: user.username }), color: 'green' });
                onSaved();
                onClose();
            })
            .catch((err) => notifications.show({
                title: t('Failed to update admin pages'),
                message: err.response?.data?.error ?? '',
                color: 'red',
            }))
            .finally(() => setSaving(false));
    }

    return (
        <Modal opened={user !== null} onClose={onClose} title={t('Admin pages for {{username}}', { username: user?.username ?? '' })}>
            <Stack gap="sm">
                <Text size="sm" c="dimmed">
                    {t('A read-only admin can open the pages switched on here but can never change anything, and never sees IP addresses.')}
                </Text>
                {ADMIN_AREAS.map(({ area, label }) => (
                    <Switch
                        key={area}
                        label={label}
                        checked={areas.includes(area)}
                        onChange={(e) => toggle(area, e.currentTarget.checked)}
                    />
                ))}
                <Group justify="space-between" mt="sm">
                    <Group gap="xs">
                        <Button variant="subtle" size="xs" onClick={() => setAreas(ADMIN_AREAS.map((a) => a.area))}>{t('All on')}</Button>
                        <Button variant="subtle" size="xs" onClick={() => setAreas([])}>{t('All off')}</Button>
                    </Group>
                    <Group gap="xs">
                        <Button variant="default" onClick={onClose}>{t('Cancel')}</Button>
                        <Button onClick={save} loading={saving}>{t('Save')}</Button>
                    </Group>
                </Group>
            </Stack>
        </Modal>
    );
}
