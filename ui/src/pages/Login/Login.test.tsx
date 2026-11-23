import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router';
import { render, screen, userEvent, waitFor } from '@test-utils';

const posts: { url: string; body: unknown }[] = [];
let loginReply: Record<string, unknown> = {};

vi.mock('../../axios_config', () => ({
    default: {
        defaults: { headers: { common: {} } },
        get: vi.fn((url: string) => {
            if (url.startsWith('/api/login')) {
                // An older server may still list email as an identity attribute
                return Promise.resolve({
                    status: 200,
                    data: { response: { identity_attributes: ['username', 'email'], csrf_token: '' } },
                });
            }
            if (url === '/api/turnstile') {
                return Promise.resolve({ status: 200, data: { enabled: false } });
            }
            return Promise.reject(new Error(`unmocked ${url}`));
        }),
        post: vi.fn((url: string, body: unknown) => {
            posts.push({ url, body });
            return Promise.resolve({ status: 200, data: { response: loginReply } });
        }),
    },
}));

import Login from './Login';

const renderLogin = () =>
    render(
        <MemoryRouter>
            <Login />
        </MemoryRouter>,
    );

describe('Login', () => {
    beforeEach(() => {
        posts.length = 0;
        loginReply = {};
        localStorage.clear();
    });

    it('offers no sign-up, email reset or email field', async () => {
        renderLogin();
        expect(await screen.findByRole('button', { name: 'Login' })).toBeInTheDocument();
        await waitFor(() => expect(localStorage.getItem('emailEnabled')).toBeNull());
        expect(screen.queryByText(/register/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/create account/i)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /forgot password/i })).not.toBeInTheDocument();
        expect(screen.queryByLabelText(/email/i)).not.toBeInTheDocument();
        expect(screen.getByText('Forgot your password? Contact your administrator.')).toBeInTheDocument();
        expect(screen.getByLabelText(/username/i)).toBeInTheDocument();
    });

    it('asks for the authenticator code when the account uses one', async () => {
        loginReply = { tf_required: true, tf_method: 'authenticator' };
        renderLogin();
        await userEvent.type(screen.getByLabelText(/username/i), 'alice');
        await userEvent.type(screen.getByLabelText(/password/i, { selector: 'input' }), 'secret-pass{Enter}');
        expect(await screen.findByText('Please check your authenticator app for an auth code')).toBeInTheDocument();
        expect(screen.queryByText(/check your email/i)).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Submit' })).toBeInTheDocument();
        expect(posts[0].url).toMatch(/^\/api\/login/);
    });
});
