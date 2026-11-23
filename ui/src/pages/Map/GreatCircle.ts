// Spherical-earth helpers. Angles are degrees in and out.

const RADIUS: Record<string, number> = {
  M: 6371009,
  KM: 6371.009,
  MI: 3958.761,
  NM: 3440.07,
  YD: 6967420,
  FT: 20902260,
};

const toRad = (deg: number) => (deg * Math.PI) / 180;
const toDeg = (rad: number) => (rad * 180) / Math.PI;

const GreatCircle = {
  /** Mean earth radius in the given unit; kilometres for an unknown unit. */
  validateRadius(unit: string): number {
    return RADIUS[unit] ?? RADIUS.KM;
  },

  /** Distance between two points (Vincenty's special case on a sphere). */
  distance(lat1: number, lon1: number, lat2: number, lon2: number, unit?: string): number {
    const r = GreatCircle.validateRadius(unit ?? 'KM');
    const p1 = toRad(lat1);
    const p2 = toRad(lat2);
    const dl = toRad(lon2 - lon1);
    const x = Math.cos(p2) * Math.sin(dl);
    const y = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    const a = x * x + y * y;
    const b = Math.sin(p1) * Math.sin(p2) + Math.cos(p1) * Math.cos(p2) * Math.cos(dl);
    return Math.atan2(Math.sqrt(a), b) * r;
  },

  /** Initial bearing from point 1 to point 2, in [0, 360). */
  bearing(lat1: number, lon1: number, lat2: number, lon2: number): number {
    const p1 = toRad(lat1);
    const p2 = toRad(lat2);
    const dl = toRad(lon2 - lon1);
    const theta = Math.atan2(
      Math.sin(dl) * Math.cos(p2),
      Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)
    );
    const deg = toDeg(theta);
    return deg < 0 ? deg + 360 : deg;
  },

  /** The point `dt` away from (lat1, lon1) along bearing `brng`. Longitude is not wrapped. */
  destination(
    lat1: number,
    lon1: number,
    brng: number,
    dt: number,
    unit?: string
  ): { LAT: number; LON: number } {
    const r = GreatCircle.validateRadius(unit ?? 'KM');
    const delta = dt / r;
    const theta = toRad(brng);
    const p1 = toRad(lat1);
    const l1 = toRad(lon1);
    const p3 = Math.asin(
      Math.sin(p1) * Math.cos(delta) + Math.cos(p1) * Math.sin(delta) * Math.cos(theta)
    );
    const l3 =
      l1 +
      Math.atan2(
        Math.sin(theta) * Math.sin(delta) * Math.cos(p1),
        Math.cos(delta) - Math.sin(p1) * Math.sin(p3)
      );
    return { LAT: toDeg(p3), LON: toDeg(l3) };
  },
};

export default GreatCircle;
