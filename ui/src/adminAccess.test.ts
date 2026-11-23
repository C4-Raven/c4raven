import { afterEach, describe, expect, it } from 'vitest';
import { adminAreaForPath, canOpenPath, isReadOnlyAdmin, storeAdminAccess } from './adminAccess';

afterEach(() => localStorage.clear());

describe('adminAreaForPath', () => {
    it('maps admin routes to their area', () => {
        expect(adminAreaForPath('/users')).toBe('users');
        expect(adminAreaForPath('/server-requests')).toBe('server_requests');
        expect(adminAreaForPath('/link_account')).toBe('takgov');
        expect(adminAreaForPath('/meshtastic')).toBe('meshtastic');
    });

    it('leaves ordinary pages alone', () => {
        expect(adminAreaForPath('/dashboard')).toBeNull();
    });
});

describe('canOpenPath', () => {
    it('lets a full administrator open every admin page', () => {
        storeAdminAccess({ roles: [{ name: 'administrator' }] });
        expect(canOpenPath('/users')).toBe(true);
        expect(canOpenPath('/plugin_updates')).toBe(true);
        expect(isReadOnlyAdmin()).toBe(false);
    });

    it('limits a read-only admin to the areas switched on', () => {
        storeAdminAccess({ roles: [{ name: 'user' }, { name: 'admin_viewer' }], admin_view_areas: ['groups'] });
        expect(isReadOnlyAdmin()).toBe(true);
        expect(canOpenPath('/groups')).toBe(true);
        expect(canOpenPath('/users')).toBe(false);
        expect(canOpenPath('/plugin_updates')).toBe(false);
        expect(canOpenPath('/dashboard')).toBe(true);
    });

    it('keeps plain users out of admin pages', () => {
        storeAdminAccess({ roles: [{ name: 'user' }] });
        expect(canOpenPath('/supporting_documents')).toBe(false);
        expect(canOpenPath('/meshtastic')).toBe(false);
        expect(canOpenPath('/map')).toBe(true);
    });

    it('opens Meshtastic to a read-only admin only with its area on', () => {
        storeAdminAccess({ roles: [{ name: 'admin_viewer' }], admin_view_areas: ['groups'] });
        expect(canOpenPath('/meshtastic')).toBe(false);
        storeAdminAccess({ roles: [{ name: 'admin_viewer' }], admin_view_areas: ['meshtastic'] });
        expect(canOpenPath('/meshtastic')).toBe(true);
    });
});
