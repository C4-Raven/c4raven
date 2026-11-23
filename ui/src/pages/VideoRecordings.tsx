import { useEffect, useRef, useState } from 'react';
import {
    ActionIcon, AspectRatio, Badge, Button, Group, Image, LoadingOverlay, Modal, Stack, Text, Tooltip, UnstyledButton,
} from '@mantine/core';
import { DataTable, type DataTableSortStatus } from 'mantine-datatable';
import { useTranslation } from 'react-i18next';
import { formatDuration, intervalToDuration } from 'date-fns';
import {
    ArrowDownload20Regular, Delete20Regular, Dismiss20Regular, Play20Regular, VideoClip20Regular,
} from '@fluentui/react-icons';
import axios from '@/axios_config';
import { apiRoutes } from '@/apiRoutes';
import { formatBitRate, formatBytes } from '@/bytes_formatter';
import { PageHeader, Panel } from '@/components/ui';
import { EmptyState, formatDateTime, ICON_16 } from './ServerRequests/shared';
import { errorText, notifyFailure, notifySuccess, DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS, PANEL_TABLE } from './pageUtils';

interface Recording {
    id: number;
    thumbnail: string | null;
    path: string;
    start_time: string | null;
    stop_time: string | null;
    in_progress: boolean;
    duration: number | null;
    filename: string;
    width: number | null;
    height: number | null;
    video_codec: string | null;
    video_bitrate: number | null;
    audio_codec: string | null;
    audio_bitrate: number | null;
    audio_samplerate: number | null;
    audio_channels: number | null;
    file_size: number | null;
    formatted_duration?: string | null;
}

function durationText(r: Recording): string {
    if (r.duration) {
        const text = formatDuration(intervalToDuration({ start: 0, end: Math.round(r.duration * 1000) }));
        if (text) { return text; }
    }
    return r.formatted_duration || '';
}

function codecCell(codec: string | null, bitrate: number | null) {
    if (!codec) { return '—'; }
    return (
        <Stack gap={0}>
            <Text size="sm">{codec}</Text>
            {!!bitrate && <Text size="xs" c="dimmed">{formatBitRate(bitrate)}</Text>}
        </Stack>
    );
}

