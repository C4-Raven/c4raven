import { describe, expect, it } from 'vitest';

// The map page crashed on load in the production build ("L is not defined"): a classic Leaflet
// plugin ran before Leaflet was on the window. Nothing else imports the page, so load it here.
describe('Map page module', () => {
  it('loads without a global L already present', async () => {
    delete (globalThis as { L?: unknown }).L;
    const mod = (await import('./Map.tsx')) as { default: unknown };
    expect(typeof mod.default).toBe('function');
    expect((globalThis as { L?: { Marker?: { prototype: { slideTo?: unknown } } } }).L?.Marker?.prototype.slideTo).toBeTypeOf('function');
  });
});
