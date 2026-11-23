import React, { useEffect, useRef, useState } from 'react';
import {
    ActionIcon,
    Box,
    Button,
    Center,
    Checkbox,
    Image,
    PasswordInput,
    PinInput,
    Stack,
    Text,
    TextInput,
    Title,
    Tooltip,
    useComputedColorScheme,
    useMantineColorScheme,
} from '@mantine/core';
import {
    Checkmark20Regular,
    Dismiss20Regular,
    LockClosed20Regular,
    Person20Regular,
    WeatherMoon20Regular,
    WeatherSunny20Regular,
} from '@fluentui/react-icons';
import { notifications } from '@mantine/notifications';
import { useNavigate } from 'react-router';
import { QRCode } from 'react-qrcode-logo';
import plainAxios from 'axios';
import axios from '../../axios_config';
import { apiRoutes } from '../../apiRoutes';
import { Panel } from '../../components/ui';
import ravenLogo from '../../images/raven-logo.png';
import classes from './Login.module.css';

type Mode = 'login' | 'authenticator';

const TURNSTILE_SCRIPT_ID = 'cf-turnstile-script';
const TURNSTILE_SCRIPT_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js';

interface TurnstileApi {
    render: (container: HTMLElement, options: Record<string, unknown>) => string | undefined;
    reset: (widgetId?: string) => void;
    remove: (widgetId?: string) => void;
}

declare global {
    interface Window {
        turnstile?: TurnstileApi;
    }
}

const HEADINGS: Record<Mode, string | null> = {
    login: null,
    authenticator: 'Two-factor authentication',
};

function firstError(err: any): string {
    return err?.response?.data?.response?.errors?.[0] ?? '';
}

function fail(title: string, message?: string) {
    notifications.show({ title, message, color: 'red', icon: <Dismiss20Regular /> });
}

function succeed(message: string) {
    notifications.show({ message, color: 'green', icon: <Checkmark20Regular /> });
}

/** Unwraps the Flask-Security `{ response: {...} }` envelope when present. */
function body(data: any): any {
    return data?.response ?? data ?? {};
}

function roleNames(roles: unknown): string[] {
    if (!Array.isArray(roles)) {
        return [];
    }
    return roles.map((r) => (typeof r === 'string' ? r : r?.name)).filter(Boolean);
}

