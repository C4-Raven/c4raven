import { useEffect, useState } from 'react';
import axios from './axios_config';
import { apiRoutes } from './apiRoutes';

/** The optional parts of the web UI this server has turned on (GET /api/features). */
export interface Features {
  map: boolean;
  /** The admin-only Meshtastic page (RAVEN_ENABLE_MESHTASTIC); off unless the server says on. */
  meshtastic: boolean;
}

/** Read the server's (or the cache's) answer: the map is on unless said off, Meshtastic off unless said on. */
function parse(data: any): Features {
  return { map: data?.map !== false, meshtastic: data?.meshtastic === true };
}

const STORAGE_KEY = 'features';

function cached(): Features | null {
  try {
    const value = JSON.parse(String(localStorage.getItem(STORAGE_KEY)));
    // A value cached before the meshtastic key existed reads as Meshtastic off
    return value && typeof value.map === 'boolean' ? parse(value) : null;
  } catch {
    return null;
  }
}

let request: Promise<Features | null> | null = null;

/** Fetched once per page load; a failed request is retried by the next caller. */
export function loadFeatures(): Promise<Features | null> {
  if (!request) {
    request = axios
      .get(apiRoutes.serverFeatures)
      .then((response) => {
        // A server from before the Meshtastic switch has no meshtastic key: off
        const features = parse(response.data);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(features));
        return features;
      })
      .catch((error) => {
        // A server from before RAVEN_MAP_ENABLE has no /api/features: it always had the map
        if (error?.response?.status === 404) {
          return { map: true, meshtastic: false };
        }
        request = null;
        return null;
      });
  }
  return request;
}

/** For tests: forget the fetched value. */
export function resetFeatures() {
  request = null;
}

/**
 * The server's features: the last known value straight away (null before the first fetch),
 * then the fresh one. A page that must not start before it knows (the map) waits for non-null.
 */
export function useFeatures(): Features | null {
  const [features, setFeatures] = useState<Features | null>(cached);
  useEffect(() => {
    let active = true;
    loadFeatures().then((fresh) => {
      if (active && fresh) {
        setFeatures(fresh);
      }
    });
    return () => {
      active = false;
    };
  }, []);
  return features;
}
