// What the map shows and how big: the categories the filter panel toggles, the
// icon scale, and the TAK-style team member icon. Kept free of Leaflet and
// React so it can be unit tested.

export type Category =
    | 'usersOnline'
    | 'usersOffline'
    | 'friendly'
    | 'hostile'
    | 'neutral'
    | 'unknown'
    | 'other'
    | 'casevac'
    | 'rbLines'
    | 'fovs';

export const CATEGORIES: Category[] = [
    'usersOnline', 'usersOffline', 'friendly', 'hostile', 'neutral', 'unknown', 'other', 'casevac', 'rbLines', 'fovs',
];

export interface MapDisplaySettings {
    /** Multiplier on every icon's size: 0.5 (small) to 1.5 (large). */
    iconScale: number;
    labels: boolean;
    show: Record<Category, boolean>;
}

export const MIN_SCALE = 0.5;
export const MAX_SCALE = 1.5;

export const DEFAULT_SETTINGS: MapDisplaySettings = {
    iconScale: 1,
    labels: true,
    show: Object.fromEntries(CATEGORIES.map((c) => [c, true])) as Record<Category, boolean>,
};

const STORAGE_KEY = 'raven.map.display';

/** Saved settings merged over the defaults; the defaults if storage is empty, unreadable or blocked. */
export function loadSettings(storage: Pick<Storage, 'getItem'> | undefined = safeStorage()): MapDisplaySettings {
    try {
        const raw = storage?.getItem(STORAGE_KEY);
        if (!raw) { return DEFAULT_SETTINGS; }
        const saved = JSON.parse(raw);
        const scale = Number(saved?.iconScale);
        const show = { ...DEFAULT_SETTINGS.show };
        for (const c of CATEGORIES) {
            if (typeof saved?.show?.[c] === 'boolean') { show[c] = saved.show[c]; }
        }
        return {
            iconScale: Number.isFinite(scale) ? clampScale(scale) : DEFAULT_SETTINGS.iconScale,
            labels: typeof saved?.labels === 'boolean' ? saved.labels : DEFAULT_SETTINGS.labels,
            show,
        };
    } catch {
        return DEFAULT_SETTINGS;
    }
}

export function saveSettings(settings: MapDisplaySettings, storage: Pick<Storage, 'setItem'> | undefined = safeStorage()) {
    try {
        storage?.setItem(STORAGE_KEY, JSON.stringify(settings));
    } catch {
        // Private window or blocked storage: the settings just aren't remembered.
    }
}

function safeStorage(): Storage | undefined {
    try {
        return window.localStorage;
    } catch {
        return undefined;
    }
}

export function clampScale(scale: number): number {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

/**
 * Which filter a marker belongs to, from its affiliation (the server derives
 * it from the CoT type). Suspect, joker and faker count as hostile, pending as
 * unknown and assumed friend as friendly, the way TAK colours them.
 */
export function markerCategory(marker: { affiliation?: string | null; type?: string | null }): Category {
    let affiliation = marker.affiliation ?? null;
    if (!affiliation && marker.type) {
        const m = /^a-([a-z])-/.exec(marker.type);
        affiliation = m ? ({ f: 'friendly', a: 'assumed', h: 'hostile', s: 'suspect', j: 'joker', k: 'faker', n: 'neutral', u: 'unknown', p: 'pending' } as Record<string, string>)[m[1]] ?? null : null;
    }
    switch (affiliation) {
        case 'friendly':
        case 'assumed':
            return 'friendly';
        case 'hostile':
        case 'suspect':
        case 'joker':
        case 'faker':
            return 'hostile';
        case 'neutral':
            return 'neutral';
        case 'unknown':
        case 'pending':
            return 'unknown';
        default:
            return 'other';
    }
}

/** The abbreviation ATAK draws inside a team member's circle, or '' for a plain member. */
export function roleAbbreviation(role: string | null | undefined): string {
    switch ((role ?? '').trim().toLowerCase()) {
        case 'team lead': return 'TL';
        case 'hq': return 'HQ';
        case 'sniper': return 'S';
        case 'medic': return 'M';
        case 'forward observer': return 'FO';
        case 'rto': return 'R';
        case 'k9': return 'K9';
        default: return '';
    }
}

export const BASE_USER_ICON = 22;
const OFFLINE_FILL = '#9e9e9e';
const DEFAULT_TEAM_COLOR = '#ffffff';

function isLight(hex: string): boolean {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex);
    if (!m) { return true; }
    const n = parseInt(m[1], 16);
    const r = (n >> 16) & 255;
    const g = (n >> 8) & 255;
    const b = n & 255;
    return 0.299 * r + 0.587 * g + 0.114 * b > 150;
}

function escapeXml(text: string): string {
    return text.replace(/[<>&"']/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&#39;' })[c] as string);
}

export interface TeamIconOptions {
    color?: string | null;
    role?: string | null;
    online: boolean;
    /** Degrees clockwise from north; the pointer is only drawn while moving. */
    course?: number | null;
    speed?: number | null;
    scale: number;
}

/**
 * The TAK team member icon as an SVG string: a circle in the team's colour
 * with a white ring and a dark outline, the role abbreviation inside it, and a
 * heading pointer outside it while the user is moving. Grey when offline. Only
 * the pointer rotates, so the role text stays upright.
 */
export function teamIconSvg({ color, role, online, course, speed, scale }: TeamIconOptions): { svg: string; size: number } {
    const size = Math.round(BASE_USER_ICON * clampScale(scale) * 1.6);
    const fill = online ? (color || DEFAULT_TEAM_COLOR) : OFFLINE_FILL;
    const abbreviation = roleAbbreviation(role);
    const moving = online && course !== null && course !== undefined && Number.isFinite(Number(course)) && (speed ?? 0) > 0.3;
    // A 32x32 viewBox: circle of radius 9 in the middle, pointer between radius 10 and 15.
    const pointer = moving
        ? `<path d="M16 1 L20 9 L12 9 Z" fill="${fill}" stroke="#111" stroke-width="1" stroke-linejoin="round" transform="rotate(${Number(course)} 16 16)"/>`
        : '';
    const text = abbreviation
        ? `<text x="16" y="16" text-anchor="middle" dominant-baseline="central" font-family="Arial, sans-serif" font-weight="700" font-size="${abbreviation.length > 1 ? 7.5 : 9}" fill="${isLight(fill) ? '#111' : '#fff'}">${escapeXml(abbreviation)}</text>`
        : '';
    const svg = [
        `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="${size}" height="${size}" style="opacity:${online ? 1 : 0.65};overflow:visible">`,
        pointer,
        `<circle cx="16" cy="16" r="9" fill="${fill}" stroke="#111" stroke-width="3.5"/>`,
        `<circle cx="16" cy="16" r="9" fill="${fill}" stroke="#fff" stroke-width="1.6"/>`,
        text,
        '</svg>',
    ].join('');
    return { svg, size };
}
