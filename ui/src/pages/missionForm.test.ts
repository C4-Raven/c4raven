import { describe, expect, it } from 'vitest';
import { missionRequestBody, type MissionForm } from './missionForm';

const FORM: MissionForm = {
    name: 'ops',
    description: 'desc',
    tool: 'public',
    default_role: 'MISSION_SUBSCRIBER',
    password: '',
    creator_uid: 'ANDROID-1',
    groups: ['3'],
    hashtags: null,
};

describe('missionRequestBody', () => {
    it('leaves the password out of an edit when the field is blank', () => {
        const body = missionRequestBody(FORM, true);
        expect(body).not.toHaveProperty('password');
        expect(body).toMatchObject({ name: 'ops', groups: ['3'], creator_uid: 'ANDROID-1' });
    });

    it('sends a newly typed password on edit', () => {
        expect(missionRequestBody({ ...FORM, password: 's3cret' }, true).password).toBe('s3cret');
    });

    it('sends the password field on create, even when blank', () => {
        expect(missionRequestBody(FORM, false).password).toBe('');
    });

    it('never sends hashtags', () => {
        expect(missionRequestBody({ ...FORM, hashtags: ['a'] }, true)).not.toHaveProperty('hashtags');
    });
});
