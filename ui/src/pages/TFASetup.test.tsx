import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from 'i18next';
import { render, screen, userEvent } from '@test-utils';

const posts: { url: string; body: unknown }[] = [];
let method: string | null = null;

vi.mock('../axios_config', () => ({
    default: {
        get: vi.fn(() => Promise.resolve({ status: 200, data: { response: { tf_primary_method: method } } })),
        post: vi.fn((url: string, body: unknown) => {
            posts.push({ url, body });
            return Promise.resolve({ status: 200, data: { response: {} } });
        }),
    },
}));

import TFASetup from './TFASetup';

i18n.init({ lng: 'en', resources: {}, interpolation: { escapeValue: false } });

describe('TFASetup', () => {
    beforeEach(() => {
        posts.length = 0;
        method = null;
    });

    it('offers the authenticator app and disable only', async () => {
        render(<TFASetup />);
        const radios = await screen.findAllByRole('radio');
        expect(radios).toHaveLength(2);
        expect(screen.getByLabelText(/authenticator app/i)).toBeInTheDocument();
        expect(screen.getByLabelText(/disable two factor/i)).toBeInTheDocument();
        expect(screen.queryByText(/email/i)).not.toBeInTheDocument();
    });

    it('shows the current authenticator method as selected', async () => {
        method = 'authenticator';
        render(<TFASetup />);
        expect(await screen.findByText('authenticator')).toBeInTheDocument();
        expect(screen.getByLabelText(/authenticator app/i)).toBeChecked();
    });

    it('disables two-factor through tf-setup', async () => {
        method = 'authenticator';
        render(<TFASetup />);
        await screen.findByText('authenticator');
        await userEvent.click(screen.getByLabelText(/disable two factor/i));
        expect(posts).toEqual([{ url: '/api/tf-setup', body: { setup: 'disable' } }]);
    });
});
