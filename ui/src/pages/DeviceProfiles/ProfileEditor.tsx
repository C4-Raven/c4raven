import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
    Alert,
    Button,
    Divider,
    Group,
    Modal,
    MultiSelect,
    Stack,
    Switch,
    Text,
    Textarea,
    TextInput,
    UnstyledButton,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
    ArrowLeft20Regular,
    ArrowRight20Regular,
    Checkmark20Regular,
    Clock20Regular,
    ErrorCircle20Regular,
    Info20Regular,
    PlugConnected20Regular,
    QrCode20Regular,
    Save20Regular,
    Wrench20Regular,
} from '@fluentui/react-icons';
import { useTranslation } from 'react-i18next';
import axios from '../../axios_config';
import { apiRoutes } from '../../apiRoutes';
import { extractError } from '../ServerRequests/shared';
import FilesStep, { filesAfterSave, filesProblem, useFileLimits } from './FilesStep';
import SettingsStep from './SettingsStep';
import { findSetting } from './settingsCatalog';
import {
    ON_CONNECT_KEY,
    TOOL_NAME_RE,
    type PendingFile,
    type Profile,
    type ProfileInput,
    type TargetKind,
} from './types';
import classes from './DeviceProfiles.module.css';
import { isReadOnlyAdmin } from '../../adminAccess';
import { ReadOnlyTip } from '../../components/ReadOnlyTip';

type StepKey = 'basics' | 'settings' | 'files' | 'who' | 'when';
const STEPS: StepKey[] = ['basics', 'settings', 'files', 'who', 'when'];
type Errors = Partial<Record<'name' | 'settings' | 'files' | 'who' | 'when', string>>;

/** The error that belongs to a step (the basics step shows the name error). */
function stepError(errors: Errors, key: StepKey) {
    return key === 'basics' ? errors.name : errors[key];
}

/** Raven UI wizard header: flat numbered tabs, accent bar under the current step. */
function WizardSteps({ steps, active, onSelect, compact }: {
    steps: { key: StepKey; label: string; description: string; error?: string }[];
    active: number;
    onSelect: (index: number) => void;
    compact: boolean;
}) {
    const { t } = useTranslation();
    return (
        <nav className={classes.steps}>
            {steps.map((step, i) => (
                <UnstyledButton
                    key={step.key}
                    className={classes.step}
                    onClick={() => onSelect(i)}
                    aria-label={step.label}
                    aria-current={i === active ? 'step' : undefined}
                    data-done={i < active || undefined}
                    data-error={step.error ? true : undefined}
                >
                    <span className={classes.stepNumber}>
                        {step.error ? <ErrorCircle20Regular /> : i < active ? <Checkmark20Regular /> : i + 1}
                    </span>
                    {!compact && (
                        <div style={{ minWidth: 0 }}>
                            <div className={classes.stepLabel}>{step.label}</div>
                            {step.error
                                ? <Text span size="xs" c="red">{t('Needs attention')}</Text>
                                : <div className={classes.stepDescription}>{step.description}</div>}
                        </div>
                    )}
                </UnstyledButton>
            ))}
        </nav>
    );
}

