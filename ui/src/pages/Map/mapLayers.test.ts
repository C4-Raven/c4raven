import { describe, expect, it } from 'vitest';
import { MAP_LAYERS } from './mapLayers';

describe('map layer table', () => {
  it('fetches every source over https with plain query separators', () => {
    for (const { id, source } of MAP_LAYERS) {
      expect(source.url.startsWith('https://'), id).toBe(true);
      // An HTML entity inside a JS string reaches the tile server literally.
      expect(source.url, id).not.toContain('&amp;');
    }
  });

  it('opens on exactly one base map', () => {
    const initial = MAP_LAYERS.filter((l) => l.initial);
    expect(initial).toHaveLength(1);
    expect(initial[0].role).toBe('base');
  });

  it('has unique ids and labels', () => {
    expect(new Set(MAP_LAYERS.map((l) => l.id)).size).toBe(MAP_LAYERS.length);
    expect(new Set(MAP_LAYERS.map((l) => l.label)).size).toBe(MAP_LAYERS.length);
  });

  it('lists base maps before overlays', () => {
    const roles = MAP_LAYERS.map((l) => l.role);
    expect(roles.indexOf('overlay')).toBeGreaterThan(roles.lastIndexOf('base'));
  });

  it('uses a {s} placeholder exactly when subdomains are given', () => {
    for (const { id, source } of MAP_LAYERS) {
      if (source.kind === 'tiles') {
        expect(source.url.includes('{s}'), id).toBe(!!source.subdomains);
      }
    }
  });

  it('keeps the expected base maps and overlays', () => {
    const ids = (role: 'base' | 'overlay') =>
      MAP_LAYERS.filter((l) => l.role === role).map((l) => l.id);
    expect(ids('base')).toEqual([
      'osm',
      'carto-light',
      'carto-dark',
      'google-road',
      'google-satellite',
      'google-terrain',
      'esri-imagery',
      'esri-topo',
    ]);
    expect(ids('overlay')).toEqual([
      'google-roads-overlay',
      'google-relief-overlay',
      'street-view',
      'nexrad',
    ]);
  });
});
