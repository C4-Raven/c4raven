import { describe, expect, it } from 'vitest';
import {
    clampScale,
    DEFAULT_SETTINGS,
    loadSettings,
    markerCategory,
    roleAbbreviation,
    saveSettings,
    teamIconSvg,
} from './mapDisplay';

describe('markerCategory', () => {
    it('groups affiliations the way TAK colours them', () => {
        expect(markerCategory({ affiliation: 'friendly' })).toBe('friendly');
        expect(markerCategory({ affiliation: 'assumed' })).toBe('friendly');
        expect(markerCategory({ affiliation: 'hostile' })).toBe('hostile');
        expect(markerCategory({ affiliation: 'suspect' })).toBe('hostile');
        expect(markerCategory({ affiliation: 'joker' })).toBe('hostile');
        expect(markerCategory({ affiliation: 'neutral' })).toBe('neutral');
        expect(markerCategory({ affiliation: 'pending' })).toBe('unknown');
        expect(markerCategory({ affiliation: 'unknown' })).toBe('unknown');
    });

    it('falls back to the CoT type, then to other', () => {
        expect(markerCategory({ affiliation: null, type: 'a-h-G-E-V' })).toBe('hostile');
        expect(markerCategory({ affiliation: null, type: 'b-m-p-s-m' })).toBe('other');
        expect(markerCategory({})).toBe('other');
    });
});

describe('roleAbbreviation', () => {
    it('matches ATAK', () => {
        expect(roleAbbreviation('Team Member')).toBe('');
        expect(roleAbbreviation('Team Lead')).toBe('TL');
        expect(roleAbbreviation('HQ')).toBe('HQ');
        expect(roleAbbreviation('Forward Observer')).toBe('FO');
        expect(roleAbbreviation('K9')).toBe('K9');
        expect(roleAbbreviation(null)).toBe('');
    });
});

describe('settings', () => {
    it('defaults when nothing or junk is stored', () => {
        expect(loadSettings({ getItem: () => null })).toEqual(DEFAULT_SETTINGS);
        expect(loadSettings({ getItem: () => '{not json' })).toEqual(DEFAULT_SETTINGS);
        expect(loadSettings({ getItem: () => { throw new Error('blocked'); } })).toEqual(DEFAULT_SETTINGS);
    });

    it('round-trips and clamps', () => {
        let stored = '';
        const settings = { ...DEFAULT_SETTINGS, iconScale: 9, labels: false, show: { ...DEFAULT_SETTINGS.show, hostile: false } };
        saveSettings(settings, { setItem: (_k, v) => { stored = v; } });
        const loaded = loadSettings({ getItem: () => stored });
        expect(loaded.iconScale).toBe(1.5);
        expect(loaded.labels).toBe(false);
        expect(loaded.show.hostile).toBe(false);
        expect(loaded.show.friendly).toBe(true);
    });

    it('clampScale keeps the slider range', () => {
        expect(clampScale(0.1)).toBe(0.5);
        expect(clampScale(1.2)).toBe(1.2);
    });
});

describe('teamIconSvg', () => {
    it('draws the team colour, role and a pointer only while moving', () => {
        const moving = teamIconSvg({ color: '#00ffff', role: 'Team Lead', online: true, course: 90, speed: 2, scale: 1 });
        expect(moving.svg).toContain('fill="#00ffff"');
        expect(moving.svg).toContain('>TL<');
        expect(moving.svg).toContain('rotate(90 16 16)');

        const still = teamIconSvg({ color: '#00ffff', role: 'Team Member', online: true, course: 90, speed: 0, scale: 1 });
        expect(still.svg).not.toContain('rotate(');
        expect(still.svg).not.toContain('<text');
    });

    it('greys out offline users and scales', () => {
        const offline = teamIconSvg({ color: '#ff0000', online: false, scale: 1 });
        expect(offline.svg).toContain('#9e9e9e');
        expect(offline.svg).not.toContain('#ff0000');
        expect(teamIconSvg({ online: true, scale: 1.5 }).size).toBeGreaterThan(teamIconSvg({ online: true, scale: 0.5 }).size);
    });

    it('escapes nothing it does not need to and never injects markup from the role', () => {
        expect(teamIconSvg({ online: true, role: '<script>', scale: 1 }).svg).not.toContain('<script>');
    });
});
