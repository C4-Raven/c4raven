/**
 * Pure helpers behind the Federation Hub connection map (FederationPolicyDiagram):
 * readable node names, partner grouping, edge pairing, layout and fit-to-view.
 * Kept free of React so they can be unit tested on their own.
 */

export interface XY { x: number; y: number }

/** Live state of a map node: link up, set up but not connected, failing, or turned off. */
export type MapStatus = 'connected' | 'waiting' | 'down' | 'off';

/** Partner kinds, the same values as the Federation Hub page's PartnerKind. */
export type MapKind = 'raven_hub' | 'tak_server' | 'other';

export interface MapEntityLike {
    id: string;
    type: string;
    displayName?: string;
    name?: string;
    description?: string | null;
    config?: Record<string, any>;
}

export interface MapRuleLike {
    id: string;
    source: string;
    destination: string;
}

const UUID_SUFFIX = /_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LONG_HEX = /[0-9a-f]{32,}/i;

/** A certificate fingerprint in any usual spelling ("AB:12:…:7C", "ab12cd…", "AB 12 CD"). */
export function looksLikeFingerprint(text: string): boolean {
    const s = text.trim();
    if (!s) { return false; }
    if (LONG_HEX.test(s.replace(/[:\s-]/g, '')) && /^[0-9a-f:\s.…-]+$/i.test(s)) { return true; }
    const hexDigits = (s.match(/[0-9a-f]/gi) ?? []).length;
    return /^[0-9a-f:\s.…-]+$/i.test(s) && /[:…]/.test(s) && hexDigits >= 8;
}

/** An IP address, host:port or URL: never shown on the map. */
export function looksLikeAddress(text: string): boolean {
    const s = text.trim();
    if (!s) { return false; }
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) { return true; }
    if (/^\d{1,3}(\.\d{1,3}){3}(:\d+)?$/.test(s)) { return true; }
    if (/^\[?[0-9a-f]*:[0-9a-f:]+\]?(:\d+)?$/i.test(s) && (s.match(/:/g) ?? []).length >= 2) { return true; }
    // host names: dotted, no spaces, ends in a TLD-ish label or a port
    return /^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?$/i.test(s) && !/\s/.test(s);
}

/** "CN=County TAK CA,OU=…-<sha256>" (a hub CA group uid) -> "County TAK CA". */
export function commonNameFromUid(uid: string | undefined | null): string {
    if (!uid) { return ''; }
    const dn = uid.replace(/-[0-9a-f]{32,}$/i, '');
    const m = /(?:^|[,/]\s*)CN\s*=\s*([^,/]+)/i.exec(dn);
    return m ? m[1].trim() : '';
}

/** A name that is safe and useful to show, or '' (fingerprints, addresses and raw ids are dropped). */
export function cleanName(raw: string | undefined | null): string {
    if (!raw) { return ''; }
    let s = raw.trim().replace(UUID_SUFFIX, '');
    if (!s) { return ''; }
    if (LONG_HEX.test(s)) {
        // A CA uid ("<subject DN>-<sha256>"): its common name is the readable part.
        s = commonNameFromUid(s);
    }
    if (!s || looksLikeFingerprint(s) || looksLikeAddress(s)) { return ''; }
    return s;
}

/** Best readable name for a policy node; '' when nothing readable is known. */
export function entityName(entity: MapEntityLike, preferred?: string | null): string {
    const candidates = [preferred, entity.displayName, entity.name];
    for (const c of candidates) {
        const clean = cleanName(c);
        if (clean) { return clean; }
    }
    return entity.type === 'FederationGroup' ? commonNameFromUid(entity.name) : '';
}

/** Raven's bookkeeping in an entity description: {"raven": {"kind": ..., "ca": ...}}. */
export function entityMeta(entity: MapEntityLike): { kind?: MapKind; ca?: string } {
    if (!entity.description) { return {}; }
    try {
        const data = JSON.parse(entity.description);
        const meta = data && typeof data === 'object' ? data.raven : null;
        if (!meta || typeof meta !== 'object') { return {}; }
        const kind = ['raven_hub', 'tak_server', 'other'].includes(meta.kind) ? meta.kind as MapKind : undefined;
        return { kind, ca: typeof meta.ca === 'string' ? meta.ca : undefined };
    } catch {
        return {};
    }
}

/**
 * Outgoing nodes that belong to a partner's CA node (Raven stores the CA uid in
 * the outgoing node's metadata), mapped outgoing id -> CA node id. The read-only
 * map draws such a pair as the one partner it is.
 */
export function partnerAliases(entities: MapEntityLike[]): Record<string, string> {
    const caByName: Record<string, string> = {};
    entities.forEach((e) => {
        if (e.type === 'FederationGroup' && e.name && !caByName[e.name]) { caByName[e.name] = e.id; }
    });
    const aliases: Record<string, string> = {};
    entities.forEach((e) => {
        if (e.type !== 'FederationOutgoing') { return; }
        const ca = entityMeta(e).ca;
        if (ca && caByName[ca]) { aliases[e.id] = caByName[ca]; }
    });
    return aliases;
}

/** One line on the map: every rule between two nodes, either way. `a` < `b`. */
export interface MapEdge<R extends MapRuleLike = MapRuleLike> {
    key: string;
    a: string;
    b: string;
    /** Rules a -> b. */
    ab: R[];
    /** Rules b -> a. */
    ba: R[];
}

