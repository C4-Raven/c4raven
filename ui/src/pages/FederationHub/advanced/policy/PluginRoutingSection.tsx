import React, { useRef } from 'react';
import { ActionIcon, Autocomplete, Button, Group, SimpleGrid, Stack, Switch, TagsInput, Text, Tooltip } from '@mantine/core';
import { Add20Regular, Delete20Regular } from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import { Panel } from '../../../../components/ui';
import { blankPluginRouting } from '../helpers';
import type { PolicyEntity, PolicyPluginRouting, RegisteredPlugin } from '../types';

/**
 * The policy's plugins[] routing (the native console's plugin policy): which
 * federates a plugin's data goes to and comes from. Part of the draft, saved
 * with the whole policy through "Save & apply".
 */
export default function PluginRoutingSection({ plugins, onChange, registered, entities }: {
    plugins: PolicyPluginRouting[];
    onChange: (plugins: PolicyPluginRouting[]) => void;
    registered: RegisteredPlugin[];
    entities: PolicyEntity[];
}) {
    const { t } = useTranslation();
    const nodeNames = Array.from(new Set(entities.map((e) => e.name).filter((n): n is string => !!n)));
    const pluginNames = Array.from(new Set(registered.map((p) => p.name).filter((n): n is string => !!n)));
    const set = (i: number, patch: Partial<PolicyPluginRouting>) => onChange(plugins.map((p, j) => (j === i ? { ...p, ...patch } : p)));
    // Stable row keys: entries have no id of their own and names can repeat or be empty.
    const nextId = useRef(0);
    const ids = useRef<number[]>([]);
    if (ids.current.length > plugins.length) { ids.current = ids.current.slice(0, plugins.length); }
    while (ids.current.length < plugins.length) { ids.current.push(nextId.current++); }
    const remove = (i: number) => {
        ids.current = ids.current.filter((_, j) => j !== i);
        onChange(plugins.filter((_, j) => j !== i));
    };
    const lists: { key: 'sendTo' | 'dontSend' | 'receiveFrom' | 'dontReceive'; label: string }[] = [
        { key: 'sendTo', label: t('Send to') },
        { key: 'dontSend', label: t('Never send to') },
        { key: 'receiveFrom', label: t('Receive from') },
        { key: 'dontReceive', label: t('Never receive from') },
    ];
    return (
        <Stack gap="sm">
            <Group justify="space-between" wrap="wrap">
                <div>
                    <Text fw={600}>{t('Plugin routing')}</Text>
                    <Text size="sm" c="dimmed">{t('Which federates each hub plugin\'s data goes to and comes from. Lists hold policy node names. Saved with the policy by "Save & apply".')}</Text>
                </div>
                <Button size="xs" variant="light" leftSection={<Add20Regular style={{ width: 14, height: 14 }} />} onClick={() => onChange([...plugins, blankPluginRouting(pluginNames.find((n) => !plugins.some((p) => p.pluginName === n)) ?? '')])}>
                    {t('Add plugin routing')}
                </Button>
            </Group>
            {plugins.length === 0 && <Text size="sm" c="dimmed">{t('No plugin routing in this policy.')}</Text>}
            {plugins.map((p, i) => (
                <Panel key={ids.current[i]} p="sm">
                    <Stack gap="xs">
                        <Group justify="space-between" wrap="nowrap" align="flex-end">
                            <Autocomplete
                                label={t('Plugin name')}
                                data={pluginNames}
                                value={p.pluginName ?? ''}
                                onChange={(pluginName) => set(i, { pluginName })}
                                style={{ flex: 1 }}
                                error={!(p.pluginName ?? '').trim() ? t('A plugin name is required') : undefined}
                            />
                            <Tooltip label={t('Remove from draft')}>
                                <ActionIcon color="red" variant="subtle" aria-label={t('Remove plugin routing')} onClick={() => remove(i)}>
                                    <Delete20Regular style={{ width: 16, height: 16 }} />
                                </ActionIcon>
                            </Tooltip>
                        </Group>
                        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
                            {lists.map(({ key, label }) => (
                                <TagsInput
                                    key={key}
                                    label={label}
                                    data={nodeNames}
                                    value={p[key] ?? []}
                                    onChange={(v) => set(i, { [key]: v })}
                                    clearable
                                    comboboxProps={{ withinPortal: true }}
                                />
                            ))}
                        </SimpleGrid>
                        <Group gap="lg" wrap="wrap">
                            <Switch label={t('Send by default')} checked={p.sendByDefault !== false} onChange={(e) => set(i, { sendByDefault: e.currentTarget.checked })} />
                            <Switch label={t('Receive by default')} checked={p.receiveByDefault !== false} onChange={(e) => set(i, { receiveByDefault: e.currentTarget.checked })} />
                            <Switch label={t('Disabled')} checked={!!p.disabled} onChange={(e) => set(i, { disabled: e.currentTarget.checked })} />
                        </Group>
                    </Stack>
                </Panel>
            ))}
        </Stack>
    );
}
