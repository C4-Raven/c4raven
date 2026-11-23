import 'leaflet/dist/leaflet.css';
import 'react-leaflet-fullscreen/styles.css';
import './leafletGlobal';
import 'leaflet.marker.slideto';

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import L from 'leaflet';
import { MapContainer, ScaleControl } from 'react-leaflet';
import { FullscreenControl } from 'react-leaflet-fullscreen';
import ms from 'milsymbol';
import axios from 'axios';
import { Button, Divider, Drawer, Table } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { Delete20Regular, Dismiss20Regular } from '@fluentui/react-icons';
import instance from '../../axios_config';
import { apiRoutes } from '../../apiRoutes';
import { socket } from '../../socketio';
import GreatCircle from './GreatCircle';
import Video from './Video';
import MapDisplayControl from './MapDisplayControl';
import { MapLayerPicker } from './mapLayers';
import {
  CATEGORIES,
  type Category,
  loadSettings,
  type MapDisplaySettings,
  markerCategory,
  saveSettings,
  teamIconSvg,
} from './mapDisplay';
import classes from './Map.module.css';

type Json = Record<string, any>;

type SlidingMarker = L.Marker & {
  slideTo: (
    latlng: L.LatLngExpression,
    options: { duration: number; keepAtCenter: boolean }
  ) => void;
};

interface EudEntry {
  data: Json;
  point: Json | null;
  marker: L.Marker;
  category: Category | null;
}

interface MarkerEntry {
  data: Json;
  layer: L.Marker | L.CircleMarker;
  spot: boolean;
  category: Category;
}

interface CasevacEntry {
  data: Json;
  marker: L.Marker;
}

interface DrawerState {
  opened: boolean;
  uid: string | null;
  title: string;
  data: Json;
  livePoint: Json | null;
  deletable: boolean;
}

const VIDEO_POINT_TYPE = 'b-m-p-s-p-loc';
const FOV_DISTANCE_M = 100;

const CLOSED_DRAWER: DrawerState = {
  opened: false,
  uid: null,
  title: '',
  data: {},
  livePoint: null,
  deletable: false,
};

function isValidPosition(point: Json | null | undefined): point is Json {
  if (!point) {
    return false;
  }
  const { latitude, longitude } = point;
  return (
    typeof latitude === 'number' &&
    typeof longitude === 'number' &&
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    Math.abs(longitude) <= 180 &&
    Math.abs(latitude) <= 90
  );
}

/** ARGB hex from TAK ("ffff0000") to a CSS colour ("#ff0000"). */
function argbToCss(hex: unknown): string | undefined {
  return typeof hex === 'string' && hex.length > 2 ? `#${hex.slice(2)}` : undefined;
}

function hasValue(value: unknown): boolean {
  return value !== null && value !== undefined;
}

function setLabel(
  layer: L.Layer,
  text: unknown,
  show: boolean,
  offset: L.PointExpression = [0, 0]
) {
  layer.unbindTooltip();
  if (show && text) {
    layer.bindTooltip(String(text), {
      permanent: true,
      direction: 'bottom',
      opacity: 0.75,
      className: classes.label,
      offset,
    });
  }
}

function formatValue(value: unknown): string {
  if (value === true) {
    return 'True';
  }
  if (value === false) {
    return 'False';
  }
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  return String(value);
}

interface Row {
  key: string;
  value: ReactNode;
}

function buildRows(data: Json, livePoint: Json | null): { details: Row[]; position: Row[] } {
  const details: Row[] = [];
  const position: Row[] = [];
  const pushFields = (target: Row[], obj: Json) => {
    for (const [key, value] of Object.entries(obj)) {
      target.push({ key, value: formatValue(value) });
    }
  };

  for (const [key, value] of Object.entries(data)) {
    switch (key) {
      case 'icon':
        if (value) {
          details.push({ key, value: <img src={value.bitmap} alt="" style={{ maxWidth: 48 }} /> });
        }
        break;
      case 'point':
      case 'last_point':
        if (value) {
          pushFields(position, value);
        }
        break;
      case 'zmist':
        if (value) {
          pushFields(details, value);
        }
        break;
      case 'eud':
      case 'data_packages':
        break;
      default:
        details.push({ key, value: formatValue(value) });
    }
  }
  if (livePoint) {
    pushFields(position, livePoint);
  }
  return { details, position };
}

