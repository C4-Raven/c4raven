import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
vi.mock('./axios_config', () => ({ default: { get: (...args: unknown[]) => get(...args) } }));

import { loadFeatures, resetFeatures } from './features';

const notFound = () => Promise.reject(Object.assign(new Error('404'), { response: { status: 404 } }));

describe('loadFeatures', () => {
    beforeEach(() => {
        localStorage.clear();
        resetFeatures();
        get.mockReset();
    });

    it('reads both switches from the server', async () => {
        get.mockResolvedValue({ status: 200, data: { map: false, meshtastic: true } });
        expect(await loadFeatures()).toEqual({ map: false, meshtastic: true });
        expect(JSON.parse(String(localStorage.getItem('features')))).toEqual({ map: false, meshtastic: true });
    });

    it('treats a server without the meshtastic key as Meshtastic off', async () => {
        get.mockResolvedValue({ status: 200, data: { map: true } });
        expect(await loadFeatures()).toEqual({ map: true, meshtastic: false });
    });

    it('keeps the map and leaves Meshtastic off on a server without /api/features', async () => {
        get.mockImplementation(notFound);
        expect(await loadFeatures()).toEqual({ map: true, meshtastic: false });
    });

    it('asks only once per page load', async () => {
        get.mockResolvedValue({ status: 200, data: { map: true, meshtastic: false } });
        await loadFeatures();
        await loadFeatures();
        expect(get).toHaveBeenCalledTimes(1);
    });

    it('asks again after a failed request', async () => {
        get.mockRejectedValueOnce(new Error('network'));
        expect(await loadFeatures()).toBeNull();
        get.mockResolvedValue({ status: 200, data: { map: true, meshtastic: true } });
        expect(await loadFeatures()).toEqual({ map: true, meshtastic: true });
    });
});
