// Classic Leaflet plugins (leaflet.marker.slideto) extend a global `L` when they load, so
// Leaflet has to be on the window before them. Import this module ahead of any such plugin:
// ES modules run in import order, so the global is set by the time the plugin runs.
import L from 'leaflet';

(globalThis as unknown as { L: typeof L }).L = L;

export default L;
