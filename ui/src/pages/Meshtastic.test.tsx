import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from 'i18next';
import { render, screen, userEvent, waitFor, within } from '@test-utils';

const CHANNEL: Channel = {
    name: 'Ops',
    psk: 'c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0MTI=',
    uplink_enabled: true,
    downlink_enabled: false,
    position_precision: 16,
    lora_region: 'EU_868',
    lora_hop_limit: 3,
    lora_tx_enabled: true,
    lora_tx_power: 27,
    lora_sx126x_rx_boosted_gain: false,
    modem_preset: 'LONG_FAST',
    url: 'https://meshtastic.org/e/#ops',
};
const OPEN: Channel = { ...CHANNEL, name: 'Public', psk: null, lora_region: 'UNSET', url: 'https://meshtastic.org/e/#public' };

let channels: Channel[] = [];
const api = vi.hoisted(() => ({
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
}));

vi.mock('../axios_config', () => ({ default: api }));
// The QR library draws on a canvas jsdom lacks
vi.mock('react-qrcode-logo', () => ({ QRCode: ({ value }: { value: string }) => <div data-testid="qr">{value}</div> }));

import Meshtastic, { type Channel } from './Meshtastic';
import { storeAdminAccess } from '../adminAccess';

i18n.init({ lng: 'en', resources: {}, interpolation: { escapeValue: false } });

function renderPage() {
    return render(<Meshtastic />);
}

