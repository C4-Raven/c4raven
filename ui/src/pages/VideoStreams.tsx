import { useEffect, useRef, useState } from 'react';
import {
    ActionIcon, AspectRatio, Badge, Box, Button, CopyButton, Group, Image, LoadingOverlay, Menu, Modal, Pagination,
    SegmentedControl, Select, SimpleGrid, Stack, Switch, Text, TextInput, Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useTranslation } from 'react-i18next';
import {
    Add20Regular, Checkmark20Regular, Delete20Regular, Link20Regular, Play24Filled, Tv24Regular,
    VideoOff20Regular,
} from '@fluentui/react-icons';
import axios from '@/axios_config';
import { apiRoutes } from '@/apiRoutes';
import { PageHeader, Panel } from '@/components/ui';
import { EmptyState, ICON_16, PulseDot } from './ServerRequests/shared';
import { errorText, flattenedErrors, NO_ESCAPE, notifyFailure, notifySuccess } from './pageUtils';

interface VideoStream {
    path: string;
    thumbnail: string | null;
    username: string | null;
    rtsp_link: string;
    webrtc_link: string;
    hls_link: string;
    source: string | null;
    ready: boolean;
    record: boolean;
}

type PlayerMode = 'webrtc' | 'hls';

const REFRESH_MS = 5000;
const PAGE_SIZES = [8, 12, 16, 24, 32];

function token(): string {
    try {
        return localStorage.getItem('token') ?? '';
    } catch {
        return '';
    }
}

function StatusBadgeDot({ live }: { live: boolean }) {
    return (
        <span
            aria-hidden
            style={{
                display: 'inline-block',
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: 'currentColor',
                animation: live ? 'raven-pulse 1.5s ease-in-out infinite' : undefined,
            }}
        />
    );
}

