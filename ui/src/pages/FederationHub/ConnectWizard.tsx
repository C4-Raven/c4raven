import React, { useEffect, useMemo, useState } from 'react';
import {
    Alert,
    Button,
    Divider,
    FileInput,
    Group,
    List,
    Loader,
    Modal,
    Radio,
    SimpleGrid,
    Stack,
    Stepper,
    Text,
    TextInput,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { ArrowDownload20Regular, ArrowLeft20Regular, ArrowRight20Regular, Certificate20Regular, CheckmarkCircle20Regular, Clock20Regular, Dismiss20Regular, DocumentRibbon20Regular, ErrorCircle20Regular, Info20Regular, PlugConnected20Regular, Warning20Regular } from '@fluentui/react-icons';
import { isReadOnlyAdmin } from '../../adminAccess';
import { ReadOnlyTip } from '../../components/ReadOnlyTip';
import { useTranslation } from 'react-i18next';
import { extractError, formatDateTime, formatRelative } from '../ServerRequests/shared';
import { createPartner, downloadHandoff, downloadOwnCa, getHandoff, inspectCa, type HandoffOptions } from './api';
import CheckPanel from './CheckPanel';
import ConnectStep from './ConnectStep';
import {
    buildCreateForm,
    CA_MAX_BYTES,
    caWarnings,
    describeChanges,
    explainError,
    handoffSteps,
    kindLabel,
    ourAddressOf,
    stepHasError,
    suggestedName,
    validateWizard,
    WIZARD_STEPS,
    wizardStepOf,
    type PartnerErrorView,
    type WizardDraft,
    type WizardErrors,
    type WizardStep,
} from './helpers';
import { CopyAddress, KindIcon, ServerErrorAlert } from './parts';
import ShareStep from './ShareStep';
import {
    defaultPort,
    EVERYTHING,
    type CheckItem,
    type Handoff,
    type Overview,
    type Partner,
    type PartnerKind,
} from './types';

function blankDraft(): WizardDraft {
    return {
        kind: null,
        caFile: null,
        ca: null,
        name: '',
        direction: null,
        host: '',
        port: defaultPort('raven_hub'),
        share: { ...EVERYTHING },
        receive: { ...EVERYTHING },
        interconnected: false,
    };
}

export function useHandoffActions() {
    const { t } = useTranslation();
    const fail = async (title: string, err: unknown) => notifications.show({
        color: 'red', icon: <Dismiss20Regular />, title, message: await extractError(err),
    });
    return {
        ownCa: () => downloadOwnCa().catch((err) => fail(t('Failed to download our certificate'), err)),
        handoff: (opts?: HandoffOptions) => downloadHandoff(opts).catch((err) => fail(t('Failed to download the instructions'), err)),
    };
}

/** `handoff` makes the instructions specific (kind, our direction or an existing partner); none = neutral. */
export function HandoffButtons({ size = 'sm', handoff }: { size?: 'xs' | 'sm'; handoff?: HandoffOptions }) {
    const { t } = useTranslation();
    const actions = useHandoffActions();
    // Read-only admins see these greyed out: the server refuses ca_self and handoff to them.
    const readOnly = isReadOnlyAdmin();
    return (
        <Group gap="sm" wrap="wrap">
            <ReadOnlyTip>
                <Button size={size} variant="light" leftSection={<Certificate20Regular style={{ width: 16, height: 16 }} />} disabled={readOnly} onClick={actions.ownCa}>
                    {t('Download our CA')}
                </Button>
            </ReadOnlyTip>
            <ReadOnlyTip>
                <Button size={size} variant="light" leftSection={<ArrowDownload20Regular style={{ width: 16, height: 16 }} />} disabled={readOnly} onClick={() => actions.handoff(handoff)}>
                    {t('Download instructions for the other admin')}
                </Button>
            </ReadOnlyTip>
        </Group>
    );
}

function TypeStep({ kind, onKind, error }: { kind: PartnerKind | null; onKind: (k: PartnerKind) => void; error?: string }) {
    const { t } = useTranslation();
    // Partners are another Raven, or a TAK Server that runs its own TAK
    // Federation Hub (kind 'other'). Raven links hub to hub in both cases.
    const options: { value: PartnerKind; title: string; badge?: string; body: string; them: string }[] = [
        {
            value: 'raven_hub',
            title: t('Raven'),
            badge: t('Recommended'),
            body: t('Another C4 Raven server with its own Federation Hub.'),
            them: t('Their admin opens Federation Hub on their Raven, clicks Connect a server, and swaps certificates with you. One of you dials the other.'),
        },
        {
            value: 'other',
            title: t('TAK Server'),
            body: t('A TAK Server with its own TAK Federation Hub. Raven links to their hub, which passes data on to their TAK Server.'),
            them: t('Their admin adds your certificate to their Federation Hub, sends you their hub\'s CA, adds you to their hub policy with rules both ways to their TAK Server, and saves and activates it. One side dials the other.'),
        },
    ];
    return (
        <Stack gap="md">
            <Text size="sm" c="dimmed">{t('What are you connecting this Raven to?')}</Text>
            {error && <Alert color="red" variant="light" icon={<ErrorCircle20Regular />} py="xs">{error}</Alert>}
            <Radio.Group value={kind ?? ''} onChange={(v) => onKind(v as PartnerKind)} aria-label={t('Server type')}>
                <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm">
                    {options.map((o) => (
                        <Radio.Card key={o.value} value={o.value} p="md" style={{ height: '100%' }}>
                            <Stack gap="xs">
                                <Group gap="sm" wrap="nowrap" justify="space-between">
                                    <Group gap="sm" wrap="nowrap">
                                        <KindIcon kind={o.value} />
                                        <Text fw={600} size="sm">{o.title}</Text>
                                    </Group>
                                    <Radio.Indicator />
                                </Group>
                                {o.badge && <Text size="xs" c="teal" fw={600}>{o.badge}</Text>}
                                <Text size="sm" c="dimmed">{o.body}</Text>
                                <Divider />
                                <Text size="xs" fw={600}>{t('What the other admin will do')}</Text>
                                <Text size="xs" c="dimmed">{o.them}</Text>
                            </Stack>
                        </Radio.Card>
                    ))}
                </SimpleGrid>
            </Radio.Group>
            <Text size="xs" c="dimmed">{t('The TAK Server must run TAK Federation Hub. Raven does not link to a TAK Server without one.')}</Text>
        </Stack>
    );
}

function CertsStep({ draft, overview, handoff, partners, errors, inspecting, onFile, onName }: {
    draft: WizardDraft;
    overview: Overview | null;
    handoff: Handoff | null;
    partners: Partner[];
    errors: WizardErrors;
    inspecting: boolean;
    onFile: (f: File | null) => void;
    onName: (v: string) => void;
}) {
    const readOnly = isReadOnlyAdmin();
    const { t } = useTranslation();
    const warnings = draft.ca ? caWarnings(draft.ca, partners, t) : [];
    const color = { error: 'red', warning: 'yellow', info: 'blue' } as const;
    const steps = handoffSteps(handoff, draft.kind);
    const address = ourAddressOf(handoff) || ourAddressOf(overview?.hub);
    return (
        <Stack gap="md">
            <Text size="sm" c="dimmed">
                {t('Both servers must trust each other\'s certificate authority (CA) before they can connect. You give them yours and upload theirs.')}
            </Text>
            <Stack gap="xs">
                <Text fw={600} size="sm">{t('1. Give them our certificate')}</Text>
                <Text size="xs" c="dimmed">
                    {t('Download the instructions and send the zip to the other admin. It holds the steps and our CA file.')}
                </Text>
                {address && (
                    <Group gap={6} wrap="wrap">
                        <Text size="xs" c="dimmed">{t('They connect to')}</Text>
                        <CopyAddress value={address} />
                    </Group>
                )}
                <HandoffButtons size="xs" />
                <Alert variant="light" color="yellow" icon={<Warning20Regular />} py="xs">
                    {t('Agree with the other admin which ONE side connects. You pick it in the next step.')}
                </Alert>
                {steps.length > 0 && (
                    <>
                        <Text size="xs" fw={600} mt={4}>
                            {t('What the other admin does ({{kind}})', { kind: kindLabel(draft.kind ?? 'other', t), interpolation: { escapeValue: false } })}
                        </Text>
                        <List type="ordered" size="xs" spacing={4}>
                            {steps.map((step) => <List.Item key={step}>{step}</List.Item>)}
                        </List>
                    </>
                )}
            </Stack>
            <Divider />
            <Stack gap="sm">
                <Text fw={600} size="sm">{t('2. Upload their certificate')}</Text>
                <FileInput
                    label={t('Their CA certificate')}
                    description={draft.kind === 'tak_server'
                        ? t('On a TAK Server this is usually /opt/tak/certs/files/ca.pem, the CA that issued takserver.jks.')
                        : draft.kind === 'other'
                            ? t('The CA that issued their Federation Hub\'s certificate (a .pem file). Their admin gets it from their hub.')
                            : t('The CA file their admin downloaded from their Federation Hub page (a .pem file).')}
                    placeholder={t('Choose a .pem, .crt or .der file')}
                    accept=".pem,.crt,.cer,.der"
                    value={draft.caFile}
                    onChange={onFile}
                    disabled={readOnly}
                    clearable
                    leftSection={<DocumentRibbon20Regular style={{ width: 16, height: 16 }} />}
                    error={errors.ca}
                    required
                />
                {inspecting && (
                    <Group gap={8}><Loader size="xs" /><Text size="sm" c="dimmed">{t('Reading the certificate…')}</Text></Group>
                )}
                {draft.ca && !inspecting && (
                    <Stack gap={6}>
                        <Text size="xs" c="dimmed">{t('Subject')}</Text>
                        <Text size="sm" style={{ wordBreak: 'break-word' }}>{draft.ca.subject}</Text>
                        {draft.ca.not_after && (
                            <Text size="sm">
                                {t('Expires {{date}} ({{relative}})', { date: formatDateTime(draft.ca.not_after), relative: formatRelative(draft.ca.not_after), interpolation: { escapeValue: false } })}
                            </Text>
                        )}
                        {warnings.map((w) => (
                            <Alert
                                key={w.text}
                                color={color[w.level]}
                                variant="light"
                                py="xs"
                                icon={w.level === 'info' ? <Info20Regular /> : <Warning20Regular />}
                            >
                                {w.text}
                            </Alert>
                        ))}
                    </Stack>
                )}
            </Stack>
            <TextInput
                label={t('Partner name')}
                description={t('Shown on this page and on the hub, e.g. "Raven North" or "County TAK"')}
                placeholder={t('e.g. Raven North')}
                value={draft.name}
                onChange={(e) => onName(e.currentTarget.value)}
                error={errors.name}
                required
            />
        </Stack>
    );
}

function ReviewStep({ draft, overview, ourAddress }: { draft: WizardDraft; overview: Overview | null; ourAddress: string }) {
    const { t } = useTranslation();
    const lines = describeChanges(draft, { policy: overview?.hub.active_policy ?? null, ourAddress }, t);
    return (
        <Stack gap="md">
            <Text size="sm" c="dimmed">{t('Here is exactly what will change on this hub when you press Connect.')}</Text>
            <div>
                <Group gap="sm" mb="sm" wrap="nowrap">
                    {draft.kind && <KindIcon kind={draft.kind} />}
                    <div>
                        <Text fw={700}>{draft.name.trim() || t('New partner')}</Text>
                        {draft.kind && <Text size="xs" c="dimmed">{kindLabel(draft.kind, t)}</Text>}
                    </div>
                </Group>
                <List spacing={6} size="sm">
                    {lines.map((l) => <List.Item key={l}>{l}</List.Item>)}
                </List>
            </div>
            <Alert variant="light" color="blue" icon={<Info20Regular />}>
                <Stack gap="xs">
                    <Text size="sm">{t('The other admin still has to do their part. These instructions match what you chose here, including who connects:')}</Text>
                    <HandoffButtons size="xs" handoff={{ kind: draft.kind, direction: draft.direction ?? undefined }} />
                </Stack>
            </Alert>
        </Stack>
    );
}

export default function ConnectWizard({ opened, onClose, overview, onCreated, onOpenPartner }: {
    opened: boolean;
    onClose: () => void;
    overview: Overview | null;
    onCreated: (p: Partner) => void;
    /** "Open existing partner" on a ca_exists / outgoing_exists answer. */
    onOpenPartner?: (id: string) => void;
}) {
    const { t } = useTranslation();
    // Read-only admins can walk through every step; uploading a CA and connecting stay greyed out
    const readOnly = isReadOnlyAdmin();
    const isPhone = useMediaQuery('(max-width: 48em)');
    const [draft, setDraft] = useState<WizardDraft>(blankDraft);
    const [active, setActive] = useState(0);
    const [attempted, setAttempted] = useState(false);
    const [errors, setErrors] = useState<WizardErrors>({});
    const [inspecting, setInspecting] = useState(false);
    const [portTouched, setPortTouched] = useState(false);
    const [saving, setSaving] = useState(false);
    const [serverError, setServerError] = useState<PartnerErrorView>();
    const [handoff, setHandoff] = useState<Handoff | null>(null);
    const [created, setCreated] = useState<Partner | null>(null);
    const [checkState, setCheckState] = useState<{ connected: boolean; finished: boolean }>({ connected: false, finished: false });
    const partners = useMemo(() => overview?.partners ?? [], [overview]);
    const ourAddress = ourAddressOf(handoff) || ourAddressOf(overview?.hub);

    useEffect(() => {
        if (opened) {
            setDraft(blankDraft());
            setActive(0);
            setAttempted(false);
            setErrors({});
            setServerError(undefined);
            setCreated(null);
            setPortTouched(false);
            setCheckState({ connected: false, finished: false });
            // The handoff isn't served to read-only admins; the hub overview gives our address
            if (!readOnly) { getHandoff().then(setHandoff).catch(() => setHandoff(null)); }
        }
    }, [opened, readOnly]);

    useEffect(() => {
        if (attempted) { setErrors(validateWizard(draft, partners, t)); }
    }, [draft, attempted, partners, t]);

    function patch(change: Partial<WizardDraft>) {
        setDraft((d) => ({ ...d, ...change }));
    }

    function chooseKind(kind: PartnerKind) {
        setDraft((d) => ({
            ...d,
            kind,
            port: portTouched ? d.port : defaultPort(kind),
        }));
    }

    async function chooseFile(file: File | null) {
        if (readOnly) { return; }
        patch({ caFile: file, ca: null, caError: undefined });
        if (!file) { return; }
        if (file.size > CA_MAX_BYTES) {
            patch({ caError: t('That file is too big for a certificate (over 1 MiB). Upload just the CA certificate.') });
            return;
        }
        setInspecting(true);
        try {
            const ca = await inspectCa(file);
            setDraft((d) => (d.caFile === file ? { ...d, ca, name: d.name.trim() ? d.name : suggestedName(ca) } : d));
        } catch (err) {
            const msg = (await explainError(err, partners, t, t('This file could not be read as a certificate'))).message;
            setDraft((d) => (d.caFile === file ? { ...d, caError: msg } : d));
        } finally {
            setInspecting(false);
        }
    }

    async function connect() {
        if (readOnly) { return; }
        setAttempted(true);
        const errs = validateWizard(draft, partners, t);
        setErrors(errs);
        const bad = wizardStepOf(errs);
        if (bad) {
            setActive(WIZARD_STEPS.indexOf(bad));
            return;
        }
        setSaving(true);
        setServerError(undefined);
        try {
            const partner = await createPartner(buildCreateForm(draft));
            setCreated(partner);
            onCreated(partner);
            notifications.show({ color: 'green', icon: <CheckmarkCircle20Regular />, title: t('Partner added and activated'), message: partner.name });
        } catch (err) {
            setServerError(await explainError(err, partners, t, t('The hub rejected the change')));
        } finally {
            setSaving(false);
        }
    }

    const step = WIZARD_STEPS[active];
    const isLast = active === WIZARD_STEPS.length - 1;
    const labels: Record<WizardStep, [string, string]> = {
        type: [t('Server type'), t('What it is')],
        certs: [t('Swap certificates'), t('Trust each other')],
        connect: [t('Who connects'), t('One side dials')],
        share: [t('What to share'), t('Each way')],
        review: [t('Review & connect'), t('Apply it')],
    };

    const onCheck = (checks: CheckItem[], finished: boolean) => {
        setCheckState({ connected: checks.some((c) => c.code === 'connected' && c.ok), finished });
    };

    return (
        <Modal
            opened={opened}
            onClose={onClose}
            size="xl"
            fullScreen={isPhone}
            title={<Text fw={700} size="lg">{created ? t('Connecting to {{name}}', { name: created.name, interpolation: { escapeValue: false } }) : t('Connect a server')}</Text>}
            closeOnClickOutside={false}
        >
            {created ? (
                <Stack gap="md">
                    <Text size="sm" c="dimmed">
                        {t('The partner is saved and active on the hub. These checks re-run every 3 seconds for up to two minutes; the hub itself retries every 5 seconds.')}
                    </Text>
                    <CheckPanel partnerId={created.id} poll onResult={onCheck} />
                    {checkState.connected ? (
                        <Alert color="green" variant="light" icon={<CheckmarkCircle20Regular />} title={t('Connected')}>
                            {t('The two servers are linked. Data starts flowing as soon as devices on either side send something.')}
                        </Alert>
                    ) : (
                        <Alert color="yellow" variant="light" icon={<Clock20Regular />} title={t('Waiting for the other side')}>
                            <Stack gap="xs">
                                <Text size="sm">
                                    {t('Your side is ready. If the other admin hasn\'t finished their part yet, send them the instructions. The card on the Federation Hub page turns green when they connect.')}
                                </Text>
                                <HandoffButtons size="xs" handoff={{ partnerId: created.id, kind: created.kind }} />
                            </Stack>
                        </Alert>
                    )}
                    <Group justify="flex-end">
                        <Button onClick={onClose}>{t('Done')}</Button>
                    </Group>
                </Stack>
            ) : (
                <>
                    {readOnly && (
                        <Alert color="gray" variant="light" icon={<Info20Regular />} mb="md">
                            {t('You can look through every step. Only a full administrator can upload a certificate and connect the server.')}
                        </Alert>
                    )}
                    <Stepper
                        active={active}
                        onStepClick={setActive}
                        allowNextStepsSelect
                        size="sm"
                        mb="lg"
                        styles={{ steps: { flexWrap: 'wrap', rowGap: 8 } }}
                    >
                        {WIZARD_STEPS.map((key) => {
                            const bad = stepHasError(key, errors);
                            return (
                                <Stepper.Step
                                    key={key}
                                    label={isPhone ? undefined : labels[key][0]}
                                    aria-label={labels[key][0]}
                                    description={isPhone ? undefined : (bad ? <Text span size="xs" c="red">{t('Needs attention')}</Text> : labels[key][1])}
                                    color={bad ? 'red' : undefined}
                                    icon={bad ? <ErrorCircle20Regular style={{ width: 16, height: 16 }} color="var(--mantine-color-red-5)" /> : undefined}
                                />
                            );
                        })}
                    </Stepper>
                    {isPhone && (
                        <Text size="sm" fw={600} mt={-8} mb="md">
                            {t('Step {{n}} of {{total}}', { n: active + 1, total: WIZARD_STEPS.length, interpolation: { escapeValue: false } })} · {labels[step][0]}
                        </Text>
                    )}

                    {step === 'type' && <TypeStep kind={draft.kind} onKind={chooseKind} error={errors.kind} />}
                    {step === 'certs' && (
                        <CertsStep
                            draft={draft}
                            overview={overview}
                            handoff={handoff}
                            partners={partners}
                            errors={errors}
                            inspecting={inspecting}
                            onFile={chooseFile}
                            onName={(name) => patch({ name })}
                        />
                    )}
                    {step === 'connect' && (
                        <ConnectStep
                            kind={draft.kind ?? 'other'}
                            direction={draft.direction}
                            onDirection={(direction) => patch({ direction })}
                            host={draft.host}
                            port={draft.port}
                            onHost={(host) => patch({ host })}
                            onPort={(port) => { setPortTouched(true); patch({ port }); }}
                            errors={errors}
                            ourAddress={ourAddress}
                            partnerName={draft.name}
                        />
                    )}
                    {step === 'share' && (
                        <ShareStep
                            kind={draft.kind ?? 'other'}
                            share={draft.share}
                            receive={draft.receive}
                            onShare={(share) => patch({ share })}
                            onReceive={(receive) => patch({ receive })}
                            interconnected={draft.interconnected}
                            onInterconnected={(interconnected) => patch({ interconnected })}
                            suggestions={[]}
                            errors={errors}
                        />
                    )}
                    {step === 'review' && <ReviewStep draft={draft} overview={overview} ourAddress={ourAddress} />}

                    {serverError && (
                        <Stack mt="lg">
                            <ServerErrorAlert
                                view={serverError}
                                title={t('Couldn\'t connect the partner')}
                                onOpenPartner={onOpenPartner}
                                onRetry={connect}
                            >
                                {!serverError.halfChanged && (
                                    <Text size="xs">{t('Nothing was left half done: the hub was put back the way it was.')}</Text>
                                )}
                            </ServerErrorAlert>
                        </Stack>
                    )}
                    {attempted && wizardStepOf(errors) && (
                        <Text mt="md" size="sm" c="red">{t('Some steps need attention before you can connect. They are marked in red above.')}</Text>
                    )}

                    <Group justify="space-between" mt="xl" gap="sm" wrap="wrap">
                        <Button
                            variant="default"
                            leftSection={<ArrowLeft20Regular style={{ width: 16, height: 16 }} />}
                            onClick={() => (active === 0 ? onClose() : setActive((a) => a - 1))}
                        >
                            {active === 0 ? t('Cancel') : t('Back')}
                        </Button>
                        {isLast ? (
                            <ReadOnlyTip>
                                <Button leftSection={<PlugConnected20Regular style={{ width: 16, height: 16 }} />} loading={saving} disabled={readOnly} onClick={connect}>
                                    {t('Connect')}
                                </Button>
                            </ReadOnlyTip>
                        ) : (
                            <Button
                                rightSection={<ArrowRight20Regular style={{ width: 16, height: 16 }} />}
                                onClick={() => setActive((a) => a + 1)}
                                disabled={step === 'type' && !draft.kind}
                            >
                                {t('Next')}
                            </Button>
                        )}
                    </Group>
                </>
            )}
        </Modal>
    );
}