/** Sign-in page with authenticator-app two-factor and an optional Turnstile challenge. */
export default function Login() {
    const navigate = useNavigate();
    const { setColorScheme } = useMantineColorScheme();
    const scheme = useComputedColorScheme('dark');

    const [mode, setMode] = useState<Mode>('login');
    const [ldapEnabled, setLdapEnabled] = useState(false);
    const [csrfToken, setCsrfToken] = useState('');

    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [rememberMe, setRememberMe] = useState(false);
    const [code, setCode] = useState('');
    const [enrolQr, setEnrolQr] = useState('');
    const [enrolKey, setEnrolKey] = useState('');
    const [submitting, setSubmitting] = useState(false);

    const [turnstile, setTurnstile] = useState({ enabled: false, siteKey: '' });
    const [turnstileToken, setTurnstileToken] = useState('');
    const turnstileBox = useRef<HTMLDivElement>(null);
    const turnstileWidget = useRef<string | undefined>(undefined);

    useEffect(() => {
        axios
            .get(apiRoutes.authTurnstile)
            .then((r) => setTurnstile({ enabled: !!r.data?.enabled, siteKey: r.data?.site_key ?? '' }))
            .catch((err) => console.error('Turnstile config unavailable', err));

        axios
            .get(apiRoutes.authLogin, { headers: { 'Content-Type': 'application/json' } })
            .then((r) => {
                const data = body(r.data);
                const attributes: string[] = Array.isArray(data.identity_attributes) ? data.identity_attributes : [];
                setLdapEnabled(attributes.includes('ldap'));
                const token = data.csrf_token ?? '';
                setCsrfToken(token);
                if (token !== '') {
                    axios.defaults.headers.common['X-XSRF-Token'] = token;
                    plainAxios.defaults.headers.common['X-XSRF-Token'] = token;
                }
            })
            .catch((err) => console.error('Failed to read login settings', err));
    }, []);

    // Load the Turnstile script once, only when the server has the challenge switched on.
    useEffect(() => {
        if (!turnstile.enabled || document.getElementById(TURNSTILE_SCRIPT_ID)) {
            return;
        }
        const script = document.createElement('script');
        script.id = TURNSTILE_SCRIPT_ID;
        script.src = TURNSTILE_SCRIPT_URL;
        script.async = true;
        script.defer = true;
        document.head.appendChild(script);
    }, [turnstile.enabled]);

    // (Re-)render the widget in login mode; a new scheme or mode means a fresh widget and no token.
    useEffect(() => {
        if (!turnstile.enabled || mode !== 'login') {
            return undefined;
        }
        setTurnstileToken('');
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const mount = () => {
            if (cancelled) {
                return;
            }
            const box = turnstileBox.current;
            if (!window.turnstile || !box) {
                timer = setTimeout(mount, 200);
                return;
            }
            turnstileWidget.current = window.turnstile.render(box, {
                sitekey: turnstile.siteKey,
                theme: scheme,
                callback: (token: string) => setTurnstileToken(token),
                'expired-callback': () => setTurnstileToken(''),
                'error-callback': () => setTurnstileToken(''),
            });
        };
        mount();
        return () => {
            cancelled = true;
            if (timer) {
                clearTimeout(timer);
            }
            if (turnstileWidget.current !== undefined) {
                try {
                    window.turnstile?.remove(turnstileWidget.current);
                } catch {
                    // Widget already gone.
                }
                turnstileWidget.current = undefined;
            }
        };
    }, [turnstile.enabled, turnstile.siteKey, mode, scheme]);

    const resetTurnstile = () => {
        setTurnstileToken('');
        if (turnstileWidget.current !== undefined) {
            window.turnstile?.reset(turnstileWidget.current);
        }
    };

    const loadUser = () =>
        axios
            .get(apiRoutes.authMe)
            .then((r) => {
                if (r.status === 200) {
                    const me = r.data ?? {};
                    const roles = roleNames(me.roles);
                    localStorage.setItem('token', String(me.token));
                    localStorage.setItem('administrator', roles.includes('administrator') ? 'true' : 'false');
                    localStorage.setItem('adminViewer', roles.includes('admin_viewer') ? 'true' : 'false');
                    localStorage.setItem('adminViewAreas', JSON.stringify(me.admin_view_areas ?? []));
                }
                navigate('/dashboard');
            })
            .catch((err) => console.error('Failed to load the signed-in user', err));

    const startEnrolment = () =>
        axios
            .post(apiRoutes.authTotpSetup, { setup: 'authenticator' })
            .then((r) => {
                const data = body(r.data);
                const issuer = data.tf_authr_issuer;
                const key: string = data.tf_authr_key ?? '';
                setEnrolQr(
                    `otpauth://totp/${issuer}:${data.tf_authr_username}?secret=${key.replace(/-/g, '')}&issuer=${issuer}`,
                );
                setEnrolKey(key);
                setMode('authenticator');
            })
            .catch((err) => fail('Failed to start two-factor setup', firstError(err)));

    const login = () => {
        if (turnstile.enabled && !turnstileToken) {
            fail('Verification required', 'Please complete the human verification challenge');
            return Promise.resolve();
        }
        const payload = {
            username,
            password,
            submit: 'Login',
            csrf_token: csrfToken,
            turnstile_token: turnstileToken || '',
        };
        return axios
            .post(ldapEnabled ? apiRoutes.authLdapLogin : apiRoutes.authLogin, JSON.stringify(payload))
            .then((r) => {
                if (r.status !== 200) {
                    return undefined;
                }
                localStorage.setItem('loggedIn', 'true');
                localStorage.setItem('username', username);
                const data = body(r.data);
                if (data.tf_required) {
                    if (data.tf_method === 'authenticator') {
                        setMode('authenticator');
                        return undefined;
                    }
                    return startEnrolment();
                }
                if (data.force_password_change) {
                    window.location.href = apiRoutes.authChangePassword;
                    return undefined;
                }
                return loadUser();
            })
            .catch((err) => {
                fail('Login Failed', firstError(err));
                if (turnstile.enabled) {
                    resetTurnstile();
                }
            });
    };

    const validateCode = () =>
        axios
            .post(apiRoutes.authTotpValidate, { code })
            .then((r) => {
                if (r.status !== 200) {
                    return undefined;
                }
                succeed('Authentication Succeeded');
                if (body(r.data).force_password_change) {
                    window.location.href = apiRoutes.authChangePassword;
                    return undefined;
                }
                return loadUser();
            })
            .catch((err) => fail('Authentication Failed', firstError(err)));

    const submit = (event: React.FormEvent) => {
        event.preventDefault();
        if (submitting) {
            return;
        }
        const action = mode === 'login' ? login : validateCode;
        setSubmitting(true);
        Promise.resolve(action()).finally(() => setSubmitting(false));
    };

    const isTwoFactor = mode === 'authenticator';
    const heading = HEADINGS[mode];

    return (
        <div className={classes.page}>
            <Tooltip label={scheme === 'dark' ? 'Light mode' : 'Dark mode'}>
                <ActionIcon
                    className={classes.themeToggle}
                    variant="subtle"
                    color="gray"
                    size="lg"
                    aria-label="Toggle colour scheme"
                    onClick={() => setColorScheme(scheme === 'dark' ? 'light' : 'dark')}
                >
                    {scheme === 'dark' ? <WeatherSunny20Regular /> : <WeatherMoon20Regular />}
                </ActionIcon>
            </Tooltip>

            <Panel className={classes.panel}>
                <Center mb="lg">
                    <Image src={ravenLogo} alt="C4 RAVEN" className="raven-logo" h={56} w="auto" fit="contain" />
                </Center>

                {heading && (
                    <Title order={1} ta="center" className={classes.heading}>
                        {heading}
                    </Title>
                )}

                <form onSubmit={submit}>
                    <Stack mt="lg" gap="md">
                        {mode === 'login' && (
                            <>
                                <TextInput
                                    required
                                    label="Username"
                                    autoComplete="username"
                                    leftSection={<Person20Regular />}
                                    value={username}
                                    onChange={(e) => setUsername(e.currentTarget.value)}
                                />
                                <PasswordInput
                                    required
                                    label="Password"
                                    autoComplete="current-password"
                                    leftSection={<LockClosed20Regular />}
                                    value={password}
                                    onChange={(e) => setPassword(e.currentTarget.value)}
                                />
                            </>
                        )}

                        {mode === 'authenticator' && !enrolQr && (
                            <Text ta="center">Please check your authenticator app for an auth code</Text>
                        )}

                        {enrolQr && isTwoFactor && (
                            <Stack align="center" gap="sm">
                                <Text size="sm">
                                    Two-factor authentication is required for this account. Scan this QR code with an
                                    authenticator app (or enter the key below manually), then enter the 6-digit code it
                                    shows.
                                </Text>
                                <Box bg="white" p="sm">
                                    <QRCode value={enrolQr} size={260} quietZone={10} ecLevel="L" qrStyle="squares" />
                                    <Text c="black" ff="monospace" size="sm" ta="center">
                                        {enrolKey}
                                    </Text>
                                </Box>
                            </Stack>
                        )}

                        {isTwoFactor && (
                            <>
                                <Center>
                                    <PinInput
                                        length={6}
                                        type="number"
                                        oneTimeCode
                                        autoFocus
                                        value={code}
                                        onChange={setCode}
                                    />
                                </Center>
                                <Button type="submit" fullWidth loading={submitting}>
                                    Submit
                                </Button>
                            </>
                        )}

                        {mode === 'login' && (
                            <Checkbox
                                label="Remember me"
                                checked={rememberMe}
                                onChange={(e) => setRememberMe(e.currentTarget.checked)}
                            />
                        )}

                        {mode === 'login' && (
                            <Button type="submit" fullWidth mt="xs" loading={submitting}>
                                Login
                            </Button>
                        )}

                        {mode === 'login' && turnstile.enabled && (
                            <Center>
                                <div ref={turnstileBox} />
                            </Center>
                        )}
                    </Stack>
                </form>

                {mode === 'login' && (
                    <Text size="xs" c="dimmed" ta="center" mt="lg">
                        Forgot your password? Contact your administrator.
                    </Text>
                )}
            </Panel>
        </div>
    );
}
