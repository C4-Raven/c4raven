import { Suspense } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from 'i18next';
import { render, screen } from '@test-utils';

let features: Record<string, boolean> | 'missing' = { map: true, meshtastic: false };
const requested: string[] = [];

vi.mock('../axios_config', () => ({
    default: {
        get: vi.fn((url: string) => {
            requested.push(url);
            if (url === '/api/features') {
                return features === 'missing'
                    ? Promise.reject(Object.assign(new Error('404'), { response: { status: 404 } }))
                    : Promise.resolve({ status: 200, data: features });
            }
            return Promise.reject(new Error(`unmocked ${url}`));
        }),
    },
}));

// Stands in for the real page, which would call /api/meshtastic
const pageLoaded = vi.fn();
vi.mock('./Meshtastic.tsx', () => ({
    default: () => {
        pageLoaded();
        return <div>meshtastic page</div>;
    },
}));

import MeshtasticGate from './MeshtasticGate';
import { resetFeatures } from '../features';

i18n.init({ lng: 'en', resources: {}, interpolation: { escapeValue: false } });

const renderGate = () => render(<Suspense fallback={null}><MeshtasticGate /></Suspense>);

describe('MeshtasticGate', () => {
    beforeEach(() => {
        localStorage.clear();
        resetFeatures();
        pageLoaded.mockClear();
        requested.length = 0;
    });

    it('shows the off notice, and never loads the page, when the server has Meshtastic off', async () => {
        features = { map: true, meshtastic: false };
        renderGate();
        expect(await screen.findByText('Meshtastic is turned off on this server')).toBeInTheDocument();
        expect(screen.getByText(/RAVEN_ENABLE_MESHTASTIC in config.yml/)).toBeInTheDocument();
        expect(pageLoaded).not.toHaveBeenCalled();
        expect(requested.filter((u) => u.startsWith('/api/meshtastic'))).toEqual([]);
    });

    it('treats a server without /api/features as Meshtastic off', async () => {
        features = 'missing';
        renderGate();
        expect(await screen.findByText('Meshtastic is turned off on this server')).toBeInTheDocument();
        expect(pageLoaded).not.toHaveBeenCalled();
    });

    it('does not trust a cached "on" before the server answers', () => {
        localStorage.setItem('features', JSON.stringify({ map: true, meshtastic: true }));
        features = { map: true, meshtastic: false };
        renderGate();
        expect(pageLoaded).not.toHaveBeenCalled();
    });

    it('loads the page when the server has it on', async () => {
        features = { map: true, meshtastic: true };
        renderGate();
        expect(await screen.findByText('meshtastic page')).toBeInTheDocument();
    });
});
