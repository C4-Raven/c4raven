import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';
import i18n from 'i18next';
import { render, waitFor } from '@test-utils';

let features: Record<string, boolean> = { map: true, meshtastic: false };
let me: { roles: { name: string }[]; admin_view_areas?: AdminArea[] } = { roles: [{ name: 'administrator' }] };

vi.mock('../../axios_config', () => ({
    default: {
        get: vi.fn((url: string) => {
            if (url === '/api/features') {return Promise.resolve({ status: 200, data: features });}
            if (url === '/api/me') {return Promise.resolve({ status: 200, data: me });}
            return Promise.reject(new Error(`unmocked ${url}`));
        }),
    },
}));

import Navbar from './Navbar';
import { resetFeatures } from '../../features';
import { storeAdminAccess, type AdminArea } from '../../adminAccess';

i18n.init({ lng: 'en', resources: {}, interpolation: { escapeValue: false } });

// The labels are translated when the module loads, so links are found by where they go
const link = (path: string) => document.querySelector(`a[href="${path}"]`);

/** Waits until the server's features have been fetched and the admin links drawn. */
async function settled(adminPath: string) {
    await waitFor(() => {
        expect(localStorage.getItem('features')).not.toBeNull();
        expect(link(adminPath)).not.toBeNull();
    });
}

function renderNavbar() {
    storeAdminAccess(me);
    return render(
        <MemoryRouter initialEntries={['/dashboard']}>
            <Navbar />
        </MemoryRouter>,
    );
}

describe('Navbar Meshtastic entry', () => {
    beforeEach(() => {
        localStorage.clear();
        resetFeatures();
        me = { roles: [{ name: 'administrator' }] };
    });

    it('is hidden when the server has Meshtastic off', async () => {
        features = { map: true, meshtastic: false };
        renderNavbar();
        await settled('/users');
        expect(link('/meshtastic')).toBeNull();
    });

    it('is shown to an administrator when the server has it on', async () => {
        features = { map: true, meshtastic: true };
        renderNavbar();
        await waitFor(() => expect(link('/meshtastic')).not.toBeNull());
        expect(link('/users')).not.toBeNull();
    });

    it('is hidden from a read-only admin without the Meshtastic area', async () => {
        features = { map: true, meshtastic: true };
        me = { roles: [{ name: 'admin_viewer' }], admin_view_areas: ['groups'] };
        renderNavbar();
        await settled('/groups');
        expect(link('/meshtastic')).toBeNull();
    });

    it('is shown to a read-only admin with the Meshtastic area', async () => {
        features = { map: true, meshtastic: true };
        me = { roles: [{ name: 'admin_viewer' }], admin_view_areas: ['meshtastic'] };
        renderNavbar();
        await waitFor(() => expect(link('/meshtastic')).not.toBeNull());
        expect(link('/users')).toBeNull();
    });
});