function KeyValueTable({ rows }: { rows: Row[] }) {
  return (
    <Table striped withRowBorders={false} fz="sm">
      <Table.Tbody>
        {rows.map((row, index) => (
          <Table.Tr key={`${row.key}-${index}`}>
            <Table.Td fw={600} style={{ wordBreak: 'break-word' }}>
              {row.key}
            </Table.Td>
            <Table.Td style={{ wordBreak: 'break-word' }}>{row.value}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

function emptyCounts(): Record<Category, number> {
  return Object.fromEntries(CATEGORIES.map((c) => [c, 0])) as Record<Category, number>;
}

export default function Map() {
  const [map, setMap] = useState<L.Map | null>(null);
  const [settings, setSettings] = useState<MapDisplaySettings>(() => loadSettings());
  const [counts, setCounts] = useState<Record<Category, number>>(emptyCounts);
  const [drawer, setDrawer] = useState<DrawerState>(CLOSED_DRAWER);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const settingsRef = useRef(settings);
  const drawerUidRef = useRef<string | null>(null);
  const loadedRef = useRef(false);

  const groupsRef = useRef<Record<Category, L.LayerGroup> | null>(null);
  if (groupsRef.current === null) {
    groupsRef.current = Object.fromEntries(CATEGORIES.map((c) => [c, L.layerGroup()])) as Record<
      Category,
      L.LayerGroup
    >;
  }
  const groups = groupsRef.current;

  const eudsRef = useRef(new globalThis.Map<string, EudEntry>());
  const markersRef = useRef(new globalThis.Map<string, MarkerEntry>());
  const casevacsRef = useRef(new globalThis.Map<string, CasevacEntry>());
  const rbLinesRef = useRef(new globalThis.Map<string, L.Polyline>());
  const fovsRef = useRef(new globalThis.Map<string, L.Polygon>());

  const updateCounts = useCallback(() => {
    setCounts(
      Object.fromEntries(CATEGORIES.map((c) => [c, groups[c].getLayers().length])) as Record<
        Category,
        number
      >
    );
  }, [groups]);

  // Glide when the marker is on the map; slideTo does nothing for a hidden one.
  const moveMarker = (marker: L.Marker, latlng: L.LatLngExpression, duration: number) => {
    if (mapRef.current?.hasLayer(marker)) {
      (marker as SlidingMarker).slideTo(latlng, { duration, keepAtCenter: false });
    } else {
      marker.setLatLng(latlng);
    }
  };

  const openDrawer = (state: Omit<DrawerState, 'opened'>) => {
    drawerUidRef.current = state.uid;
    setDrawer({ ...state, opened: true });
  };

  const closeDrawer = () => {
    drawerUidRef.current = null;
    setDrawer((d) => ({ ...d, opened: false }));
  };

  // ---- Users (EUDs) ----

  const drawEud = (entry: EudEntry) => {
    const { iconScale, labels } = settingsRef.current;
    const { data, point, marker } = entry;
    const pointType = point?.type ?? data.type;
    if (pointType === VIDEO_POINT_TYPE) {
      const size = Math.round(30 * iconScale);
      marker.setIcon(
        L.divIcon({
          html: renderToStaticMarkup(<Video />),
          className: classes.bareIcon,
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
          tooltipAnchor: [0, size / 2],
        })
      );
    } else {
      const { svg, size } = teamIconSvg({
        color: data.team_color,
        role: data.team_role,
        online: data.last_status === 'Connected',
        course: hasValue(point?.course) ? point?.course : point?.azimuth,
        speed: point?.speed,
        scale: iconScale,
      });
      marker.setIcon(
        L.divIcon({
          html: svg,
          className: classes.bareIcon,
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
          tooltipAnchor: [0, 0.32 * size],
        })
      );
    }
    setLabel(marker, data.callsign, labels);
  };

  const drawFov = (point: Json | null) => {
    // Only points carrying a usable camera bearing and angle get a field of view;
    // anything else would give NaN corners, which Leaflet throws on.
    if (!point || !hasValue(point.fov) || !hasValue(point.azimuth)) {
      return;
    }
    const azimuth = Number(point.azimuth);
    const fov = Number(point.fov);
    if (
      !Number.isFinite(azimuth) ||
      !Number.isFinite(fov) ||
      !Number.isFinite(point.latitude) ||
      !Number.isFinite(point.longitude)
    ) {
      return;
    }
    let a1 = azimuth - fov / 2;
    if (a1 < 0) {
      a1 += 360;
    }
    let a2 = azimuth + fov / 2;
    if (a2 === 360) {
      a2 = 0;
    } else if (a2 > 360) {
      a2 -= 360;
    }
    const { latitude, longitude } = point;
    const p1 = GreatCircle.destination(latitude, longitude, a1, FOV_DISTANCE_M, 'M');
    const p2 = GreatCircle.destination(latitude, longitude, a2, FOV_DISTANCE_M, 'M');
    const vertices: L.LatLngExpression[] = [
      [latitude, longitude],
      [p1.LAT, p1.LON],
      [p2.LAT, p2.LON],
    ];
    const key = point.device_uid;
    const existing = fovsRef.current.get(key);
    if (existing) {
      existing.setLatLngs(vertices);
    } else {
      const polygon = L.polygon(vertices, { color: 'gray' });
      fovsRef.current.set(key, polygon);
      groups.fovs.addLayer(polygon);
    }
    updateCounts();
  };

  const removeFov = (key: string) => {
    const polygon = fovsRef.current.get(key);
    if (polygon) {
      groups.fovs.removeLayer(polygon);
      fovsRef.current.delete(key);
      updateCounts();
    }
  };

  const placeEud = (entry: EudEntry) => {
    if (!entry.point) {
      return;
    }
    const category: Category =
      entry.data.last_status === 'Connected' ? 'usersOnline' : 'usersOffline';
    if (entry.category === category) {
      return;
    }
    if (entry.category) {
      groups[entry.category].removeLayer(entry.marker);
    } else {
      entry.marker.setLatLng([entry.point.latitude, entry.point.longitude]);
    }
    groups[category].addLayer(entry.marker);
    entry.category = category;
  };

  const onEud = (eud: Json) => {
    const uid = eud.uid;
    let entry = eudsRef.current.get(uid);
    if (!entry) {
      const point = isValidPosition(eud.last_point) ? eud.last_point : null;
      const marker = L.marker(point ? [point.latitude, point.longitude] : [0, 0]);
      const created: EudEntry = { data: eud, point, marker, category: null };
      marker.on('click', () => {
        openDrawer({
          uid,
          title: created.data.callsign,
          data: { ...created.data, last_point: null },
          livePoint: created.point,
          deletable: false,
        });
      });
      eudsRef.current.set(uid, created);
      entry = created;
    } else {
      entry.data = eud;
    }

    if (eud.last_status === 'Connected') {
      drawFov(entry.point);
    } else {
      removeFov(uid);
    }
    placeEud(entry);
    drawEud(entry);
    updateCounts();
  };

  const onPoint = (point: Json) => {
    if (!isValidPosition(point)) {
      return;
    }
    const entry = eudsRef.current.get(point.uid);
    if (entry) {
      const first = !entry.point;
      entry.point = point;
      if (first) {
        placeEud(entry);
      } else {
        moveMarker(entry.marker, [point.latitude, point.longitude], 1500);
      }
      drawEud(entry);
      if (drawerUidRef.current === point.uid) {
        setDrawer((d) => ({ ...d, livePoint: point }));
      }
    }
    drawFov(point);
    updateCounts();
  };

  // ---- Markers ----

  const drawMarker = (entry: MarkerEntry) => {
    const { iconScale, labels } = settingsRef.current;
    const { data, layer } = entry;
    if (layer instanceof L.CircleMarker) {
      const radius = 6 * iconScale;
      layer.setRadius(radius);
      layer.setStyle({ weight: 2, color: argbToCss(data.color_hex) });
      setLabel(layer, data.callsign, labels, [0, radius + 2]);
      return;
    }
    const marker = layer as L.Marker;
    if (data.mil_std_2525c !== null && data.icon === null) {
      const point = data.point ?? {};
      const direction = hasValue(point.azimuth)
        ? point.azimuth
        : hasValue(point.course)
          ? point.course
          : undefined;
      const symbol = new ms.Symbol(data.mil_std_2525c, {
        size: Math.round(25 * iconScale),
        ...(direction !== undefined ? { direction: Number(direction) } : {}),
      });
      const { width, height } = symbol.getSize();
      const anchor = symbol.getAnchor();
      marker.setIcon(
        L.divIcon({
          html: symbol.asSVG(),
          className: classes.bareIcon,
          iconSize: [width, height],
          iconAnchor: [anchor.x, anchor.y],
          tooltipAnchor: [width / 2 - anchor.x, height - anchor.y],
        })
      );
    } else if (data.icon !== null && data.icon !== undefined) {
      const size = Math.round(24 * iconScale);
      marker.setIcon(
        L.icon({
          iconUrl: data.icon.bitmap,
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
          tooltipAnchor: [0, size / 2],
        })
      );
    } else {
      const s = iconScale;
      marker.setIcon(
        L.icon({
          iconUrl: '/map_icons/marker-icon.png',
          shadowUrl: '/map_icons/marker-shadow.png',
          iconSize: [25 * s, 41 * s],
          iconAnchor: [12.5 * s, 41 * s],
          shadowSize: [41 * s, 41 * s],
          shadowAnchor: [12.5 * s, 41 * s],
          tooltipAnchor: [0, 0],
        })
      );
    }
    setLabel(marker, data.callsign, labels);
  };

  const deleteMarker = (uid: string) => {
    instance
      .delete(apiRoutes.mapMarkers, { params: { uid } })
      .then((response) => {
        if (response.status === 200) {
          const entry = markersRef.current.get(uid);
          if (entry) {
            groups[entry.category].removeLayer(entry.layer);
            entry.layer.remove();
            markersRef.current.delete(uid);
          }
          updateCounts();
          closeDrawer();
          notifications.show({ message: 'Marker deleted', color: 'green' });
        }
      })
      .catch((error) => {
        notifications.show({
          title: 'Failed to delete marker',
          message: error?.response?.data?.error,
          color: 'red',
        });
      });
  };

  const onMarker = (data: Json) => {
    if (!data.point) {
      return;
    }
    const uid = data.uid;
    const spot =
      typeof data.iconset_path === 'string' && data.iconset_path.includes('COT_MAPPING_SPOTMAP');
    const category = markerCategory(data);
    const latlng: L.LatLngExpression = [data.point.latitude, data.point.longitude];

    let entry = markersRef.current.get(uid);
    if (entry && entry.spot !== spot) {
      groups[entry.category].removeLayer(entry.layer);
      markersRef.current.delete(uid);
      entry = undefined;
    }

    if (!entry) {
      const layer = spot ? L.circleMarker(latlng) : L.marker(latlng);
      const created: MarkerEntry = { data, layer, spot, category };
      layer.on('click', () => {
        openDrawer({
          uid,
          title: created.data.callsign,
          data: created.data,
          livePoint: null,
          deletable: true,
        });
      });
      markersRef.current.set(uid, created);
      groups[category].addLayer(layer);
      entry = created;
    } else {
      entry.data = data;
      if (entry.layer instanceof L.CircleMarker) {
        entry.layer.setLatLng(latlng);
      } else {
        moveMarker(entry.layer as L.Marker, latlng, 2000);
      }
      if (entry.category !== category) {
        groups[entry.category].removeLayer(entry.layer);
        groups[category].addLayer(entry.layer);
        entry.category = category;
      }
    }
    drawMarker(entry);
    updateCounts();
  };

  // ---- CasEvac ----

  const drawCasevac = (entry: CasevacEntry) => {
    const { iconScale, labels } = settingsRef.current;
    const { data, marker } = entry;
    const size = Math.round(24 * iconScale);
    marker.setIcon(
      L.icon({
        iconUrl: data.icon?.bitmap,
        ...(data.icon?.shadow ? { shadowUrl: data.icon.shadow } : {}),
        iconSize: [size, size],
        iconAnchor: [size / 2, size],
        tooltipAnchor: [0, 0],
      })
    );
    setLabel(marker, data.title, labels);
  };

  const onCasevac = (data: Json) => {
    const uid = data.uid;
    const latlng: L.LatLngExpression = [data.point.latitude, data.point.longitude];
    let entry = casevacsRef.current.get(uid);
    if (!entry) {
      const marker = L.marker(latlng);
      const created: CasevacEntry = { data, marker };
      marker.on('click', () => {
        openDrawer({
          uid,
          title: created.data.title,
          data: created.data,
          livePoint: null,
          deletable: false,
        });
      });
      casevacsRef.current.set(uid, created);
      groups.casevac.addLayer(marker);
      entry = created;
    } else {
      entry.data = data;
      moveMarker(entry.marker, latlng, 2000);
    }
    drawCasevac(entry);
    updateCounts();
  };

  // ---- Range & bearing lines ----

  const onRbLine = (data: Json) => {
    const uid = data.uid;
    const existing = rbLinesRef.current.get(uid);
    if (existing) {
      groups.rbLines.removeLayer(existing);
    }
    const line = L.polyline(
      [
        [data.point.latitude, data.point.longitude],
        [data.end_latitude, data.end_longitude],
      ],
      { color: argbToCss(data.color_hex), weight: data.stroke_weight, interactive: false }
    );
    rbLinesRef.current.set(uid, line);
    groups.rbLines.addLayer(line);
    updateCounts();
  };

  // The latest handlers, for socket listeners registered once.
  const handlersRef = useRef({ onEud, onPoint, onMarker, onCasevac, onRbLine });
  handlersRef.current = { onEud, onPoint, onMarker, onCasevac, onRbLine };

  useEffect(() => {
    mapRef.current = map;
  }, [map]);

  // Initial state, once the Leaflet map exists.
  useEffect(() => {
    if (!map || loadedRef.current) {
      return;
    }
    loadedRef.current = true;
    axios
      .get(apiRoutes.mapState)
      .then((response) => {
        if (response.status !== 200) {
          return;
        }
        const h = handlersRef.current;
        const body = response.data ?? {};
        (body.euds ?? []).forEach((eud: Json) => h.onEud(eud));
        (body.markers ?? []).forEach((marker: Json) => h.onMarker(marker));
        (body.rb_lines ?? []).forEach((line: Json) => h.onRbLine(line));
        (body.casevacs ?? []).forEach((casevac: Json) => h.onCasevac(casevac));
      })
      .catch(() => {
        notifications.show({
          message: 'Failed to get map state',
          color: 'red',
          icon: <Dismiss20Regular />,
        });
      });
  }, [map]);

  // Live updates.
  useEffect(() => {
    const point = (data: Json) => handlersRef.current.onPoint(data);
    const rbLine = (data: Json) => handlersRef.current.onRbLine(data);
    const marker = (data: Json) => handlersRef.current.onMarker(data);
    const eud = (data: Json) => handlersRef.current.onEud(data);
    const casevac = (data: Json) => handlersRef.current.onCasevac(data);
    socket.on('point', point);
    socket.on('rb_line', rbLine);
    socket.on('marker', marker);
    socket.on('eud', eud);
    socket.on('casevac', casevac);
    return () => {
      socket.off('point', point);
      socket.off('rb_line', rbLine);
      socket.off('marker', marker);
      socket.off('eud', eud);
      socket.off('casevac', casevac);
    };
  }, []);

  // Keep the map sized to its container (the nav opening/closing resizes it).
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!map || !wrapper) {
      return undefined;
    }
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(wrapper);
    return () => observer.disconnect();
  }, [map]);

  // Category visibility.
  useEffect(() => {
    if (!map) {
      return;
    }
    for (const category of CATEGORIES) {
      if (settings.show[category]) {
        groups[category].addTo(map);
      } else {
        groups[category].remove();
      }
    }
  }, [map, settings.show, groups]);

  const changeSettings = (next: MapDisplaySettings) => {
    const previous = settingsRef.current;
    settingsRef.current = next;
    setSettings(next);
    saveSettings(next);
    if (previous.iconScale !== next.iconScale || previous.labels !== next.labels) {
      eudsRef.current.forEach(drawEud);
      markersRef.current.forEach(drawMarker);
      casevacsRef.current.forEach(drawCasevac);
    }
  };

  const rows = buildRows(drawer.data, drawer.livePoint);

  return (
    <div ref={wrapperRef} className={classes.fill}>
      <MapContainer
        ref={setMap}
        className={classes.map}
        center={[10, 0]}
        zoom={3}
        scrollWheelZoom
        style={{ width: '100%', height: '100%', zIndex: 90 }}
      >
        <MapDisplayControl settings={settings} counts={counts} onChange={changeSettings} />
        <ScaleControl />
        <MapLayerPicker />
        <FullscreenControl />
      </MapContainer>

      <Drawer
        opened={drawer.opened}
        onClose={closeDrawer}
        position="right"
        title={drawer.title}
        overlayProps={{ backgroundOpacity: 0 }}
      >
        {drawer.deletable && drawer.uid && (
          <Button
            color="red"
            variant="subtle"
            leftSection={<Delete20Regular />}
            onClick={() => deleteMarker(drawer.uid as string)}
          >
            Delete Marker
          </Button>
        )}
        <Divider label="Details" labelPosition="center" my="sm" />
        <KeyValueTable rows={rows.details} />
        <Divider label="Position" labelPosition="center" my="sm" />
        <KeyValueTable rows={rows.position} />
      </Drawer>
    </div>
  );
}
