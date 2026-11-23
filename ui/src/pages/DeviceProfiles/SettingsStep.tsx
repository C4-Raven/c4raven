import React, { useMemo, useState } from 'react';
import {
    Alert,
    Badge,
    Button,
    Chip,
    Collapse,
    Divider,
    Group,
    Select,
    SimpleGrid,
    Stack,
    Text,
    TextInput,
    UnstyledButton,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
    Add20Regular,
    ArrowImport20Regular,
    ChevronDown20Regular,
    ChevronRight20Regular,
    Code20Regular,
    ErrorCircle20Regular,
    Options20Regular,
    Search20Regular,
} from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import { CATEGORIES, findSetting, SETTINGS, type SettingDef } from './settingsCatalog';
import ImportPrefsButton from './ImportPrefsButton';
import { isReadOnlyAdmin } from '../../adminAccess';
import SettingRow, { SettingValueInput } from './SettingRow';
import { mergeSettings, settingFromDef, VALUE_CLASS_LABELS, VALUE_CLASSES, type ProfileSetting } from './types';
import classes from './DeviceProfiles.module.css';

const PAGE = 12;

function matches(def: SettingDef, query: string) {
    if (!query) { return true; }
    const q = query.toLowerCase();
    return def.label.toLowerCase().includes(q)
        || def.description.toLowerCase().includes(q)
        || def.key.toLowerCase().includes(q);
}

