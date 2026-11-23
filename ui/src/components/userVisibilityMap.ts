/**
 * Pure helpers behind the "Who Can See Whom" map (UserVisibilityDiagram):
 * node layout, curved connector geometry, the layer's bounding box and each
 * user's link summary. Kept free of React so they can be unit tested; the
 * shared box/fit maths lives in federationMap.
 */
import { autoLayout, boxExit, type XY } from './federationMap';

export interface VisibilityEdgeLike {
    source: string;
    target: string;
    type: 'solid' | 'dotted';
    manual: boolean;
}

/** Up to this many users sit on a ring (the same look as the federation map); more go round a board-shaped rectangle. */
export const RING_MAX = 16;

/**
 * Users around the edge of a rectangle shaped like the board (`aspect` = width / height),
 * so a big roster still fits at a readable zoom. Rows along the top and bottom are spaced
 * by card width, columns down the sides by card height, so no two cards overlap.
 * Coordinates are card top-left corners around (0, 0); order runs clockwise from the top left.
 */
export function perimeterLayout(ids: string[], width: number, height: number, aspect = 1.75, gapX = 20, gapY = 22): Record<string, XY> {
    const n = ids.length;
    const pos: Record<string, XY> = {};
    if (n === 0) { return pos; }
    const stepX = width + gapX;
    const stepY = height + gapY;
    // Smallest rectangle (centre to centre) whose rows and columns hold everyone.
    let h = stepY * 2;
    let rowCap = 0;
    let colCap = 0;
    for (let guard = 0; guard < 10000; guard++) {
        const w = Math.max(stepX, h * aspect);
        rowCap = Math.floor(w / stepX) + 1;
        // Side columns start a full step below the top row, so corners never collide.
        colCap = Math.max(0, Math.floor((h - 2 * stepY) / stepY) + 1);
        if (2 * rowCap + 2 * colCap >= n) { break; }
        h += stepY / 2;
    }
    const w = (rowCap - 1) * stepX;
    const caps = [rowCap, colCap, rowCap, colCap];
    const total = caps.reduce((s, c) => s + c, 0);
    // Share users between top, right, bottom and left in proportion to room (largest remainder).
    const exact = caps.map((c) => (c * n) / total);
    const counts = exact.map((x, i) => Math.min(caps[i], Math.floor(x)));
    let left = n - counts.reduce((s, c) => s + c, 0);
    const order = exact.map((x, i) => ({ i, r: x - Math.floor(x) })).sort((a, b) => b.r - a.r);
    while (left > 0) {
        let placed = false;
        for (const { i } of order) {
            if (left > 0 && counts[i] < caps[i]) { counts[i] += 1; left -= 1; placed = true; }
        }
        if (!placed) { break; }
    }
    const spread = (k: number, from: number, to: number): number[] => {
        if (k <= 0) { return []; }
        if (k === 1) { return [(from + to) / 2]; }
        return Array.from({ length: k }, (_, i) => from + ((to - from) * i) / (k - 1));
    };
    const top = -h / 2;
    const bottom = h / 2;
    const sideFrom = top + stepY;
    const sideTo = bottom - stepY;
    const centres: XY[] = [
        ...spread(counts[0], -w / 2, w / 2).map((x) => ({ x, y: top })),
        ...spread(counts[1], sideFrom, sideTo).map((y) => ({ x: w / 2, y })),
        ...spread(counts[2], w / 2, -w / 2).map((x) => ({ x, y: bottom })),
        ...spread(counts[3], sideTo, sideFrom).map((y) => ({ x: -w / 2, y })),
    ];
    ids.forEach((id, i) => {
        const c = centres[i] ?? { x: 0, y: 0 };
        pos[id] = { x: c.x - width / 2, y: c.y - height / 2 };
    });
    return pos;
}

/** A ring for a small group, the board-shaped perimeter for a big one. */
export function visibilityLayout(ids: string[], width: number, height: number, aspect = 1.75): Record<string, XY> {
    if (ids.length <= RING_MAX) { return autoLayout(ids, null, width, height); }
    return perimeterLayout(ids, width, height, aspect);
}

