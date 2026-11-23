import { useEffect, useState } from 'react';
import {
    Badge,
    Box,
    Button,
    Divider,
    Group,
    LoadingOverlay,
    PinInput,
    Radio,
    Stack,
    Text,
} from '@mantine/core';
import { Checkmark20Regular, Dismiss20Regular } from '@fluentui/react-icons';
import { notifications } from '@mantine/notifications';
import { QRCode } from 'react-qrcode-logo';
import { t } from 'i18next';
import axios from '../axios_config';
import { apiRoutes } from '../apiRoutes';
import { PageHeader, Panel } from '../components/ui';

type Setup = 'authenticator' | 'disable';

/** otpauth URI an authenticator app understands, from the tf-setup response. */
export function otpauthUri(issuer: string, username: string, key: string): string {
    return `otpauth://totp/${issuer}:${username}?secret=${key.replace(/-/g, '')}&issuer=${issuer}`;
}

function initialChoice(method: string | null | undefined): Setup {
    return method === 'authenticator' ? method : 'disable';
}

function notifyFailed(title: string, err: any) {
    notifications.show({
        title,
        message: err?.response?.status === 401 ? t('Not logged in') : '',
        color: 'red',
        icon: <Dismiss20Regular />,
    });
}

function notifyOk(message: string) {
    notifications.show({ message, color: 'green', icon: <Checkmark20Regular /> });
}

/** Lets the signed-in user see their 2FA method and switch to an authenticator app, or none. */
export default function TFASetup() {
    const [loading, setLoading] = useState(true);
    const [currentMethod, setCurrentMethod] = useState<string | null>(null);
    const [choice, setChoice] = useState<Setup>('disable');
    const [pendingSetup, setPendingSetup] = useState<Setup | null>(null);
    const [qrValue, setQrValue] = useState('');
    const [authKey, setAuthKey] = useState('');
    const [code, setCode] = useState('');

    useEffect(() => {
        axios
            .get(apiRoutes.authTotpSetup)
            .then((r) => {
                const method = (r.data?.response ?? r.data)?.tf_primary_method ?? null;
                setCurrentMethod(method);
                setChoice(initialChoice(method));
            })
            .catch((err) => {
                notifications.show({
                    title: 'Failed to get 2FA methods',
                    message: err?.response?.status === 401 ? t('Not logged in') : '',
                    color: 'red',
                    icon: <Dismiss20Regular />,
                });
            })
            .finally(() => setLoading(false));
    }, []);

    const choose = (value: string) => {
        const setup = value as Setup;
        if (setup === choice) {
            return;
        }
        setChoice(setup);
        setQrValue('');
        setAuthKey('');
        setCode('');
        setPendingSetup(null);
        axios
            .post(apiRoutes.authTotpSetup, { setup })
            .then((r) => {
                if (r.status !== 200) {
                    return;
                }
                if (setup === 'authenticator') {
                    const { tf_authr_issuer, tf_authr_username, tf_authr_key } = r.data?.response ?? r.data ?? {};
                    setQrValue(otpauthUri(tf_authr_issuer, tf_authr_username, tf_authr_key ?? ''));
                    setAuthKey(tf_authr_key ?? '');
                    setPendingSetup('authenticator');
                } else {
                    notifyOk('You have successfully disabled two-factor authentication');
                }
            })
            .catch((err) => notifyFailed('Failed to set 2FA method', err));
    };

    const submitCode = () => {
        axios
            .post(apiRoutes.authTotpValidate, { code: code || undefined })
            .then((r) => {
                if (r.status === 200) {
                    notifyOk('You successfully changed your two-factor method');
                }
            })
            .catch(() => {
                notifications.show({
                    message: 'Failed to change your two-factor method',
                    color: 'red',
                    icon: <Dismiss20Regular />,
                });
            });
    };

    return (
        <Box pos="relative">
            <LoadingOverlay visible={loading} />
            <PageHeader
                title="Two-factor authentication"
                description="Two-factor authentication adds an extra layer of security to your account. In addition to your username and password, you'll need to use a code."
            />
            <Panel maw={640}>
                <Group gap="xs">
                    <Text>Current 2FA method enabled for this account:</Text>
                    <Badge variant="light" color="blue">
                        {currentMethod ?? 'None'}
                    </Badge>
                </Group>
                <Divider my="md" />
                <Radio.Group value={choice} onChange={choose}>
                    <Stack gap="md">
                        <Radio
                            value="authenticator"
                            label="Set up using an authenticator app (e.g. google, lastpass, authy)"
                        />
                        <Radio value="disable" label="Disable two factor authentication" />
                    </Stack>
                </Radio.Group>

                {qrValue && (
                    <>
                        <Divider my="md" />
                        <Stack align="center" gap="sm">
                            <Text>
                                Open an authenticator app on your device and scan the following QRcode (or enter the
                                code below manually) to start receiving codes
                            </Text>
                            <Box bg="white" p="sm">
                                <QRCode value={qrValue} size={300} quietZone={10} ecLevel="L" qrStyle="squares" />
                                <Text c="black" ff="monospace" size="sm" ta="center">
                                    {authKey}
                                </Text>
                            </Box>
                        </Stack>
                    </>
                )}

                {pendingSetup && (
                    <>
                        <Divider my="md" />
                        <Stack align="center" gap="md">
                            <PinInput length={6} type="number" oneTimeCode value={code} onChange={setCode} />
                            <Button onClick={submitCode}>Submit Code</Button>
                        </Stack>
                    </>
                )}
            </Panel>
        </Box>
    );
}
