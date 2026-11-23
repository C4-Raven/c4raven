import { LayersControl, TileLayer, WMSTileLayer } from 'react-leaflet';

/**
 * Background maps and overlays offered in the map's layer picker, as data. The picker
 * lists base maps first (radio buttons, exactly one shown) and overlays after
 * (checkboxes, any number). Every source is fetched over https.
 */

const OSM_CREDIT =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const CARTO_CREDIT = `${OSM_CREDIT} &copy; <a href="https://carto.com/attributions">CARTO</a>`;
const ESRI_CREDIT = 'Tiles &copy; Esri';
const GOOGLE_CREDIT = 'Map data &copy; Google';
const IEM_CREDIT =
  'Radar &copy; <a href="https://mesonet.agron.iastate.edu/">Iowa Environmental Mesonet</a>';

/** Google's tile servers mt0..mt3 serve the same tiles; spread requests over them. */
const GOOGLE_HOSTS = ['0', '1', '2', '3'];
const googleTiles = (layer: string) =>
  `https://mt{s}.google.com/vt/lyrs=${layer}&hl=en&x={x}&y={y}&z={z}`;

/** Deepest zoom the map can reach; sources with less detail stretch their last level. */
export const MAP_MAX_ZOOM = 20;

interface TileSource {
  kind: 'tiles';
  url: string;
  subdomains?: string[];
  /** Deepest level the server actually has tiles for. */
  nativeZoom: number;
  /** Some servers (OSM) refuse tile requests that carry no Referer. */
  sendReferrer?: boolean;
}

interface WmsSource {
  kind: 'wms';
  url: string;
  layers: string;
}

export interface MapLayerDef {
  id: string;
  label: string;
  role: 'base' | 'overlay';
  source: TileSource | WmsSource;
  credit?: string;
  /** The base map shown when the page opens. */
  initial?: boolean;
}

export const MAP_LAYERS: MapLayerDef[] = [
  {
    id: 'osm',
    label: 'OpenStreetMap',
    role: 'base',
    initial: true,
    credit: OSM_CREDIT,
    source: {
      kind: 'tiles',
      url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      nativeZoom: 19,
      sendReferrer: true,
    },
  },
  {
    id: 'carto-light',
    label: 'CARTO Light',
    role: 'base',
    credit: CARTO_CREDIT,
    source: {
      kind: 'tiles',
      url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
      subdomains: ['a', 'b', 'c', 'd'],
      nativeZoom: 20,
    },
  },
  {
    id: 'carto-dark',
    label: 'CARTO Dark',
    role: 'base',
    credit: CARTO_CREDIT,
    source: {
      kind: 'tiles',
      url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
      subdomains: ['a', 'b', 'c', 'd'],
      nativeZoom: 20,
    },
  },
  {
    id: 'google-road',
    label: 'Google Road',
    role: 'base',
    credit: GOOGLE_CREDIT,
    source: { kind: 'tiles', url: googleTiles('m'), subdomains: GOOGLE_HOSTS, nativeZoom: 20 },
  },
  {
    id: 'google-satellite',
    label: 'Google Satellite + Labels',
    role: 'base',
    credit: GOOGLE_CREDIT,
    source: { kind: 'tiles', url: googleTiles('y'), subdomains: GOOGLE_HOSTS, nativeZoom: 20 },
  },
  {
    id: 'google-terrain',
    label: 'Google Terrain',
    role: 'base',
    credit: GOOGLE_CREDIT,
    source: { kind: 'tiles', url: googleTiles('p'), subdomains: GOOGLE_HOSTS, nativeZoom: 20 },
  },
  {
    id: 'esri-imagery',
    label: 'Esri Imagery',
    role: 'base',
    credit: ESRI_CREDIT,
    source: {
      kind: 'tiles',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      nativeZoom: 19,
    },
  },
  {
    id: 'esri-topo',
    label: 'Esri Topographic',
    role: 'base',
    credit: ESRI_CREDIT,
    source: {
      kind: 'tiles',
      url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
      nativeZoom: 19,
    },
  },
  {
    id: 'google-roads-overlay',
    label: 'Roads and labels',
    role: 'overlay',
    credit: GOOGLE_CREDIT,
    source: { kind: 'tiles', url: googleTiles('h'), subdomains: GOOGLE_HOSTS, nativeZoom: 20 },
  },
  {
    id: 'google-relief-overlay',
    label: 'Terrain shading',
    role: 'overlay',
    credit: GOOGLE_CREDIT,
    source: { kind: 'tiles', url: googleTiles('t'), subdomains: GOOGLE_HOSTS, nativeZoom: 20 },
  },
  {
    id: 'street-view',
    label: 'Street View coverage',
    role: 'overlay',
    credit: GOOGLE_CREDIT,
    // Blue Street View lines on transparent tiles, as Google Maps itself requests them.
    source: {
      kind: 'tiles',
      url:
        'https://www.google.com/maps/vt?pb=!1m7!8m6!1m3!1i{z}!2i{x}!3i{y}!2i9!3x1!2m8!1e2!2ssvv' +
        '!4m2!1scc!2s*211m3*211e2*212b1*213e2*212b1*214b1!4m2!1ssvl!2s*211b0*212b1!3m8!2sen' +
        '!3sus!5e1105!12m4!1e68!2m2!1sset!2sRoadmap!4e0!5m4!1e0!8m2!1e1!1e1!6m6!1e12!2i2' +
        '!11e0!39b0!44e0!50e0',
      nativeZoom: 20,
    },
  },
  {
    id: 'nexrad',
    label: 'Weather radar (US)',
    role: 'overlay',
    credit: IEM_CREDIT,
    // NEXRAD base reflectivity composite, refreshed every five minutes.
    source: {
      kind: 'wms',
      url: 'https://mesonet.agron.iastate.edu/cgi-bin/wms/nexrad/n0q.cgi',
      layers: 'nexrad-n0q-900913',
    },
  },
];

function LayerSource({ def }: { def: MapLayerDef }) {
  const { source } = def;
  // Overlays draw in Leaflet's overlay pane so they always sit above the base map. Options
  // are only set when needed: Leaflet copies an explicit `undefined` over its default.
  const pane = def.role === 'overlay' ? { pane: 'overlayPane' } : {};
  if (source.kind === 'wms') {
    return (
      <WMSTileLayer
        url={source.url}
        params={{ layers: source.layers, format: 'image/png', transparent: true }}
        attribution={def.credit}
        {...pane}
      />
    );
  }
  return (
    <TileLayer
      url={source.url}
      subdomains={source.subdomains ?? 'abc'}
      maxNativeZoom={source.nativeZoom}
      maxZoom={MAP_MAX_ZOOM}
      attribution={def.credit}
      {...(source.sendReferrer
        ? { referrerPolicy: 'strict-origin-when-cross-origin' as const }
        : {})}
      {...pane}
    />
  );
}

/** The layer picker, built from MAP_LAYERS. */
export function MapLayerPicker({ layers = MAP_LAYERS }: { layers?: MapLayerDef[] }) {
  return (
    <LayersControl position="topright">
      {layers.map((def) =>
        def.role === 'base' ? (
          <LayersControl.BaseLayer key={def.id} name={def.label} checked={!!def.initial}>
            <LayerSource def={def} />
          </LayersControl.BaseLayer>
        ) : (
          <LayersControl.Overlay key={def.id} name={def.label}>
            <LayerSource def={def} />
          </LayersControl.Overlay>
        )
      )}
    </LayersControl>
  );
}
