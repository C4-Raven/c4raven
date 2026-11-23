import type { ComponentType } from 'react';
import { lazyPage } from './lazyPage';
import type { AdminArea } from './adminAccess';

/**
 * The pages shown inside the signed-in layout. /login and /404 are routed by App
 * itself, outside the layout, so they are not listed here.
 *
 * Every entry needs a signed-in session. A page with `adminArea` is additionally
 * shown only to someone who may see that admin area (see adminAccess.ts).
 */
export interface PageEntry {
  path: string;
  title: string;
  page: ComponentType;
  adminArea?: AdminArea;
}

/** An old address kept working by sending it to the page's current one. */
export interface RedirectEntry {
  path: string;
  redirectTo: string;
}

export type RouteEntry = PageEntry | RedirectEntry;

export interface RouteSection {
  section: 'operations' | 'account' | 'administration' | 'legacy';
  entries: RouteEntry[];
}

export const isRedirect = (entry: RouteEntry): entry is RedirectEntry => 'redirectTo' in entry;

const page = (
  path: string,
  title: string,
  load: () => Promise<unknown>,
  adminArea?: AdminArea
): PageEntry => ({ path, title, page: lazyPage(load), ...(adminArea ? { adminArea } : {}) });

const Dashboard = () => import('./pages/Dashboard/Dashboard.tsx');

export const routeSections: RouteSection[] = [
  {
    section: 'operations',
    entries: [
      page('/', 'Home', Dashboard),
      page('/dashboard', 'Dashboard', Dashboard),
      page('/map', 'Map', () => import('./pages/Map/MapGate.tsx')),
      page('/euds', 'EUDs', () => import('./pages/EUDs.tsx')),
      page('/casevac', 'CasEvac', () => import('./pages/Casevac.tsx')),
      page('/missions', 'Missions', () => import('./pages/Missions.tsx')),
      page('/missions/:name', 'Mission', () => import('./pages/MissionDetail.tsx')),
      page('/data_packages', 'Data Packages', () => import('./pages/DataPackages.tsx')),
      page('/video_streams', 'Video Streams', () => import('./pages/VideoStreams.tsx')),
      page('/video_recordings', 'Video Recordings', () => import('./pages/VideoRecordings.tsx')),
    ],
  },
  {
    section: 'account',
    entries: [page('/tfa_setup', '2FA Setup', () => import('./pages/TFASetup.tsx'))],
  },
  {
    section: 'administration',
    entries: [
      page('/users', 'Users', () => import('./pages/Users.tsx'), 'users'),
      page('/groups', 'Groups', () => import('./pages/Groups.tsx'), 'groups'),
      page(
        '/server-requests',
        'Server Requests',
        () => import('./pages/ServerRequests/index.tsx'),
        'server_requests'
      ),
      page(
        '/plugin_updates',
        'Plugins',
        () => import('./pages/PluginUpdates.tsx'),
        'plugin_updates'
      ),
      page(
        '/device_profiles',
        'Device Profiles',
        () => import('./pages/DeviceProfiles/index.tsx'),
        'device_profiles'
      ),
      page(
        '/link_account',
        'Link TAK.gov Account',
        () => import('./pages/LinkTakGov.tsx'),
        'takgov'
      ),
      page(
        '/federation_hub',
        'Federation Hub',
        () => import('./pages/FederationHub/index.tsx'),
        'federation_hub'
      ),
      page(
        '/supporting_documents',
        'Supporting Documents',
        () => import('./pages/SupportingDocuments.tsx'),
        'supporting_documents'
      ),
      page('/meshtastic', 'Meshtastic', () => import('./pages/MeshtasticGate.tsx'), 'meshtastic'),
    ],
  },
  {
    section: 'legacy',
    // The scheduler page moved into Server Requests.
    entries: [{ path: '/jobs', redirectTo: '/server-requests' }],
  },
];

/** Every entry, sections flattened. */
export const allRoutes: RouteEntry[] = routeSections.flatMap((s) => s.entries);