export default function SettingsStep({ settings, onChange, error }: {
    settings: ProfileSetting[];
    onChange: (settings: ProfileSetting[]) => void;
    error?: string;
}) {
    const { t } = useTranslation();
    // Read-only admins see every control, switched off (the editor's fieldset disables the rest)
    const readOnly = isReadOnlyAdmin();
    const [query, setQuery] = useState('');
    const [category, setCategory] = useState<string>('all');
    const [shown, setShown] = useState(PAGE);
    const [customOpen, setCustomOpen] = useState(false);
    const [customKey, setCustomKey] = useState('');
    const [customClass, setCustomClass] = useState<string>('class java.lang.String');
    const [customValue, setCustomValue] = useState('');
    const [customError, setCustomError] = useState<string>();

    const inProfile = useMemo(() => new Set(settings.map((s) => s.key)), [settings]);

    const counts = useMemo(() => {
        const c: Record<string, number> = {};
        for (const def of SETTINGS) { c[def.category] = (c[def.category] ?? 0) + 1; }
        return c;
    }, []);

    const available = useMemo(
        () => SETTINGS.filter((def) => !inProfile.has(def.key)
            && (category === 'all' || def.category === category)
            && matches(def, query.trim())),
        [inProfile, category, query],
    );

    const categoryLabel = (id: string) => CATEGORIES.find((c) => c.id === id)?.label ?? id;
    const categoryDescription = CATEGORIES.find((c) => c.id === category)?.description;

    function add(def: SettingDef) {
        onChange([...settings, settingFromDef(def)]);
    }

    function update(index: number, next: ProfileSetting) {
        onChange(settings.map((s, i) => (i === index ? next : s)));
    }

    function remove(index: number) {
        onChange(settings.filter((_, i) => i !== index));
    }

    function addCustom() {
        const key = customKey.trim();
        if (!key) { setCustomError(t('Enter the exact ATAK preference key')); return; }
        if (/\s/.test(key)) { setCustomError(t('Preference keys cannot contain spaces')); return; }
        if (inProfile.has(key)) { setCustomError(t('This profile already has that setting')); return; }
        const def = findSetting(key);
        onChange([...settings, def
            ? settingFromDef(def, customValue || undefined)
            : { key, value: customValue, value_class: customClass }]);
        setCustomKey('');
        setCustomValue('');
        setCustomError(undefined);
        if (def) {
            notifications.show({
                color: 'blue',
                title: t('Known setting added'),
                message: t('"{{key}}" is the "{{label}}" setting, so it was added with its friendly control.', { key, label: def.label, interpolation: { escapeValue: false } }),
            });
        }
    }

    function onImported(incoming: { key: string; value: string; value_class: string }[]) {
        const result = mergeSettings(settings, incoming);
        onChange(result.settings);
        notifications.show({
            color: result.added + result.replaced ? 'green' : 'yellow',
            title: t('Settings imported'),
            message: result.added + result.replaced
                ? t('{{added}} added, {{replaced}} updated (settings with the same key were overwritten).', result)
                : t('The file had no settings to import.'),
        });
    }

    return (
        <Stack gap="lg">
            {/* What's in the profile */}
            <Stack gap="xs">
                <Group justify="space-between" align="flex-end" wrap="wrap" gap="xs">
                    <div>
                        <Text fw={600}>{t('Settings in this profile')}</Text>
                        <Text size="sm" c="dimmed">
                            {t('These values are written into ATAK on each device that receives the profile.')}
                        </Text>
                    </div>
                    <ImportPrefsButton onImported={onImported} disabled={readOnly} />
                </Group>
                {error && (
                    <Alert color="red" variant="light" icon={<ErrorCircle20Regular />} py="xs">{error}</Alert>
                )}
                {settings.length === 0 ? (
                    <Group gap="sm" wrap="nowrap" align="flex-start" py="xs">
                        <span className={classes.icon}><Options20Regular /></span>
                        <Text size="sm" c="dimmed">
                            {t('No settings yet. Pick some from the list below, or import a preference file exported from a device that is already set up the way you want.')}
                        </Text>
                    </Group>
                ) : (
                    <div className={classes.rows}>
                        {settings.map((s, i) => (
                            <SettingRow
                                key={s.key}
                                setting={s}
                                onChange={(next) => update(i, next)}
                                onRemove={() => remove(i)}
                            />
                        ))}
                    </div>
                )}
            </Stack>

            <Divider label={t('Add settings')} labelPosition="left" />

            {/* Catalog browser */}
            <Stack gap="sm">
                <TextInput
                    leftSection={<Search20Regular />}
                    placeholder={t('Search settings, e.g. "team", "units", "callsign"')}
                    aria-label={t('Search settings')}
                    value={query}
                    onChange={(e) => { setQuery(e.currentTarget.value); setShown(PAGE); }}
                />
                <Chip.Group
                    multiple={false}
                    value={category}
                    onChange={(v) => { setCategory(v as string); setShown(PAGE); }}
                >
                    <Group gap={6} wrap="wrap">
                        <Chip value="all" size="xs" variant="light">{t('All')} ({SETTINGS.length})</Chip>
                        {CATEGORIES.filter((c) => counts[c.id]).map((c) => (
                            <Chip key={c.id} value={c.id} size="xs" variant="light">
                                {c.label} ({counts[c.id]})
                            </Chip>
                        ))}
                    </Group>
                </Chip.Group>
                {categoryDescription && <Text size="sm" c="dimmed">{categoryDescription}</Text>}

                {available.length === 0 ? (
                    <Text size="sm" c="dimmed" ta="center" py="md">
                        {query
                            ? t('No settings match "{{query}}". Try another word, or add it as a custom setting below.', { query, interpolation: { escapeValue: false } })
                            : t('Every setting in this category is already in the profile.')}
                    </Text>
                ) : (
                    <Stack gap={6}>
                        <div className={classes.rows}>
                            {available.slice(0, shown).map((def) => (
                                <Group key={def.key} justify="space-between" wrap="nowrap" gap="sm" align="center">
                                    <Stack gap={2} style={{ minWidth: 0, flex: 1 }}>
                                        <Group gap={6} wrap="wrap">
                                            <Text size="sm" fw={600}>{def.label}</Text>
                                            {category === 'all' && (
                                                <Badge size="xs" variant="light" color="gray" tt="none">{categoryLabel(def.category)}</Badge>
                                            )}
                                        </Group>
                                        <Text size="xs" c="dimmed">{def.description}</Text>
                                        <Text size="xs" c="dimmed" ff="monospace" className={classes.mono}>{def.key}</Text>
                                    </Stack>
                                    <Button
                                        size="xs"
                                        variant="default"
                                        leftSection={<Add20Regular />}
                                        onClick={() => add(def)}
                                        aria-label={t('Add {{name}}', { name: def.label })}
                                        style={{ flexShrink: 0 }}
                                    >
                                        {t('Add')}
                                    </Button>
                                </Group>
                            ))}
                        </div>
                        {available.length > shown && (
                            <Button variant="subtle" size="xs" onClick={() => setShown((n) => n + PAGE * 2)}>
                                {t('Show more ({{count}} left)', { count: available.length - shown })}
                            </Button>
                        )}
                    </Stack>
                )}
            </Stack>

            {/* Custom key fallback */}
            <div>
                <Divider mb="sm" />
                <UnstyledButton onClick={() => setCustomOpen((o) => !o)} w="100%" aria-expanded={customOpen}>
                    <Group gap="xs" wrap="nowrap">
                        <span className={classes.icon}>{customOpen ? <ChevronDown20Regular /> : <ChevronRight20Regular />}</span>
                        <span className={classes.icon}><Code20Regular /></span>
                        <div>
                            <Text size="sm" fw={600}>{t('Custom setting (advanced)')}</Text>
                            <Text size="xs" c="dimmed">
                                {t("For an ATAK preference key that isn't in the list. You need the exact key and its type.")}
                            </Text>
                        </div>
                    </Group>
                </UnstyledButton>
                <Collapse expanded={customOpen}>
                    <Stack gap="sm" mt="sm">
                        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
                            <TextInput
                                label={t('Preference key')}
                                placeholder="e.g. locationCallsign"
                                value={customKey}
                                onChange={(e) => { setCustomKey(e.currentTarget.value); setCustomError(undefined); }}
                                error={customError}
                                styles={{ input: { fontFamily: 'var(--mantine-font-family-monospace)' } }}
                            />
                            <Select
                                label={t('Type')}
                                data={VALUE_CLASSES.map((v) => ({ value: v, label: t(VALUE_CLASS_LABELS[v]) }))}
                                value={customClass}
                                onChange={(v) => {
                                    if (!v) { return; }
                                    setCustomClass(v);
                                    setCustomValue(v === 'class java.lang.Boolean' ? 'false' : '');
                                }}
                                allowDeselect={false}
                                comboboxProps={{ withinPortal: true }}
                            />
                        </SimpleGrid>
                        {['class java.lang.Integer', 'class java.lang.Long', 'class java.lang.Float'].includes(customClass) && (
                            <Alert color="yellow" variant="light" icon={<ErrorCircle20Regular />} py="xs">
                                {t('Most ATAK number settings are stored as Text, even when they hold a number. Only pick a number type if you know the key needs it, otherwise ATAK can fail to read it.')}
                            </Alert>
                        )}
                        <SettingValueInput
                            label={t('Value')}
                            valueClass={customClass}
                            value={customValue}
                            onChange={setCustomValue}
                        />
                        <Group justify="flex-end">
                            <Button variant="default" leftSection={<ArrowImport20Regular />} onClick={addCustom}>
                                {t('Add custom setting')}
                            </Button>
                        </Group>
                    </Stack>
                </Collapse>
            </div>
        </Stack>
    );
}