/** Rules folded into one edge per node pair, so "share both ways" is one line with two arrow heads. */
export function pairEdges<R extends MapRuleLike>(rules: R[], nodeIds: Set<string>, aliases: Record<string, string> = {}): MapEdge<R>[] {
    const byKey = new Map<string, MapEdge<R>>();
    rules.forEach((r) => {
        const s = aliases[r.source] ?? r.source;
        const d = aliases[r.destination] ?? r.destination;
        if (s === d || !nodeIds.has(s) || !nodeIds.has(d)) { return; }
        const [a, b] = s < d ? [s, d] : [d, s];
        const key = `${a}|${b}`;
        let edge = byKey.get(key);
        if (!edge) {
            edge = { key, a, b, ab: [], ba: [] };
            byKey.set(key, edge);
        }
        (s === a ? edge.ab : edge.ba).push(r);
    });
    return Array.from(byKey.values());
}

/**
 * Hub-and-spoke layout: `centre` (this server) in the middle, everything else
 * on an ellipse around it, spaced so cards never overlap. Coordinates are card
 * top-left corners around (0, 0).
 */
export function autoLayout(ids: string[], centre: string | null, width: number, height: number): Record<string, XY> {
    const pos: Record<string, XY> = {};
    const others = ids.filter((id) => id !== centre);
    const hasCentre = !!centre && ids.includes(centre);
    if (hasCentre) { pos[centre!] = { x: -width / 2, y: -height / 2 }; }
    const n = others.length;
    if (n === 0) { return pos; }
    if (!hasCentre && n === 1) {
        pos[others[0]] = { x: -width / 2, y: -height / 2 };
        return pos;
    }
    const rx = Math.max(width + 150, ((n * (width + 48)) / (2 * Math.PI)) * 1.15);
    const ry = Math.max(height + 112, rx * 0.5);
    // One or two partners sit level with the centre; three or more start at the top.
    const offset = n <= 2 ? Math.PI / 2 : 0;
    others.forEach((id, i) => {
        const angle = -Math.PI / 2 + offset + (2 * Math.PI * i) / n;
        pos[id] = { x: rx * Math.cos(angle) - width / 2, y: ry * Math.sin(angle) - height / 2 };
    });
    return pos;
}

export interface Insets { top: number; right: number; bottom: number; left: number }

/** Zoom and pan that fit every card inside the board (never zooming in past `maxZoom`). */
export function fitView(
    positions: XY[],
    width: number,
    height: number,
    board: { width: number; height: number },
    insets: Insets,
    limits: { min: number; max: number },
): { zoom: number; pan: XY } {
    if (positions.length === 0 || board.width <= 0 || board.height <= 0) {
        return { zoom: 1, pan: { x: board.width / 2, y: board.height / 2 } };
    }
    const minX = Math.min(...positions.map((p) => p.x));
    const minY = Math.min(...positions.map((p) => p.y));
    const maxX = Math.max(...positions.map((p) => p.x + width));
    const maxY = Math.max(...positions.map((p) => p.y + height));
    const availW = Math.max(board.width - insets.left - insets.right, 1);
    const availH = Math.max(board.height - insets.top - insets.bottom, 1);
    const raw = Math.min(availW / Math.max(maxX - minX, 1), availH / Math.max(maxY - minY, 1));
    const zoom = Math.round(Math.min(limits.max, Math.max(limits.min, raw)) * 100) / 100;
    const cx = insets.left + availW / 2;
    const cy = insets.top + availH / 2;
    return { zoom, pan: { x: cx - ((minX + maxX) / 2) * zoom, y: cy - ((minY + maxY) / 2) * zoom } };
}

/** Where the line from a card's centre towards `toward` leaves the card (plus a small gap). */
export function boxExit(centre: XY, toward: XY, width: number, height: number, gap = 0): XY {
    const dx = toward.x - centre.x;
    const dy = toward.y - centre.y;
    if (dx === 0 && dy === 0) { return centre; }
    const halfW = width / 2 + gap;
    const halfH = height / 2 + gap;
    const sx = dx !== 0 ? halfW / Math.abs(dx) : Infinity;
    const sy = dy !== 0 ? halfH / Math.abs(dy) : Infinity;
    const s = Math.min(sx, sy);
    return { x: centre.x + dx * s, y: centre.y + dy * s };
}

/** A gently bowed curve between two cards: SVG path, its midpoint (for a label) and both ends. */
export function edgeCurve(aCentre: XY, bCentre: XY, width: number, height: number, gap = 6): { d: string; mid: XY; start: XY; end: XY } {
    const dist = Math.max(Math.hypot(bCentre.x - aCentre.x, bCentre.y - aCentre.y), 1);
    const nx = -(bCentre.y - aCentre.y) / dist;
    const ny = (bCentre.x - aCentre.x) / dist;
    const bow = Math.min(32, dist * 0.09);
    const c = { x: (aCentre.x + bCentre.x) / 2 + nx * bow, y: (aCentre.y + bCentre.y) / 2 + ny * bow };
    const start = boxExit(aCentre, c, width, height, gap);
    const end = boxExit(bCentre, c, width, height, gap);
    const mid = { x: 0.25 * start.x + 0.5 * c.x + 0.25 * end.x, y: 0.25 * start.y + 0.5 * c.y + 0.25 * end.y };
    const r = (n: number) => Math.round(n * 10) / 10;
    return { d: `M${r(start.x)},${r(start.y)} Q${r(c.x)},${r(c.y)} ${r(end.x)},${r(end.y)}`, mid, start, end };
}
