import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import '@mantine/charts/styles.css';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import '@mantine/dates/styles.css';
import 'mantine-datatable/styles.css';
import './global.css';
import './i18n';

import { useEffect } from 'react';
import { lazyPage } from './lazyPage';
import { localStorageColorSchemeManager, MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { I18nextProvider, useTranslation } from 'react-i18next';
import { BrowserRouter, Route, Routes } from 'react-router';
import { refreshCsrfToken } from './axios_config';
import { apiRoutes } from './apiRoutes';
import { cssVariablesResolver, theme } from './theme';

const Login = lazyPage(() => import('./pages/Login/Login.tsx'));
const Error404 = lazyPage(() => import('./pages/Errors/Error404.tsx'));
const DefaultLayout = lazyPage(() => import('./DefaultLayout.tsx'));

const colorSchemeManager = localStorageColorSchemeManager({ key: 'raven-color-scheme' });

/**
 * Tokens expire, and the video player passes the stored one as ?jwt=, so pick up a
 * fresh one on every load. Plain fetch: a 401 here must not bounce to the login page.
 */
async function refreshApiToken() {
  try {
    if (!localStorage.getItem('token')) {
      return;
    }
    const response = await fetch(apiRoutes.authMe, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) {
      return;
    }
    const body = await response.json();
    if (typeof body?.token === 'string' && body.token) {
      localStorage.setItem('token', body.token);
    }
  } catch {
    // Not fatal: the old token stays until it expires.
  }
}

export default function App() {
  const { i18n } = useTranslation();

  useEffect(() => {
    // eslint-disable-next-line no-console
    refreshCsrfToken().catch((error) => console.error(error));
    refreshApiToken();
  }, []);

  return (
    <I18nextProvider i18n={i18n}>
      <MantineProvider
        theme={theme}
        cssVariablesResolver={cssVariablesResolver}
        colorSchemeManager={colorSchemeManager}
        defaultColorScheme="dark"
      >
        <Notifications />
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/404" element={<Error404 />} />
            <Route path="*" element={<DefaultLayout />} />
          </Routes>
        </BrowserRouter>
      </MantineProvider>
    </I18nextProvider>
  );
}
