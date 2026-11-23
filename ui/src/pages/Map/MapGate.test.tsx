import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '../../../test-utils';

let mapEnabled = true;
let featuresMissing = false;

vi.mock('../../axios_config', () => ({
  default: {
    get: vi.fn((url: string) =>
      url === '/api/features'
        ? featuresMissing
          ? Promise.reject(Object.assign(new Error('404'), { response: { status: 404 } }))
          : Promise.resolve({ status: 200, data: { map: mapEnabled } })
        : Promise.reject(new Error(`unmocked ${url}`))
    ),
  },
}));

// Stands in for the real map, which would start fetching tiles
const mapLoaded = vi.fn();
vi.mock('./Map.tsx', () => ({
  default: () => {
    mapLoaded();
    return <div>leaflet map</div>;
  },
}));

import MapGate from './MapGate';
import { resetFeatures } from '../../features';

describe('MapGate', () => {
  beforeEach(() => {
    localStorage.clear();
    resetFeatures();
    mapLoaded.mockClear();
    featuresMissing = false;
  });

  it('keeps the map on a server that predates the setting (no /api/features)', async () => {
    featuresMissing = true;
    render(<MapGate />);
    expect(await screen.findByText('leaflet map')).toBeInTheDocument();
  });

  it('loads the map when the server has it on', async () => {
    mapEnabled = true;
    render(<MapGate />);
    expect(await screen.findByText('leaflet map')).toBeInTheDocument();
  });

  it('never loads the map (or its tiles) when the server has it off', async () => {
    mapEnabled = false;
    render(<MapGate />);
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(mapLoaded).not.toHaveBeenCalled();
  });

  it('waits for the server rather than trusting nothing', () => {
    mapEnabled = true;
    render(<MapGate />);
    expect(screen.queryByText('leaflet map')).toBeNull();
    expect(mapLoaded).not.toHaveBeenCalled();
  });
});