describe('Meshtastic page', () => {
    beforeEach(() => {
        localStorage.clear();
        storeAdminAccess({ roles: [{ name: 'administrator' }] });
        channels = [CHANNEL, OPEN];
        api.get.mockReset().mockImplementation((url: string) =>
            url === '/api/meshtastic/channel'
                ? Promise.resolve({ status: 200, data: { results: channels, total: channels.length, current_page: 1 } })
                : Promise.reject(new Error(`unmocked ${url}`)));
        api.post.mockReset().mockResolvedValue({ status: 200, data: { success: true, url: 'https://meshtastic.org/e/#new' } });
        api.delete.mockReset().mockResolvedValue({ status: 200, data: { success: true } });
    });

    it('shows each channel with its settings and actions', async () => {
        renderPage();
        const cards = await screen.findAllByTestId('meshtastic-channel');
        expect(cards).toHaveLength(2);
        const ops = within(cards[0]);
        expect(ops.getByText('Ops')).toBeInTheDocument();
        expect(ops.getByText('EU_868', { selector: 'span' })).toBeInTheDocument();
        expect(ops.getByText('Long Fast')).toBeInTheDocument();
        expect(ops.getByText('Medium (about 360 m)')).toBeInTheDocument();
        expect(ops.getByText('On, 27 dBm')).toBeInTheDocument();
        // The key stays hidden until asked for
        expect(ops.queryByText(CHANNEL.psk!)).toBeNull();
        await userEvent.click(ops.getByRole('button', { name: 'Show key' }));
        expect(ops.getByText(CHANNEL.psk!)).toBeInTheDocument();
        for (const name of ['Show QR', 'Copy URL', 'Delete']) {
            expect(ops.getByRole('button', { name })).toBeEnabled();
        }
        const open = within(cards[1]);
        expect(open.getByText('No encryption')).toBeInTheDocument();
        expect(open.getByText('Region not set')).toBeInTheDocument();
    });

    it('shows the channel QR code and URL in a window', async () => {
        renderPage();
        const [ops] = await screen.findAllByTestId('meshtastic-channel');
        await userEvent.click(within(ops).getByRole('button', { name: 'Show QR' }));
        const dialog = await screen.findByRole('dialog');
        expect(within(dialog).getByTestId('qr')).toHaveTextContent(CHANNEL.url);
        expect(within(dialog).getByDisplayValue(CHANNEL.url)).toBeInTheDocument();
    });

    it('has an empty state with a way to create the first channel', async () => {
        channels = [];
        renderPage();
        expect(await screen.findByText('No channels yet')).toBeInTheDocument();
        // One in the header, one in the empty state
        expect(screen.getAllByRole('button', { name: 'Add New Channel' })).toHaveLength(2);
    });

    it('asks before deleting a channel', async () => {
        renderPage();
        const [ops] = await screen.findAllByTestId('meshtastic-channel');
        await userEvent.click(within(ops).getByRole('button', { name: 'Delete' }));
        const dialog = await screen.findByRole('dialog');
        expect(within(dialog).getByText('Delete channel Ops?')).toBeInTheDocument();
        expect(api.delete).not.toHaveBeenCalled();
        await userEvent.click(within(dialog).getByRole('button', { name: 'Delete' }));
        await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/meshtastic/channel', { params: { url: CHANNEL.url } }));
    });

    it('keeps the channel when the delete is cancelled', async () => {
        renderPage();
        const [ops] = await screen.findAllByTestId('meshtastic-channel');
        await userEvent.click(within(ops).getByRole('button', { name: 'Delete' }));
        await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }));
        expect(api.delete).not.toHaveBeenCalled();
    });

    it('switches off every write action and hides keys for a read-only admin', async () => {
        localStorage.clear();
        storeAdminAccess({ roles: [{ name: 'admin_viewer' }], admin_view_areas: ['meshtastic'] });
        // The server blanks the key and the share URL (which holds the key) for read-only admins
        channels = [CHANNEL, OPEN].map((c) => ({ ...c, psk: null, url: '' }));
        renderPage();
        const cards = await screen.findAllByTestId('meshtastic-channel');
        expect(screen.getByText(/You have read-only access/)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Add New Channel' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Import from URL' })).toBeDisabled();
        for (const card of cards) {
            expect(within(card).getByRole('button', { name: 'Delete' })).toBeDisabled();
            // No key or URL to share, and no misleading "No encryption" badge
            expect(within(card).getByText('Hidden')).toBeInTheDocument();
            expect(within(card).queryByText('No encryption')).not.toBeInTheDocument();
            expect(within(card).getByRole('button', { name: 'Show QR' })).toBeDisabled();
            expect(within(card).getByRole('button', { name: 'Copy URL' })).toBeDisabled();
        }
    });

    it('checks the name before sending a new channel', async () => {
        renderPage();
        await screen.findAllByTestId('meshtastic-channel');
        await userEvent.click(screen.getByRole('button', { name: 'Add New Channel' }));
        const dialog = await screen.findByRole('dialog');
        await userEvent.type(within(dialog).getByLabelText('Name'), 'ThisNameIsTooLong');
        await userEvent.click(within(dialog).getByRole('button', { name: 'Add channel' }));
        expect(within(dialog).getByText('Meshtastic allows at most 11 characters')).toBeInTheDocument();
        expect(api.post).not.toHaveBeenCalled();
    });

    it('shows the server\'s error inside the new channel window', async () => {
        api.post.mockRejectedValue({ response: { status: 400, data: { success: false, error: 'Incorrect padding' } } });
        renderPage();
        await screen.findAllByTestId('meshtastic-channel');
        await userEvent.click(screen.getByRole('button', { name: 'Add New Channel' }));
        const dialog = await screen.findByRole('dialog');
        await userEvent.type(within(dialog).getByLabelText('Name'), 'Squad');
        await userEvent.click(within(dialog).getByRole('button', { name: 'Add channel' }));
        expect(await within(dialog).findByText('Incorrect padding')).toBeInTheDocument();
        expect(api.post).toHaveBeenCalledWith('/api/meshtastic/channel', expect.objectContaining({ name: 'Squad', url: '' }));
    });

    it('shows an import error under the URL field', async () => {
        api.post.mockRejectedValue({ response: { status: 400, data: { success: false, error: 'Error parsing message' } } });
        renderPage();
        await screen.findAllByTestId('meshtastic-channel');
        await userEvent.click(screen.getByRole('button', { name: 'Import from URL' }));
        const dialog = await screen.findByRole('dialog');
        await userEvent.type(within(dialog).getByLabelText('Channel URL'), 'https://meshtastic.org/e/#bad');
        await userEvent.click(within(dialog).getByRole('button', { name: 'Import' }));
        expect(await within(dialog).findByText('Error parsing message')).toBeInTheDocument();
        expect(api.post).toHaveBeenCalledWith('/api/meshtastic/channel', { url: 'https://meshtastic.org/e/#bad' });
    });

    it('shows the off notice if the server turned Meshtastic off', async () => {
        api.get.mockRejectedValue({ response: { status: 404, data: { error: 'Meshtastic is turned off on this server' } } });
        renderPage();
        expect(await screen.findByText('Meshtastic is turned off on this server')).toBeInTheDocument();
    });
});