export default function VideoStreams() {
    const { t } = useTranslation();

    const [streams, setStreams] = useState<VideoStream[]>([]);
    const [total, setTotal] = useState(0);
    const [totalPages, setTotalPages] = useState(1);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(12);
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);

    const [watching, setWatching] = useState<VideoStream | null>(null);
    const [mode, setMode] = useState<PlayerMode>('webrtc');
    const [thumbnail, setThumbnail] = useState<string | null>(null);
    const [deleteStream, setDeleteStream] = useState<VideoStream | null>(null);

    const [addOpen, setAddOpen] = useState(false);
    const [newPath, setNewPath] = useState('');
    const [newSource, setNewSource] = useState<string | null>(null);

    // The interval callback must see the current page and size
    const params = useRef({ page, pageSize });
    params.current = { page, pageSize };

    function getStreams(silent = false) {
        if (!silent) { setLoading(true); }
        axios.get(apiRoutes.videoStreamList, {
            params: {
                page: params.current.page,
                per_page: params.current.pageSize,
                sort_by: 'path',
                sort_direction: 'asc',
            },
        }).then((r) => {
            if (r.status === 200) {
                setStreams(r.data.results ?? []);
                setTotal(r.data.total ?? 0);
                setTotalPages(r.data.total_pages ?? 1);
                setPage(r.data.current_page ?? 1);
            }
        }).catch((err) => {
            if (!silent) { notifyFailure(t('Couldn\'t load streams'), errorText(err)); }
        }).finally(() => {
            if (!silent) { setLoading(false); }
        });
    }

    useEffect(() => {
        getStreams();
    }, [page, pageSize]);

    useEffect(() => {
        const timer = window.setInterval(() => {
            if (document.visibilityState === 'visible') { getStreams(true); }
        }, REFRESH_MS);
        return () => window.clearInterval(timer);
    }, []);

    function setRecord(stream: VideoStream, record: boolean) {
        setBusy(true);
        axios.patch(apiRoutes.videoStreamUpdate, { path: stream.path, record, sourceOnDemand: !record })
            .then((r) => {
                if (r.status === 200) {
                    notifications.show({
                        message: record ? `${stream.path} is now recording` : `${stream.path} is no longer recording`,
                        color: record ? 'green' : 'red',
                    });
                    getStreams();
                }
            })
            .catch((err) => notifyFailure(t('Recording Failed'), flattenedErrors(err)))
            .finally(() => setBusy(false));
    }

    function addStream(e: React.FormEvent) {
        e.preventDefault();
        setBusy(true);
        axios.post(apiRoutes.videoStreamAdd, { path: newPath, source: newSource, sourceOnDemand: true })
            .then((r) => {
                if (r.status === 200) {
                    setAddOpen(false);
                    setNewPath('');
                    setNewSource(null);
                    getStreams();
                }
            })
            .catch((err) => notifyFailure(t('Couldn\'t add the stream'), flattenedErrors(err)))
            .finally(() => setBusy(false));
    }

    function confirmDelete() {
        const stream = deleteStream;
        setDeleteStream(null);
        if (!stream) { return; }
        setBusy(true);
        axios.delete(apiRoutes.videoStreamDelete, { params: { path: stream.path } })
            .then(() => {
                notifySuccess(t('Stream deleted'));
                if (watching?.path === stream.path) { setWatching(null); }
                getStreams();
            })
            .catch((err) => notifyFailure(t('Couldn\'t delete the stream'), errorText(err)))
            .finally(() => setBusy(false));
    }

    function watch(stream: VideoStream) {
        setMode('webrtc');
        setWatching(stream);
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function openAdd() {
        setNewPath('');
        setNewSource(null);
        setAddOpen(true);
    }

    // The player's live dot follows the latest poll of the stream being watched
    const watchedNow = watching ? (streams.find((s) => s.path === watching.path) ?? watching) : null;
    const playerSrc = watchedNow
        ? `${mode === 'webrtc' ? watchedNow.webrtc_link : watchedNow.hls_link}?jwt=${token()}`
        : '';

    const copyItems = (s: VideoStream) => [
        { name: 'RTSP Link', value: s.rtsp_link },
        { name: 'WebRTC Link', value: s.webrtc_link },
        { name: 'HLS Link', value: `${s.hls_link}?jwt=${token()}` },
    ];

    return (
        <>
            <LoadingOverlay visible={loading || busy} pos="fixed" zIndex={1000} />
            <PageHeader
                title={t('Streaming')}
                description={t('Live and recorded video streams available on this server')}
                actions={<Button leftSection={<Add20Regular />} onClick={openAdd}>{t('Add Video')}</Button>}
            />

            {watchedNow && (
                <Panel
                    mb="lg"
                    title={(
                        <Group gap="xs" wrap="nowrap" component="span">
                            <PulseDot active={watchedNow.ready} />
                            <span>{watchedNow.path}</span>
                        </Group>
                    )}
                    actions={(
                        <>
                            <SegmentedControl
                                size="xs"
                                value={mode}
                                onChange={(v) => setMode(v as PlayerMode)}
                                data={[
                                    { value: 'webrtc', label: t('Low Latency') },
                                    { value: 'hls', label: t('Reliable') },
                                ]}
                            />
                            <Button size="xs" variant="default" onClick={() => setWatching(null)}>
                                {t('Close player')}
                            </Button>
                        </>
                    )}
                >
                    <AspectRatio ratio={16 / 9}>
                        <iframe
                            key={`${watchedNow.path}-${mode}`}
                            title={watchedNow.path}
                            src={playerSrc}
                            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                            allowFullScreen
                            style={{ border: 0, background: '#000', borderRadius: 'var(--mantine-radius-sm)' }}
                        />
                    </AspectRatio>
                </Panel>
            )}

            {streams.length === 0 && !loading && (
                <Panel>
                    <EmptyState
                        icon={<VideoOff20Regular />}
                        title={t('No video streams yet')}
                        description={t('Streams appear here when a camera, drone or device starts publishing to this server, or when you add one.')}
                        action={(
                            <Button variant="default" leftSection={<Add20Regular />} onClick={openAdd}>
                                {t('Add Video')}
                            </Button>
                        )}
                    />
                </Panel>
            )}

            <SimpleGrid cols={{ base: 1, sm: 2, lg: 3, xl: 4 }} spacing="md">
                {streams.map((s) => (
                    <Panel key={s.path} p={0}>
                        <AspectRatio ratio={16 / 9}>
                            <Box
                                pos="relative"
                                bg="var(--raven-chrome)"
                                onClick={() => s.thumbnail && setThumbnail(s.thumbnail)}
                                style={{ cursor: s.thumbnail ? 'zoom-in' : 'default', overflow: 'hidden' }}
                            >
                                {s.thumbnail
                                    ? <Image src={s.thumbnail} alt={s.path} fit="cover" h="100%" w="100%" />
                                    : (
                                        <Box
                                            pos="absolute"
                                            inset={0}
                                            style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: 0.35 }}
                                        >
                                            <Tv24Regular style={{ width: 36, height: 36 }} />
                                        </Box>
                                    )}
                                <Group gap={4} pos="absolute" top={8} left={8}>
                                    {s.ready
                                        ? (
                                            <Badge size="sm" variant="filled" color="green.9" leftSection={<StatusBadgeDot live />}>
                                                {t('Live')}
                                            </Badge>
                                        )
                                        : (
                                            <Badge size="sm" variant="filled" color="dark.5" leftSection={<StatusBadgeDot live={false} />}>
                                                {t('Offline')}
                                            </Badge>
                                        )}
                                    {s.record && <Badge size="sm" variant="filled" color="red">{t('REC')}</Badge>}
                                </Group>
                                <Box
                                    pos="absolute"
                                    inset={0}
                                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none' }}
                                >
                                    <Tooltip label={t('Watch')}>
                                        <ActionIcon
                                            size="xl"
                                            radius="xl"
                                            variant="filled"
                                            aria-label={t('Watch')}
                                            style={{ pointerEvents: 'auto' }}
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                watch(s);
                                            }}
                                        >
                                            <Play24Filled />
                                        </ActionIcon>
                                    </Tooltip>
                                </Box>
                            </Box>
                        </AspectRatio>
                        <Box px="md" py="sm" style={{ borderTop: '1px solid var(--raven-border)' }}>
                            <Tooltip label={s.path}>
                                <Text fw={600} size="sm" truncate>{s.path}</Text>
                            </Tooltip>
                            <Text size="xs" c="dimmed" truncate>
                                {s.username ?? ''} · {s.source || t('No source')}
                            </Text>
                            <Group justify="space-between" mt="xs" wrap="nowrap">
                                <Tooltip label={s.record ? t('Recording') : t('Not recording')}>
                                    <span style={{ display: 'inline-flex' }}>
                                        <Switch
                                            size="sm"
                                            label={t('Record')}
                                            checked={s.record}
                                            onChange={(e) => setRecord(s, e.currentTarget.checked)}
                                        />
                                    </span>
                                </Tooltip>
                                <Group gap={4} wrap="nowrap">
                                    <Menu position="bottom-end" withinPortal>
                                        <Menu.Target>
                                            <ActionIcon
                                                variant="subtle"
                                                color="gray"
                                                title={t('Copy stream link')}
                                                aria-label={t('Copy stream link')}
                                            >
                                                <Link20Regular style={ICON_16} />
                                            </ActionIcon>
                                        </Menu.Target>
                                        <Menu.Dropdown>
                                            <Menu.Label>{t('Copy Link')}</Menu.Label>
                                            {copyItems(s).map((item) => (
                                                <CopyButton key={item.name} value={item.value}>
                                                    {({ copied, copy }) => (
                                                        <Menu.Item
                                                            closeMenuOnClick={false}
                                                            onClick={copy}
                                                            color={copied ? 'teal' : undefined}
                                                            leftSection={copied
                                                                ? <Checkmark20Regular style={ICON_16} />
                                                                : <Link20Regular style={ICON_16} />}
                                                        >
                                                            {copied ? t(`Copied ${item.name}`) : t(item.name)}
                                                        </Menu.Item>
                                                    )}
                                                </CopyButton>
                                            ))}
                                        </Menu.Dropdown>
                                    </Menu>
                                    <Tooltip label={t('Delete')}>
                                        <ActionIcon
                                            variant="subtle"
                                            color="red"
                                            aria-label={t('Delete')}
                                            onClick={() => setDeleteStream(s)}
                                        >
                                            <Delete20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                </Group>
                            </Group>
                        </Box>
                    </Panel>
                ))}
            </SimpleGrid>

            {streams.length > 0 && (
                <Group justify="space-between" mt="lg">
                    <Group gap="xs">
                        <Select
                            size="xs"
                            w={140}
                            aria-label={t('Streams per page')}
                            allowDeselect={false}
                            value={String(pageSize)}
                            data={PAGE_SIZES.map((n) => ({ value: String(n), label: t(`${n} per page`) }))}
                            onChange={(v) => {
                                if (!v) { return; }
                                setPage(1);
                                setPageSize(Number(v));
                            }}
                        />
                        <Text size="xs" c="dimmed">{t(`${total} stream(s)`)}</Text>
                    </Group>
                    <Pagination size="sm" total={totalPages} value={page} onChange={setPage} />
                </Group>
            )}

            {/* Add */}
            <Modal opened={addOpen} onClose={() => setAddOpen(false)} title={t('Add Video')}>
                <form onSubmit={addStream}>
                    <Stack gap="sm">
                        <Text size="sm" c="dimmed">
                            {t('Add a stream path, and optionally a source URL such as an RTSP camera to pull from.')}
                        </Text>
                        <TextInput
                            required
                            label={t('Path')}
                            value={newPath}
                            onChange={(e) => setNewPath(e.currentTarget.value)}
                        />
                        <TextInput
                            label={t('Source')}
                            placeholder="rtsp://"
                            value={newSource ?? ''}
                            onChange={(e) => setNewSource(e.currentTarget.value)}
                        />
                        <Group justify="flex-end" mt="sm">
                            <Button variant="default" onClick={() => setAddOpen(false)}>{t('Cancel')}</Button>
                            <Button type="submit" leftSection={<Add20Regular />}>{t('Add Video Stream')}</Button>
                        </Group>
                    </Stack>
                </form>
            </Modal>

            {/* Delete */}
            <Modal opened={deleteStream !== null} onClose={() => setDeleteStream(null)} title={t('Are you sure?')}>
                <Text size="sm">
                    {deleteStream ? t("Delete {{path}}? This can't be undone.", { path: deleteStream.path, ...NO_ESCAPE }) : ''}
                </Text>
                <Group justify="flex-end" mt="md">
                    <Button variant="default" onClick={() => setDeleteStream(null)}>{t('Cancel')}</Button>
                    <Button color="red" leftSection={<Delete20Regular />} onClick={confirmDelete}>{t('Delete')}</Button>
                </Group>
            </Modal>

            {/* Thumbnail */}
            <Modal size="xl" opened={thumbnail !== null} onClose={() => setThumbnail(null)} title={t('Thumbnail')}>
                {thumbnail && <Image src={thumbnail} alt={t('Thumbnail')} />}
            </Modal>
        </>
    );
}

