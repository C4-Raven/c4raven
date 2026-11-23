import { describe, expect, it } from 'vitest';
import {
    RING_MAX,
    contentBox,
    layoutCentre,
    linkSummary,
    perimeterLayout,
    visibilityCurve,
    visibilityLayout,
} from './userVisibilityMap';

const W = 172;
const H = 52;
const names = (n: number) => Array.from({ length: n }, (_, i) => `user${i + 1}`);

function overlaps(pos: Record<string, { x: number; y: number }>): boolean {
    const list = Object.values(pos);
    for (let i = 0; i < list.length; i++) {
        for (let j = i + 1; j < list.length; j++) {
            if (Math.abs(list[i].x - list[j].x) < W && Math.abs(list[i].y - list[j].y) < H) { return true; }
        }
    }
    return false;
}

describe('perimeterLayout', () => {
    it('places nobody for an empty list', () => {
        expect(perimeterLayout([], W, H)).toEqual({});
    });

    it.each([13, 17, 40, 80, 100])('places all %i users without overlapping cards', (n) => {
        const pos = perimeterLayout(names(n), W, H, 1.8);
        expect(Object.keys(pos)).toHaveLength(n);
        expect(overlaps(pos)).toBe(false);
    });

    it('keeps a big roster roughly the shape of the board', () => {
        const pos = Object.values(perimeterLayout(names(80), W, H, 1.8));
        const w = Math.max(...pos.map((p) => p.x)) - Math.min(...pos.map((p) => p.x)) + W;
        const h = Math.max(...pos.map((p) => p.y)) - Math.min(...pos.map((p) => p.y)) + H;
        expect(w / h).toBeGreaterThan(1.2);
        expect(w / h).toBeLessThan(2.6);
    });

    it('is centred on the origin', () => {
        const c = layoutCentre(Object.values(perimeterLayout(names(30), W, H)), W, H);
        expect(Math.abs(c.x)).toBeLessThan(1);
        expect(Math.abs(c.y)).toBeLessThan(1);
    });
});

describe('visibilityLayout', () => {
    it('uses a ring for a small group and the perimeter for a big one', () => {
        const small = visibilityLayout(names(RING_MAX), W, H);
        const big = visibilityLayout(names(RING_MAX + 1), W, H);
        expect(Object.keys(small)).toHaveLength(RING_MAX);
        expect(overlaps(small)).toBe(false);
        expect(big).toEqual(perimeterLayout(names(RING_MAX + 1), W, H));
    });

    it('centres a single user', () => {
        expect(visibilityLayout(['solo'], W, H)).toEqual({ solo: { x: -W / 2, y: -H / 2 } });
    });
});

describe('contentBox', () => {
    it('covers every card plus the padding', () => {
        const box = contentBox([{ x: 0, y: 0 }, { x: 300, y: 100 }], W, H, 50);
        expect(box).toEqual({ x: -50, y: -50, w: 300 + W + 100, h: 100 + H + 100 });
    });

    it('stays small when there are no cards', () => {
        expect(contentBox([], W, H, 40)).toEqual({ x: -40, y: -40, w: 80, h: 80 });
    });
});

describe('visibilityCurve', () => {
    it('ends just outside both cards', () => {
        const a = { x: 0, y: 0 };
        const b = { x: 400, y: 0 };
        const c = visibilityCurve(a, b, { x: 200, y: 300 }, W, H, 5);
        const onBorder = (p: { x: number; y: number }, o: { x: number; y: number }) => {
            const dx = Math.abs(p.x - o.x);
            const dy = Math.abs(p.y - o.y);
            return (Math.abs(dx - (W / 2 + 5)) < 0.5 && dy <= H / 2 + 5.5) || (Math.abs(dy - (H / 2 + 5)) < 0.5 && dx <= W / 2 + 5.5);
        };
        expect(onBorder(c.start, a)).toBe(true);
        expect(onBorder(c.end, b)).toBe(true);
        expect(c.d.startsWith('M')).toBe(true);
    });

    it('bows toward the centre', () => {
        const below = visibilityCurve({ x: 0, y: 0 }, { x: 400, y: 0 }, { x: 200, y: 300 }, W, H);
        const above = visibilityCurve({ x: 0, y: 0 }, { x: 400, y: 0 }, { x: 200, y: -300 }, W, H);
        expect(below.mid.y).toBeGreaterThan(0);
        expect(above.mid.y).toBeLessThan(0);
    });

    it('arcs further inward for a chord along the edge of the layout', () => {
        const centre = { x: 0, y: 0 };
        const edge = visibilityCurve({ x: -400, y: -300 }, { x: 400, y: -300 }, centre, W, H);
        const across = visibilityCurve({ x: -400, y: -300 }, { x: 400, y: 300 }, centre, W, H);
        const edgeBow = Math.abs(edge.mid.y - -300);
        const acrossBow = Math.hypot(across.mid.x, across.mid.y);
        expect(edgeBow).toBeGreaterThan(60);
        expect(acrossBow).toBeLessThan(40);
    });
});

describe('linkSummary', () => {
    it('counts each kind of connector per user', () => {
        const s = linkSummary(['a', 'b', 'c', 'd'], [
            { source: 'a', target: 'b', type: 'solid', manual: true },
            { source: 'a', target: 'c', type: 'dotted', manual: true },
            { source: 'b', target: 'c', type: 'solid', manual: false },
            { source: 'a', target: 'zz', type: 'solid', manual: true },
        ]);
        expect(s.a).toEqual({ twoWay: 2, oneWay: 1, inherited: 0, total: 3 });
        expect(s.b).toEqual({ twoWay: 1, oneWay: 0, inherited: 1, total: 2 });
        expect(s.c).toEqual({ twoWay: 0, oneWay: 1, inherited: 1, total: 2 });
        expect(s.d).toEqual({ twoWay: 0, oneWay: 0, inherited: 0, total: 0 });
    });
});
