import React, { Suspense, type ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router';
import { LoadingOverlay } from '@mantine/core';
import { allRoutes, isRedirect, type RouteEntry } from '../routes';
import PrivateRoute from '../PrivateRoute';
import { AdminAreaGuard } from './AdminAreaGuard';
import { lazyPage } from '../lazyPage';
import { PageErrorBoundary } from './PageErrorBoundary';

const Error404 = lazyPage(() => import('../pages/Errors/Error404.tsx'));

function routeElement(entry: RouteEntry): ReactNode {
  if (isRedirect(entry)) {
    return <Navigate to={entry.redirectTo} replace />;
  }
  const Page = entry.page;
  if (!entry.adminArea) {
    return <Page />;
  }
  return (
    <AdminAreaGuard area={entry.adminArea}>
      <Page />
    </AdminAreaGuard>
  );
}

/** The routed page area of the signed-in layout. */
export function AppContent() {
  const { pathname } = useLocation();
  return (
    // Keyed by path so a page's error is cleared by moving to another page.
    <PageErrorBoundary key={pathname}>
      <Suspense fallback={<LoadingOverlay visible zIndex={1000} overlayProps={{ blur: 2 }} />}>
        <Routes>
          {/* Everything below needs a session; PrivateRoute sends anyone else to /login. */}
          <Route element={<PrivateRoute />}>
            {allRoutes.map((entry) => (
              <Route key={entry.path} path={entry.path} element={routeElement(entry)} />
            ))}
          </Route>
          <Route path="*" element={<Error404 />} />
        </Routes>
      </Suspense>
    </PageErrorBoundary>
  );
}

export default React.memo(AppContent);
