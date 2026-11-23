import { describe, expect, it } from 'vitest';
import {
    autoLayout,
    boxExit,
    cleanName,
    commonNameFromUid,
    edgeCurve,
    entityMeta,
    entityName,
    fitView,
    looksLikeAddress,
    looksLikeFingerprint,
    pairEdges,
    partnerAliases,
} from './federationMap';

const SHA = '57cd78a685b5657fc2dd53f6b51d8b03c6eeeb03a886ee05f27ab389f4984e3e';
const UID = `CN=County TAK CA,OU=Federation,O=Demo,C=US-${SHA}`;

describe('names on the map', () => {
    it('recognises fingerprints in their usual spellings', () => {
        expect(looksLikeFingerprint('57:CD:78:A6…F4:98:4E:3E')).toBe(true);
        expect(looksLikeFingerprint(SHA)).toBe(true);
        expect(looksLikeFingerprint('57:CD:78:A6:85:B5')).toBe(true);
        expect(looksLikeFingerprint('County TAK')).toBe(false);
        expect(looksLikeFingerprint('Cafe')).toBe(false);
    });

    it('recognises addresses and URLs', () => {
        expect(looksLikeAddress('198.51.100.7')).toBe(true);
        expect(looksLikeAddress('198.51.100.7:9102')).toBe(true);
        expect(looksLikeAddress('tak.example:9001')).toBe(true);
        expect(looksLikeAddress('https://hub.example')).toBe(true);
        expect(looksLikeAddress('2001:db8::1')).toBe(true);
        expect(looksLikeAddress('Raven North')).toBe(false);
    });

    it('takes the common name from a CA uid', () => {
        expect(commonNameFromUid(UID)).toBe('County TAK CA');
        expect(commonNameFromUid('')).toBe('');
    });

    it('never returns a hash, address or uuid suffix', () => {
        expect(cleanName('57:CD:78:A6…F4:98:4E:3E')).toBe('');
        expect(cleanName('tak.example:9102')).toBe('');
        expect(cleanName(UID)).toBe('County TAK CA');
        expect(cleanName('County TAK_5f0c2a8e-1b7d-4c3e-9a60-2d8f4b1e7c90')).toBe('County TAK');
        expect(cleanName('  Raven North ')).toBe('Raven North');
    });

    it('prefers the given label, then displayName, then the CA common name', () => {
        const fpNode = { id: 'n', type: 'FederationGroup', name: UID, displayName: '57:CD:78:A6…F4:98:4E:3E' };
        expect(entityName(fpNode)).toBe('County TAK CA');
        expect(entityName(fpNode, 'County')).toBe('County');
        expect(entityName({ id: 'x', type: 'FederationOutgoing', displayName: '', name: '198.51.100.7' })).toBe('');
    });

    it('reads Raven metadata from a description', () => {
        expect(entityMeta({ id: 'a', type: 'FederationOutgoing', description: JSON.stringify({ raven: { kind: 'raven_hub', ca: UID } }) }))
            .toEqual({ kind: 'raven_hub', ca: UID });
        expect(entityMeta({ id: 'a', type: 'FederationGroup', description: 'typed by hand' })).toEqual({});
        expect(entityMeta({ id: 'a', type: 'FederationGroup', description: JSON.stringify({ raven: { kind: 'bogus' } }) })).toEqual({ kind: undefined, ca: undefined });
    });
});

describe('edges', () => {
    const entities = [
        { id: 'own', type: 'FederationGroup', name: 'own-uid' },
        { id: 'ca', type: 'FederationGroup', name: UID },
        { id: 'out', type: 'FederationOutgoing', name: 'County_x', description: JSON.stringify({ raven: { ca: UID } }) },
        { id: 'lone', type: 'FederationOutgoing', name: 'Other_y' },
    ];

    it('folds a partner outgoing node into its CA node', () => {
        expect(partnerAliases(entities)).toEqual({ out: 'ca' });
    });

    it('pairs rules both ways into one edge and skips loops and unknown nodes', () => {
        const rules = [
            { id: '1', source: 'own', destination: 'ca' },
            { id: '2', source: 'ca', destination: 'own' },
            { id: '3', source: 'own', destination: 'out' },
            { id: '4', source: 'out', destination: 'ca' },
            { id: '5', source: 'own', destination: 'gone' },
            { id: '6', source: 'lone', destination: 'own' },
        ];
        const edges = pairEdges(rules, new Set(['own', 'ca', 'lone']), { out: 'ca' });
        expect(edges).toHaveLength(2);
        const main = edges.find((e) => e.key === 'ca|own')!;
        expect(main.ba.map((r) => r.id)).toEqual(['1', '3']);
        expect(main.ab.map((r) => r.id)).toEqual(['2']);
        const lone = edges.find((e) => e.key === 'lone|own')!;
        expect(lone.ab.map((r) => r.id)).toEqual(['6']);
        expect(lone.ba).toEqual([]);
    });
});

describe('layout', () => {
    const W = 188;
    const H = 58;
    const overlaps = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.abs(a.x - b.x) < W && Math.abs(a.y - b.y) < H;

    it('puts this server in the middle and partners around it without overlaps', () => {
        for (let n = 1; n <= 14; n += 1) {
            const ids = ['own', ...Array.from({ length: n }, (_, i) => `p${i}`)];
            const pos = autoLayout(ids, 'own', W, H);
            expect(pos.own).toEqual({ x: -W / 2, y: -H / 2 });
            const all = Object.values(pos);
            for (let i = 0; i < all.length; i += 1) {
                for (let j = i + 1; j < all.length; j += 1) {
                    expect(overlaps(all[i], all[j])).toBe(false);
                }
            }
        }
    });

    it('places a single partner level with this server', () => {
        const pos = autoLayout(['own', 'p'], 'own', W, H);
        expect(pos.p.y).toBeCloseTo(pos.own.y);
        expect(pos.p.x).toBeGreaterThan(pos.own.x + W);
    });

    it('centres a lone node when there is no own node', () => {
        expect(autoLayout(['a'], null, W, H)).toEqual({ a: { x: -W / 2, y: -H / 2 } });
        expect(autoLayout([], null, W, H)).toEqual({});
    });

    it('fits the cards in the board without zooming past the limit', () => {
        const board = { width: 1000, height: 400 };
        const insets = { top: 20, right: 20, bottom: 20, left: 20 };
        const small = fitView([{ x: 0, y: 0 }], W, H, board, insets, { min: 0.3, max: 1 });
        expect(small.zoom).toBe(1);
        expect(small.pan.x + (W / 2) * small.zoom).toBeCloseTo(500);
        const wide = fitView([{ x: 0, y: 0 }, { x: 3000, y: 0 }], W, H, board, insets, { min: 0.3, max: 1 });
        expect(wide.zoom).toBeLessThan(1);
        expect(Math.abs(wide.pan.x - 20)).toBeLessThan(5);
    });

    it('leaves a card on the side facing the other end', () => {
        expect(boxExit({ x: 0, y: 0 }, { x: 100, y: 0 }, 40, 20)).toEqual({ x: 20, y: 0 });
        expect(boxExit({ x: 0, y: 0 }, { x: 0, y: -100 }, 40, 20, 5)).toEqual({ x: 0, y: -15 });
        const curve = edgeCurve({ x: 0, y: 0 }, { x: 400, y: 0 }, W, H);
        expect(curve.d.startsWith('M')).toBe(true);
        expect(curve.start.x).toBeGreaterThan(W / 2);
        expect(curve.end.x).toBeLessThan(400 - W / 2);
    });
});
