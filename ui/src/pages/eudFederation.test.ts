import { describe, expect, it } from 'vitest';
import { federatedFromLabel, shortFederationId } from './eudFederation';

const ID = '622713fa68fa49d8a240dc3fe9d65545-ODmwo4kG';

describe('federatedFromLabel', () => {
    it('uses the partner name when the server knows it', () => {
        expect(federatedFromLabel(ID, 'Brisbane HQ')).toBe('Brisbane HQ');
    });

    it('falls back to a shortened id', () => {
        expect(federatedFromLabel(ID, null)).toBe('622713fa…');
        expect(federatedFromLabel(ID, undefined)).toBe('622713fa…');
        expect(federatedFromLabel(ID, '  ')).toBe('622713fa…');
    });

    it('leaves short ids alone', () => {
        expect(shortFederationId('peer-1')).toBe('peer-1');
        expect(shortFederationId('unknown-federate')).toBe('unknown-…');
    });
});