/** Centre of the cards' bounding box: connectors bow toward it, like a chord diagram. */
export function layoutCentre(positions: XY[], width: number, height: number): XY {
    if (positions.length === 0) { return { x: 0, y: 0 }; }
    const minX = Math.min(...positions.map((p) => p.x));
    const minY = Math.min(...positions.map((p) => p.y));
    const maxX = Math.max(...positions.map((p) => p.x + width));
    const maxY = Math.max(...positions.map((p) => p.y + height));
    return { x: (minX + maxX) / 2, y: (minY + maxY) / 2 };
}

/**
 * The area the connector layer has to cover: every card plus `pad` all round (room for
 * bowed curves, arrowheads and labels). Sizing the SVG to this, not a huge fixed canvas,
 * keeps pan, zoom and hover repaints cheap.
 */
export function contentBox(positions: XY[], width: number, height: number, pad: number): { x: number; y: number; w: number; h: number } {
    if (positions.length === 0) { return { x: -pad, y: -pad, w: 2 * pad, h: 2 * pad }; }
    const minX = Math.min(...positions.map((p) => p.x)) - pad;
    const minY = Math.min(...positions.map((p) => p.y)) - pad;
    const maxX = Math.max(...positions.map((p) => p.x)) + width + pad;
    const maxY = Math.max(...positions.map((p) => p.y)) + height + pad;
    return { x: Math.floor(minX), y: Math.floor(minY), w: Math.ceil(maxX - minX), h: Math.ceil(maxY - minY) };
}

export interface Curve { d: string; mid: XY; start: XY; end: XY }

/**
 * A curve between two cards that bows toward `centre`. A chord running along the
 * edge of the layout (both ends on the same side) bows further, so it arcs inward
 * over the cards between them instead of slipping underneath. `spread` offsets
 * parallel connectors between the same two users.
 */
export function visibilityCurve(a: XY, b: XY, centre: XY, width: number, height: number, gap = 5, spread = 0): Curve {
    const dist = Math.max(Math.hypot(b.x - a.x, b.y - a.y), 1);
    // Normal from the alphabetically-first end, so both directions agree on "+".
    let nx = -(b.y - a.y) / dist;
    let ny = (b.x - a.x) / dist;
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    if ((mx - centre.x) * nx + (my - centre.y) * ny > 0) { nx = -nx; ny = -ny; }
    const endDist = (Math.hypot(a.x - centre.x, a.y - centre.y) + Math.hypot(b.x - centre.x, b.y - centre.y)) / 2;
    const midDist = Math.hypot(mx - centre.x, my - centre.y);
    // Both ends in the same row or column, away from the middle: the straight
    // chord would run along that row, under the cards in between.
    const sameLine = Math.abs(b.y - a.y) < height / 2 || Math.abs(b.x - a.x) < width / 2;
    const alongEdge = sameLine && endDist > 0 && midDist > endDist * 0.45;
    const bow = (alongEdge ? Math.min(150, Math.max(40, dist * 0.22)) : Math.min(34, Math.max(10, dist * 0.09))) + spread;
    // A quadratic's apex sits halfway to its control point.
    const c = { x: mx + nx * bow * 2, y: my + ny * bow * 2 };
    const start = boxExit(a, c, width, height, gap);
    const end = boxExit(b, c, width, height, gap);
    const mid = { x: 0.25 * start.x + 0.5 * c.x + 0.25 * end.x, y: 0.25 * start.y + 0.5 * c.y + 0.25 * end.y };
    const r = (v: number) => Math.round(v * 10) / 10;
    return { d: `M${r(start.x)},${r(start.y)} Q${r(c.x)},${r(c.y)} ${r(end.x)},${r(end.y)}`, mid, start, end };
}

export interface LinkSummary { twoWay: number; oneWay: number; inherited: number; total: number }

/** How many shown connectors touch each user, by kind (a group-inherited one counts as inherited only). */
export function linkSummary(users: string[], edges: VisibilityEdgeLike[]): Record<string, LinkSummary> {
    const out: Record<string, LinkSummary> = {};
    users.forEach((u) => { out[u] = { twoWay: 0, oneWay: 0, inherited: 0, total: 0 }; });
    edges.forEach((e) => {
        if (e.source === e.target) { return; }
        [e.source, e.target].forEach((u) => {
            const s = out[u];
            if (!s) { return; }
            if (!e.manual) { s.inherited += 1; } else if (e.type === 'solid') { s.twoWay += 1; } else { s.oneWay += 1; }
            s.total += 1;
        });
    });
    return out;
}
