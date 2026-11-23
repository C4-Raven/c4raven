import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useMap } from 'react-leaflet';
import L from 'leaflet';
import { ActionIcon, Button, Checkbox, Divider, Group, Popover, Slider, Stack, Switch, Text, Tooltip } from '@mantine/core';
import { Filter20Regular } from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import { type Category, DEFAULT_SETTINGS, type MapDisplaySettings, MAX_SCALE, MIN_SCALE } from './mapDisplay';

interface Props {
    settings: MapDisplaySettings;
    counts: Record<Category, number>;
    onChange: (settings: MapDisplaySettings) => void;
}

/**
 * The map's "Display" button (top right, with Leaflet's own controls): icon
 * size, labels, and which kinds of icon to show. Rendered into a real Leaflet
 * control so it stacks with the layer switcher, and so clicks and scrolls on
 * it don't pan or zoom the map.
 */
export default function MapDisplayControl({ settings, counts, onChange }: Props) {
    const { t } = useTranslation();
    const map = useMap();
    const [container, setContainer] = useState<HTMLDivElement | null>(null);

    useEffect(() => {
        const control = new L.Control({ position: 'topright' });
        control.onAdd = () => {
            const div = L.DomUtil.create('div', 'leaflet-bar');
            L.DomEvent.disableClickPropagation(div);
            L.DomEvent.disableScrollPropagation(div);
            return div;
        };
        control.addTo(map);
        setContainer(control.getContainer() as HTMLDivElement);
        return () => { control.remove(); };
    }, [map]);

    if (!container) { return null; }

    const groups: { title: string; items: { key: Category; label: string }[] }[] = [
        {
            title: t('Users'),
            items: [
                { key: 'usersOnline', label: t('Online') },
                { key: 'usersOffline', label: t('Offline') },
            ],
        },
        {
            title: t('Markers'),
            items: [
                { key: 'friendly', label: t('Friendly') },
                { key: 'hostile', label: t('Hostile') },
                { key: 'neutral', label: t('Neutral') },
                { key: 'unknown', label: t('Unknown') },
                { key: 'other', label: t('Other markers') },
            ],
        },
        {
            title: t('Other'),
            items: [
                { key: 'casevac', label: t('Casevac') },
                { key: 'rbLines', label: t('Range & bearing lines') },
                { key: 'fovs', label: t('Camera views') },
            ],
        },
    ];

    const setShow = (key: Category, value: boolean) => onChange({ ...settings, show: { ...settings.show, [key]: value } });
    const hidden = Object.values(settings.show).filter((v) => !v).length;

    return createPortal(
        <Popover position="left-start" withArrow shadow="md" width={260} zIndex={1000}>
            <Popover.Target>
                <Tooltip label={t('Map display')} position="left" withArrow>
                    <ActionIcon
                      variant="white"
                      color="dark"
                      size={30}
                      radius={0}
                      aria-label={t('Map display')}
                      style={{ position: 'relative' }}
                    >
                        <Filter20Regular style={{ width: 18, height: 18 }} />
                        {hidden > 0 && (
                            <span
                              style={{ position: 'absolute', top: 3, right: 3, width: 7, height: 7, borderRadius: 7, background: 'var(--mantine-color-orange-6)' }}
                            />
                        )}
                    </ActionIcon>
                </Tooltip>
            </Popover.Target>
            <Popover.Dropdown>
                <Stack gap="sm">
                    <div>
                        <Text size="sm" fw={600} mb={4}>{t('Icon size')}</Text>
                        <Slider
                          min={MIN_SCALE}
                          max={MAX_SCALE}
                          step={0.1}
                          value={settings.iconScale}
                          onChange={(iconScale) => onChange({ ...settings, iconScale })}
                          label={(v) => `${Math.round(v * 100)}%`}
                          marks={[{ value: MIN_SCALE, label: t('Small') }, { value: 1, label: '' }, { value: MAX_SCALE, label: t('Large') }]}
                          mb="md"
                        />
                    </div>
                    <Switch
                      size="sm"
                      label={t('Show names')}
                      checked={settings.labels}
                      onChange={(e) => onChange({ ...settings, labels: e.currentTarget.checked })}
                    />
                    {groups.map((group) => (
                        <div key={group.title}>
                            <Divider label={group.title} labelPosition="left" mb={6} />
                            <Stack gap={6}>
                                {group.items.map((item) => (
                                    <Group key={item.key} justify="space-between" wrap="nowrap" gap="xs">
                                        <Checkbox
                                          size="xs"
                                          label={item.label}
                                          checked={settings.show[item.key]}
                                          onChange={(e) => setShow(item.key, e.currentTarget.checked)}
                                        />
                                        <Text size="xs" c="dimmed">{counts[item.key]}</Text>
                                    </Group>
                                ))}
                            </Stack>
                        </div>
                    ))}
                    <Button size="xs" variant="default" onClick={() => onChange(DEFAULT_SETTINGS)}>{t('Reset')}</Button>
                </Stack>
            </Popover.Dropdown>
        </Popover>,
        container,
    );
}
