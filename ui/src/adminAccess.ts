/**
 * Superadmins and read-only admins.
 *
 * A superadmin (role "administrator") sees and changes everything. A read-only
 * admin (role "admin_viewer") sees only the admin areas a superadmin switched on
 * for them, and the pages hide every control that would change something. The
 * server enforces both (raven/admin_view.py); this only decides what to show.
 */
import { t } from 'i18next';
import axios from './axios_config';
import { apiRoutes } from './apiRoutes';

export type AdminArea =
    | 'users'
    | 'groups'
    | 'server_requests'
    | 'plugin_updates'
    | 'device_profiles'
    | 'takgov'
    | 'federation_hub'
    | 'supporting_documents'
    | 'meshtastic';

/** Keep in step with ADMIN_AREAS in the server's raven/admin_view.py. */
export const ADMIN_AREAS: { area: AdminArea; label: string; link?: string }[] = [
    { area: 'users', label: t('Users'), link: '/users' },
    { area: 'groups', label: t('Groups'), link: '/groups' },
    { area: 'server_requests', label: t('Server Requests'), link: '/server-requests' },
    { area: 'plugin_updates', label: t('Plugins'), link: '/plugin_updates' },
    { area: 'device_profiles', label: t('Device Profiles'), link: '/device_profiles' },
    { area: 'takgov', label: t('Link TAK.gov Account'), link: '/link_account' },
    { area: 'federation_hub', label: t('Federation Hub'), link: '/federation_hub' },
    { area: 'supporting_documents', label: t('Supporting Documents'), link: '/supporting_documents' },
    { area: 'meshtastic', label: t('Meshtastic'), link: '/meshtastic' },
];

export const ADMIN_VIEWER_ROLE = 'admin_viewer';

export const isSuperAdmin = (): boolean => localStorage.getItem('administrator') === 'true';

export const isAdminViewer = (): boolean =>
    !isSuperAdmin() && localStorage.getItem('adminViewer') === 'true';

export function viewerAreas(): AdminArea[] {
    try {
        const raw = JSON.parse(localStorage.getItem('adminViewAreas') || '[]');
        return Array.isArray(raw) ? raw : [];
    } catch {
        return [];
    }
}

/** Whether the admin page for `area` should be shown at all. */
export const canSeeArea = (area: AdminArea): boolean =>
    isSuperAdmin() || (isAdminViewer() && viewerAreas().includes(area));

/** The admin area a route belongs to, or null for a page every signed-in user may open. */
export function adminAreaForPath(path: string): AdminArea | null {
    return ADMIN_AREAS.find((a) => a.link === path)?.area ?? null;
}

/** Whether the signed-in user may open the page at `path` (always true for non-admin pages). */
export function canOpenPath(path: string): boolean {
    const area = adminAreaForPath(path);
    return area === null || canSeeArea(area);
}

/** True when the admin pages must hide every control that changes something. */
export const isReadOnlyAdmin = (): boolean => isAdminViewer();

interface MeRole { name: string }
interface Me { roles?: MeRole[]; admin_view_areas?: AdminArea[] }

/** Remember the signed-in user's admin standing from /api/me. */
export function storeAdminAccess(me: Me) {
    const roles = (me.roles || []).map((r) => r.name);
    localStorage.setItem('administrator', String(roles.includes('administrator')));
    localStorage.setItem('adminViewer', String(roles.includes(ADMIN_VIEWER_ROLE)));
    localStorage.setItem('adminViewAreas', JSON.stringify(me.admin_view_areas || []));
}

/** Re-read /api/me so a superadmin's changes reach an open session. */
export async function refreshAdminAccess(): Promise<void> {
    try {
        const r = await axios.get(apiRoutes.authMe);
        if (r.status === 200) {storeAdminAccess(r.data);}
    } catch {
        // Keep what we had; the server enforces access anyway.
    }
}