export default function VideoRecordings() {
    const { t } = useTranslation();

    const [recordings, setRecordings] = useState<Recording[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
    const [sortStatus, setSortStatus] = useState<DataTableSortStatus<Recording>>({
        columnAccessor: 'path',
        direction: 'asc',
    });
    const [loading, setLoading] = useState(false);
    const loadingRef = useRef(false);
    const [deleting, setDeleting] = useState(false);

    const [playing, setPlaying] = useState<Recording | null>(null);
    const [thumbnail, setThumbnail] = useState<string | null>(null);
    const [deleteRecording, setDeleteRecording] = useState<Recording | null>(null);

    function getRecordings() {
        if (loadingRef.current) { return; }
        loadingRef.current = true;
        setLoading(true);
        axios.get(apiRoutes.videoRecordingList, {
            params: {
                page,
                per_page: pageSize,
                sort_by: sortStatus.columnAccessor,
                sort_direction: sortStatus.direction,
            },
        }).then((r) => {
            if (r.status === 200) {
                setRecordings(r.data.results ?? []);
                setTotal(r.data.total ?? 0);
                setPage(r.data.current_page ?? 1);
            }
        }).catch((err) => {
            notifyFailure(t('Couldn\'t load recordings'), errorText(err));
        }).finally(() => {
            loadingRef.current = false;
            setLoading(false);
        });
    }

    useEffect(() => {
        getRecordings();
    }, [page, pageSize, sortStatus]);

    function watch(r: Recording) {
        setPlaying(r);
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function confirmDelete() {
        const rec = deleteRecording;
        setDeleteRecording(null);
        if (!rec) { return; }
        setDeleting(true);
        axios.delete(apiRoutes.videoRecordingDelete, { params: { id: rec.id } })
            .then(() => {
                notifySuccess(t('Recording deleted'), '');
                if (playing?.id === rec.id) { setPlaying(null); }
                getRecordings();
            })
            .catch((err) => notifyFailure(t('Couldn\'t delete the recording'), errorText(err)))
            .finally(() => setDeleting(false));
    }

    const playingDuration = playing ? durationText(playing) : '';

    return (
        <>
            <LoadingOverlay visible={loading || deleting} pos="fixed" zIndex={1000} />
            <PageHeader
                title={t('Video Recordings')}
                description={t('Recordings made from video streams that have recording turned on')}
            />

            {playing && (
                <Panel
                    mb="lg"
                    title={playing.path}
                    actions={(
                        <Button
                            size="xs"
                            variant="default"
                            leftSection={<Dismiss20Regular style={ICON_16} />}
                            onClick={() => setPlaying(null)}
                        >
                            {t('Close player')}
                        </Button>
                    )}
                >
                    <Text size="xs" c="dimmed" mb="sm">
                        {formatDateTime(playing.start_time, true)}
                        {playingDuration ? ` · ${playingDuration}` : ''}
                    </Text>
                    <AspectRatio ratio={16 / 9}>
                        {/* Recordings have no caption tracks */}
                        {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
                        <video
                            key={playing.id}
                            src={`${apiRoutes.videoRecording}?id=${playing.id}`}
                            controls
                            autoPlay
                            style={{ background: '#000', borderRadius: 'var(--mantine-radius-sm)' }}
                        />
                    </AspectRatio>
                </Panel>
            )}

            <Panel p={0}>
                <DataTable
                    {...PANEL_TABLE}
                    highlightOnHover
                    verticalSpacing="sm"
                    records={recordings}
                    idAccessor="id"
                    columns={[
                        {
                            accessor: 'path',
                            title: t('Recording'),
                            sortable: true,
                            render: (r) => (
                                <Group gap="sm" wrap="nowrap">
                                    <UnstyledButton
                                        aria-label={t('Thumbnail')}
                                        disabled={!r.thumbnail}
                                        onClick={() => r.thumbnail && setThumbnail(r.thumbnail)}
                                        style={{
                                            width: 96,
                                            flexShrink: 0,
                                            cursor: r.thumbnail ? 'zoom-in' : 'default',
                                        }}
                                    >
                                        <AspectRatio
                                            ratio={16 / 9}
                                            style={{
                                                background: 'var(--raven-chrome)',
                                                border: '1px solid var(--raven-border)',
                                                borderRadius: 'var(--mantine-radius-sm)',
                                                overflow: 'hidden',
                                            }}
                                        >
                                            {r.thumbnail
                                                ? <Image src={r.thumbnail} alt={t('Thumbnail')} fit="cover" />
                                                : (
                                                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: 0.4 }}>
                                                        <VideoClip20Regular />
                                                    </div>
                                                )}
                                        </AspectRatio>
                                    </UnstyledButton>
                                    <Tooltip label={r.path}>
                                        <Text size="sm" fw={600} truncate maw={260}>{r.path}</Text>
                                    </Tooltip>
                                </Group>
                            ),
                        },
                        {
                            accessor: 'in_progress',
                            title: t('Status'),
                            sortable: true,
                            render: (r) => (r.in_progress
                                ? (
                                    <Badge
                                        size="sm"
                                        color="red"
                                        variant="light"
                                        leftSection={(
                                            <span
                                                style={{
                                                    display: 'inline-block',
                                                    width: 6,
                                                    height: 6,
                                                    borderRadius: '50%',
                                                    background: 'currentColor',
                                                    animation: 'raven-pulse 1.5s ease-in-out infinite',
                                                }}
                                            />
                                        )}
                                    >
                                        {t('In Progress')}
                                    </Badge>
                                )
                                : <Badge color="gray" variant="light">{t('Complete')}</Badge>),
                        },
                        {
                            accessor: 'start_time',
                            title: t('Start Time'),
                            sortable: true,
                            render: (r) => formatDateTime(r.start_time, true),
                        },
                        {
                            accessor: 'stop_time',
                            title: t('End Time'),
                            sortable: true,
                            render: (r) => (r.stop_time
                                ? formatDateTime(r.stop_time, true)
                                : <Text size="sm" c="dimmed">—</Text>),
                        },
                        {
                            accessor: 'duration',
                            title: t('Duration'),
                            sortable: true,
                            render: (r) => durationText(r) || '—',
                        },
                        {
                            accessor: 'file_size',
                            title: t('File Size'),
                            sortable: true,
                            textAlign: 'right',
                            render: (r) => (r.file_size ? formatBytes(r.file_size) : '—'),
                        },
                        {
                            accessor: 'width',
                            title: t('Resolution'),
                            sortable: true,
                            render: (r) => (r.width && r.height ? `${r.width} x ${r.height}` : '—'),
                        },
                        {
                            accessor: 'video_codec',
                            title: t('Video'),
                            sortable: true,
                            render: (r) => codecCell(r.video_codec, r.video_bitrate),
                        },
                        {
                            accessor: 'audio_codec',
                            title: t('Audio'),
                            sortable: true,
                            render: (r) => codecCell(r.audio_codec, r.audio_bitrate),
                        },
                        {
                            accessor: 'actions',
                            title: '',
                            textAlign: 'right',
                            render: (r) => (
                                <Group gap={4} justify="flex-end" wrap="nowrap">
                                    <Tooltip label={t('Watch')}>
                                        <ActionIcon variant="subtle" aria-label={t('Watch')} onClick={() => watch(r)}>
                                            <Play20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip label={t('Download')}>
                                        <ActionIcon
                                            component="a"
                                            href={`${apiRoutes.videoRecording}?id=${r.id}`}
                                            variant="subtle"
                                            color="gray"
                                            aria-label={t('Download')}
                                        >
                                            <ArrowDownload20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip label={t('Delete')}>
                                        <ActionIcon
                                            variant="subtle"
                                            color="red"
                                            aria-label={t('Delete')}
                                            onClick={() => setDeleteRecording(r)}
                                        >
                                            <Delete20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                </Group>
                            ),
                        },
                    ]}
                    page={page}
                    onPageChange={setPage}
                    totalRecords={total}
                    recordsPerPage={pageSize}
                    recordsPerPageOptions={PAGE_SIZE_OPTIONS}
                    onRecordsPerPageChange={(size) => {
                        setPage(1);
                        setPageSize(size);
                    }}
                    sortStatus={sortStatus}
                    onSortStatusChange={setSortStatus}
                    fetching={loading}
                    minHeight={recordings.length === 0 ? 320 : undefined}
                    emptyState={(
                        <EmptyState
                            icon={<VideoClip20Regular />}
                            title={t('No recordings yet')}
                            description={t('Turn on Record for a stream on the Video Streams page and its recordings appear here.')}
                        />
                    )}
                />
            </Panel>

            <Modal size="xl" opened={thumbnail !== null} onClose={() => setThumbnail(null)} title={t('Thumbnail')}>
                {thumbnail && <Image src={thumbnail} alt={t('Thumbnail')} />}
            </Modal>

            <Modal opened={deleteRecording !== null} onClose={() => setDeleteRecording(null)} title={t('Are you sure?')}>
                <Text size="sm">{t("Delete this recording? This can't be undone.")}</Text>
                <Group justify="flex-end" mt="md">
                    <Button variant="default" onClick={() => setDeleteRecording(null)}>{t('Cancel')}</Button>
                    <Button color="red" leftSection={<Delete20Regular />} onClick={confirmDelete}>{t('Delete')}</Button>
                </Group>
            </Modal>
        </>
    );
}
