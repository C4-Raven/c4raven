import { useEffect, useState } from 'react';
import { AppShell, Burger, Button, Group } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  Checkmark20Regular,
  ShieldKeyhole20Regular,
  SignOut20Regular,
  Warning20Regular,
} from '@fluentui/react-icons';
import { Link, Navigate, useNavigate } from 'react-router';
import { t } from 'i18next';
import axios from './axios_config';
import { apiRoutes } from './apiRoutes';
import { socket } from './socketio';
import AppContent from './components/AppContent';
import Navbar from './components/Navbar/Navbar';
import { clearSession } from './components/ui/session';
import logo from './images/raven-logo.png';
import headerClasses from './components/Header.module.css';

interface AlertEvent {
  alert_type: string;
  callsign: string;
  cancel_time: unknown;
}

function isSignedIn(): boolean {
  return JSON.parse(String(localStorage.getItem('loggedIn'))) === true;
}

function Shell() {
  const navigate = useNavigate();
  const [mobileOpened, { toggle: toggleMobile, close: closeMobile }] = useDisclosure(false);
  const [desktopOpened, { toggle: toggleDesktop }] = useDisclosure(true);
  const [, setConnected] = useState(false);

  useEffect(() => {
    const onConnect = () => setConnected(true);
    const onDisconnect = () => setConnected(false);
    const onAlert = (alert: AlertEvent) => {
      new Audio('/alert.mp3').play().catch(() => {});
      const active = alert.cancel_time === null;
      notifications.show({
        title: t('Alert'),
        message: `${alert.alert_type} from ${alert.callsign}${active ? '' : ' canceled'}`,
        color: active ? 'red' : 'green',
        icon: active ? <Warning20Regular /> : <Checkmark20Regular />,
      });
    };

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('alert', onAlert);
    socket.connect();

    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
      socket.off('alert', onAlert);
    };
  }, []);

  const logout = () => {
    axios.post(apiRoutes.authLogout).then((response) => {
      if (response.status === 200) {
        clearSession();
        navigate('/');
      }
    });
  };

  return (
    <AppShell
      header={{ height: 52 }}
      navbar={{
        width: 264,
        breakpoint: 'sm',
        collapsed: { mobile: !mobileOpened, desktop: !desktopOpened },
      }}
      padding="lg"
    >
      <AppShell.Header withBorder={false}>
        <Group h="100%" justify="space-between" wrap="nowrap" className={headerClasses.bar}>
          <Group gap="sm" wrap="nowrap" className={headerClasses.brand}>
            <Burger
              opened={mobileOpened}
              onClick={toggleMobile}
              hiddenFrom="sm"
              size="sm"
              aria-label={t('Toggle navigation')}
            />
            <Burger
              opened={desktopOpened}
              onClick={toggleDesktop}
              visibleFrom="sm"
              size="sm"
              aria-label={t('Toggle navigation')}
            />
            <span className={headerClasses.brand}>
              <img
                src={logo}
                alt="C4 RAVEN"
                className="raven-logo"
                style={{ height: 34, width: 'auto' }}
              />
            </span>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <Button
              component={Link}
              to="/tfa_setup"
              variant="subtle"
              color="gray"
              title={t('Setup 2FA')}
              leftSection={<ShieldKeyhole20Regular className={headerClasses.actionIcon} />}
            >
              2FA
            </Button>
            <Button
              variant="default"
              leftSection={<SignOut20Regular className={headerClasses.actionIcon} />}
              onClick={logout}
            >
              {t('Log Out')}
            </Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar
        style={{ background: 'var(--raven-chrome)', borderColor: 'var(--raven-border)' }}
      >
        <Navbar onNavigate={closeMobile} />
      </AppShell.Navbar>

      <AppShell.Main>
        <AppContent />
      </AppShell.Main>
    </AppShell>
  );
}

/** The signed-in application shell; anyone not signed in is sent to the login page. */
export function DefaultLayout() {
  if (!isSignedIn()) {
    return <Navigate to="/login" />;
  }
  return <Shell />;
}

export default DefaultLayout;