/** One option row on the "When" step: icon, then a switch (and anything under it). */
function OptionRow({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
    return (
        <Group gap="md" wrap="nowrap" align="flex-start">
            <span className={classes.icon} style={{ marginTop: 2 }}>{icon}</span>
            <Stack gap="sm" style={{ flex: 1 }}>{children}</Stack>
        </Group>
    );
}

interface Option { value: string; label: string }

/** Loads the pickers for "Who gets it" once per editor open. */
function useAudienceOptions(opened: boolean) {
    const [groups, setGroups] = useState<Option[]>([]);
    const [users, setUsers] = useState<Option[]>([]);
    const [devices, setDevices] = useState<Option[]>([]);

    useEffect(() => {
        if (!opened) { return; }
        axios.get(apiRoutes.groupListAll)
            .then((r) => setGroups((r.data ?? []).map((g: any) => ({ value: g.name, label: g.name }))))
            .catch(() => setGroups([]));
        axios.get(apiRoutes.userListAll)
            .then((r) => setUsers((r.data ?? []).map((u: any) => ({ value: u.username, label: u.username }))))
            .catch(() => setUsers([]));
        axios.get(apiRoutes.eudList, { params: { all: true } })
            .then((r) => {
                const rows = Array.isArray(r.data) ? r.data : (r.data?.results ?? []);
                setDevices(rows.map((d: any) => ({
                    value: d.uid,
                    label: d.callsign ? `${d.callsign}${d.username ? ` (${d.username})` : ''}` : d.uid,
                })));
            })
            .catch(() => setDevices([]));
    }, [opened]);

    return { groups, users, devices };
}

/** Make sure already-targeted values still show even if the lookup lists don't contain them. */
function withSelected(options: Option[], selected: string[], labels: Map<string, string>): Option[] {
    const known = new Set(options.map((o) => o.value));
    const extra = selected.filter((v) => !known.has(v)).map((v) => ({ value: v, label: labels.get(v) ?? v }));
    return [...options, ...extra];
}

export function validate(
    draft: ProfileInput,
    t: (k: string) => string,
    { toolMode = false, fileCount = 0, filesError }: { toolMode?: boolean; fileCount?: number; filesError?: string } = {},
): Errors {
    const errors: Errors = {};
    if (!draft.name.trim()) { errors.name = t('Give the profile a name so you can recognise it later'); }
    if (draft.settings.length === 0 && fileCount === 0) { errors.settings = t('Add at least one setting or file'); }
    if (filesError) { errors.files = filesError; }
    if (!draft.target_everyone && draft.targets.length === 0) {
        errors.who = t('Choose who gets this profile, or switch on "Everyone"');
    }
    if (toolMode) {
        if (!draft.tool.trim()) {
            errors.when = t('Enter the tool name this profile is for');
        } else if (!TOOL_NAME_RE.test(draft.tool.trim())) {
            errors.when = t('Tool names can only use letters, numbers, dots, dashes and underscores');
        }
    } else if (!draft.apply_on_enrollment && !draft.apply_on_connection) {
        errors.when = t('Pick at least one moment for devices to receive this profile');
    }
    return errors;
}

function stepOf(errors: Errors): StepKey | null {
    if (errors.name) { return 'basics'; }
    if (errors.settings) { return 'settings'; }
    if (errors.files) { return 'files'; }
    if (errors.who) { return 'who'; }
    if (errors.when) { return 'when'; }
    return null;
}

export default function ProfileEditor({ opened, onClose, profile, initial, onSaved }: {
    opened: boolean;
    onClose: () => void;
    /** Existing profile being edited, or null for a new one. */
    profile: Profile | null;
    /** Starting values (blank, a template, or the profile being edited). */
    initial: ProfileInput;
    onSaved: (profile: Profile) => void;
}) {
    const readOnly = isReadOnlyAdmin();
    const { t } = useTranslation();
    const isPhone = useMediaQuery('(max-width: 48em)');
    const [draft, setDraft] = useState<ProfileInput>(initial);
    const [active, setActive] = useState(0);
    const [errors, setErrors] = useState<Errors>({});
    const [attempted, setAttempted] = useState(false);
    const [saving, setSaving] = useState(false);
    const [serverError, setServerError] = useState<string>();
    const { groups, users, devices } = useAudienceOptions(opened);
    const limits = useFileLimits(opened);
    const [toolMode, setToolMode] = useState(false);
    const [pending, setPending] = useState<PendingFile[]>([]);
    const [removed, setRemoved] = useState<number[]>([]);
    const existingFiles = useMemo(() => profile?.files ?? [], [profile]);
    const lastSaved = useRef<Profile | null>(null);

    useEffect(() => {
        if (opened) {
            setDraft(initial);
            setActive(0);
            setErrors({});
            setAttempted(false);
            setServerError(undefined);
            setToolMode(!!initial.tool);
            setPending([]);
            setRemoved([]);
        }
    }, [opened, initial]);

    const fileCount = filesAfterSave(existingFiles, pending, removed).length;
    const filesError = filesProblem(existingFiles, pending, removed, limits, t);
    const checkAll = () => validate(draft, t, { toolMode, fileCount, filesError });

    // Once the admin has tried to save, keep the inline errors live as they fix things.
    useEffect(() => {
        if (attempted) { setErrors(validate(draft, t, { toolMode, fileCount, filesError })); }
    }, [draft, attempted, t, toolMode, fileCount, filesError]);

    const targetLabels = useMemo(() => {
        const m = new Map<string, string>();
        for (const tg of profile?.targets ?? []) { if (tg.label) { m.set(tg.value, tg.label); } }
        return m;
    }, [profile]);

    function patch(change: Partial<ProfileInput>) {
        setDraft((d) => ({ ...d, ...change }));
    }

    function targetsOf(kind: TargetKind) {
        return draft.targets.filter((tg) => tg.kind === kind).map((tg) => tg.value);
    }

    function setTargets(kind: TargetKind, values: string[]) {
        patch({ targets: [...draft.targets.filter((tg) => tg.kind !== kind), ...values.map((value) => ({ kind, value }))] });
    }

    async function save() {
        setAttempted(true);
        const errs = checkAll();
        setErrors(errs);
        const bad = stepOf(errs);
        if (bad) {
            setActive(STEPS.indexOf(bad));
            return;
        }
        setSaving(true);
        setServerError(undefined);
        const body: ProfileInput = {
            ...draft,
            name: draft.name.trim(),
            description: draft.description.trim(),
            targets: draft.target_everyone ? [] : draft.targets,
            tool: toolMode ? draft.tool.trim() : '',
        };
        try {
            const r = profile
                ? await axios.put(`${apiRoutes.deviceProfileSets}/${profile.id}`, body)
                : await axios.post(apiRoutes.deviceProfileSets, body);
            if (r.data?.success === false) {
                setServerError(r.data?.error ?? t('The server rejected the profile'));
                return;
            }
            let saved: Profile = r.data?.profile;
            const fileError = saved ? await saveFiles(saved) : undefined;
            if (fileError) {
                // The settings are saved; say which file part failed so it can be retried
                notifications.show({
                    color: 'red',
                    icon: <ErrorCircle20Regular />,
                    title: t('Profile saved, but some files were not'),
                    message: fileError,
                    autoClose: false,
                });
            } else {
                notifications.show({
                    color: 'green',
                    icon: <Checkmark20Regular />,
                    title: profile ? t('Profile saved') : t('Profile created'),
                    message: body.name,
                });
            }
            saved = lastSaved.current ?? saved;
            onSaved(saved);
        } catch (err) {
            setServerError(await extractError(err) ?? t('The server rejected the profile'));
        } finally {
            setSaving(false);
        }
    }

    /** Uploads the picked files (one request per folder) and removes the dropped ones. */
    async function saveFiles(saved: Profile): Promise<string | undefined> {
        lastSaved.current = null;
        const byFolder = new Map<string, File[]>();
        for (const p of pending) {
            byFolder.set(p.folder, [...(byFolder.get(p.folder) ?? []), p.file]);
        }
        try {
            for (const id of removed) {
                const r = await axios.delete(`${apiRoutes.deviceProfileSets}/${saved.id}/files/${id}`);
                if (r.data?.profile) { lastSaved.current = r.data.profile; }
            }
            for (const [folder, files] of byFolder) {
                const form = new FormData();
                files.forEach((f) => form.append('file', f));
                if (folder.trim()) { form.append('folder', folder.trim()); }
                const r = await axios.post(`${apiRoutes.deviceProfileSets}/${saved.id}/files`, form, {
                    headers: { 'Content-Type': 'multipart/form-data' },
                });
                if (r.data?.profile) { lastSaved.current = r.data.profile; }
            }
        } catch (err) {
            return await extractError(err) ?? t('A file could not be saved');
        }
        return undefined;
    }

    const isLast = active === STEPS.length - 1;
    const stepNames = [t('Basics'), t('Settings'), t('Files'), t('Who'), t('When')];
    const stepDescriptions = [t('Name it'), t('What changes'), t('What comes along'), t('Which devices'), t('When it is sent')];
    const steps = STEPS.map((key, i) => ({
        key,
        label: stepNames[i],
        description: stepDescriptions[i],
        error: stepError(errors, key),
    }));
    const onConnectLabel = findSetting(ON_CONNECT_KEY)?.label ?? t('Apply server profile updates on connect');

    return (
        <Modal
            opened={opened}
            onClose={onClose}
            size="xl"
            fullScreen={isPhone}
            title={<Text fw={600} size="lg">{readOnly ? t('View profile') : profile ? t('Edit profile') : t('New profile')}</Text>}
            closeOnClickOutside={false}
        >
            <WizardSteps steps={steps} active={active} onSelect={setActive} compact={!!isPhone} />
            {isPhone && (
                <Text size="sm" fw={600} mt={-8} mb="md">
                    {t('Step {{n}} of {{total}}', { n: active + 1, total: STEPS.length })} · {[t('Basics'), t('Settings'), t('Files'), t('Who gets it'), t('When it is sent')][active]}
                </Text>
            )}

            {/* A read-only admin can page through every step, but every field is switched off */}
            <fieldset disabled={readOnly} style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}>
            {active === 0 && (
                <Stack gap="md">
                    <TextInput
                        label={t('Profile name')}
                        description={t('Shown only to admins, e.g. "Field team defaults"')}
                        placeholder={t('e.g. Field team defaults')}
                        value={draft.name}
                        onChange={(e) => patch({ name: e.currentTarget.value })}
                        error={errors.name}
                        required
                        data-autofocus
                    />
                    <Textarea
                        label={t('Description')}
                        description={t('Optional. A note for other admins about what this profile is for.')}
                        autosize
                        minRows={2}
                        maxRows={6}
                        value={draft.description}
                        onChange={(e) => patch({ description: e.currentTarget.value })}
                    />
                    <Divider />
                    <Switch
                        label={t('Enabled')}
                        description={t('Turn off to stop sending this profile without deleting it.')}
                        checked={draft.enabled}
                        onChange={(e) => patch({ enabled: e.currentTarget.checked })}
                    />
                </Stack>
            )}

            {active === 1 && (
                <SettingsStep
                    settings={draft.settings}
                    onChange={(settings) => patch({ settings })}
                    error={errors.settings}
                />
            )}

            {active === 2 && (
                <FilesStep
                    profileId={profile?.id ?? null}
                    existing={existingFiles}
                    pending={pending}
                    removed={removed}
                    limits={limits}
                    error={errors.files ?? (pending.length ? filesError : undefined)}
                    onChange={(change) => {
                        if (change.pending) { setPending(change.pending); }
                        if (change.removed) { setRemoved(change.removed); }
                    }}
                />
            )}

            {active === 3 && (
                <Stack gap="md">
                    <Text size="sm" c="dimmed">
                        {t('Choose which devices receive this profile. You can mix groups, users and individual devices.')}
                    </Text>
                    {errors.who && (
                        <Alert color="red" variant="light" icon={<ErrorCircle20Regular />} py="xs">{errors.who}</Alert>
                    )}
                    <Switch
                        size="md"
                        label={t('Everyone on this server')}
                        description={t('Every user and device gets this profile.')}
                        checked={draft.target_everyone}
                        onChange={(e) => patch({ target_everyone: e.currentTarget.checked })}
                    />
                    {!draft.target_everyone && (
                        <Stack gap="sm">
                            <MultiSelect
                                label={t('Groups')}
                                description={t('Everyone who is a member of these groups')}
                                placeholder={targetsOf('group').length ? undefined : t('Pick groups')}
                                data={withSelected(groups, targetsOf('group'), targetLabels)}
                                value={targetsOf('group')}
                                onChange={(v) => setTargets('group', v)}
                                searchable
                                clearable
                                nothingFoundMessage={t('No matching groups')}
                                comboboxProps={{ withinPortal: true }}
                            />
                            <MultiSelect
                                label={t('Users')}
                                description={t('Any device these users sign in on')}
                                placeholder={targetsOf('user').length ? undefined : t('Pick users')}
                                data={withSelected(users, targetsOf('user'), targetLabels)}
                                value={targetsOf('user')}
                                onChange={(v) => setTargets('user', v)}
                                searchable
                                clearable
                                nothingFoundMessage={t('No matching users')}
                                comboboxProps={{ withinPortal: true }}
                            />
                            <MultiSelect
                                label={t('Devices')}
                                description={t('Specific devices, by callsign. Devices appear here after they have connected once.')}
                                placeholder={targetsOf('device').length ? undefined : t('Pick devices')}
                                data={withSelected(devices, targetsOf('device'), targetLabels)}
                                value={targetsOf('device')}
                                onChange={(v) => setTargets('device', v)}
                                searchable
                                clearable
                                nothingFoundMessage={t('No matching devices')}
                                comboboxProps={{ withinPortal: true }}
                            />
                        </Stack>
                    )}
                    <Alert variant="light" color="blue" icon={<Info20Regular />}>
                        {t('If two profiles change the same setting, the more specific one wins. A device profile beats a user profile, which beats a group profile, which beats an "Everyone" profile.')}
                    </Alert>
                </Stack>
            )}

            {active === 4 && (
                <Stack gap="md">
                    <Text size="sm" c="dimmed">
                        {t('Choose when devices pick up this profile. Most profiles use both.')}
                    </Text>
                    {errors.when && (
                        <Alert color="red" variant="light" icon={<ErrorCircle20Regular />} py="xs">{errors.when}</Alert>
                    )}
                    <Divider />
                    <OptionRow icon={<Wrench20Regular />}>
                        <Switch
                            size="md"
                            label={t('Only when a tool asks for it')}
                            description={t('For a tool or plugin that fetches its own profile from the server (e.g. "mission"). The profile is then sent only to devices that request that tool\'s profile, not on enrollment or connection.')}
                            checked={toolMode}
                            onChange={(e) => setToolMode(e.currentTarget.checked)}
                        />
                        {toolMode && (
                            <TextInput
                                label={t('Tool name')}
                                description={t('Devices fetch it from /Marti/api/device/profile/tool/<name>')}
                                placeholder={t('e.g. mission')}
                                value={draft.tool}
                                onChange={(e) => patch({ tool: e.currentTarget.value })}
                                required
                            />
                        )}
                    </OptionRow>
                    {!toolMode && (<>
                    <Divider />
                    <OptionRow icon={<QrCode20Regular />}>
                        <Switch
                            size="md"
                            label={t('When a device is enrolled')}
                            description={t('Sent once, when someone first signs in to this server in ATAK (including scanning an enrollment QR code). Good for initial setup.')}
                            checked={draft.apply_on_enrollment}
                            onChange={(e) => patch({ apply_on_enrollment: e.currentTarget.checked })}
                        />
                    </OptionRow>
                    <Divider />
                    <OptionRow icon={<PlugConnected20Regular />}>
                        <Switch
                            size="md"
                            label={t('Every time ATAK connects')}
                            description={t('Sent when the app connects, so changes you make here reach devices that are already set up. Each device downloads it once, then again only after you change this profile. Devices enrolled through this server have "{{setting}}" switched on automatically; devices set up by hand need it turned on in ATAK.', { setting: onConnectLabel, interpolation: { escapeValue: false } })}
                            checked={draft.apply_on_connection}
                            onChange={(e) => patch({ apply_on_connection: e.currentTarget.checked })}
                        />
                    </OptionRow>
                    <Alert variant="light" color="gray" icon={<Clock20Regular />}>
                        {t('Settings sent on connect overwrite whatever the user changed on their device each time you save this profile. Use it for things that must stay the same.')}
                    </Alert>
                    </>)}
                </Stack>
            )}
            </fieldset>

            {serverError && (
                <Alert mt="lg" color="red" variant="light" icon={<ErrorCircle20Regular />} title={t("Couldn't save the profile")}>
                    {serverError}
                </Alert>
            )}
            {attempted && stepOf(errors) && (
                <Text mt="md" size="sm" c="red">{t('Some steps need attention before you can save. They are marked in red above.')}</Text>
            )}

            <Group justify="space-between" mt="xl" gap="sm" wrap="wrap">
                <Button
                    variant="default"
                    leftSection={<ArrowLeft20Regular />}
                    onClick={() => (active === 0 ? onClose() : setActive((a) => a - 1))}
                >
                    {active === 0 ? (readOnly ? t('Close') : t('Cancel')) : t('Back')}
                </Button>
                <Group gap="sm" wrap="wrap">
                    {(profile || isLast) && (
                        <ReadOnlyTip>
                            <Button
                                leftSection={<Save20Regular />}
                                loading={saving}
                                disabled={readOnly}
                                onClick={save}
                            >
                                {profile ? t('Save changes') : t('Create profile')}
                            </Button>
                        </ReadOnlyTip>
                    )}
                    {!isLast && (
                        <Button
                            variant={profile ? 'default' : 'filled'}
                            rightSection={<ArrowRight20Regular />}
                            onClick={() => setActive((a) => a + 1)}
                        >
                            {t('Next')}
                        </Button>
                    )}
                </Group>
            </Group>
        </Modal>
    );
}
