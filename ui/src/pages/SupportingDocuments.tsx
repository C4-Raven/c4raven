import { ActionIcon, Button, Checkbox, FileButton, Group, Modal, Text, Tooltip } from '@mantine/core';
import { useEffect, useState } from 'react';
import {
    ArrowDownload20Regular,
    ArrowUpload20Regular,
    Checkmark20Regular,
    Delete20Regular,
    Dismiss20Regular,
} from '@fluentui/react-icons';
import { notifications } from '@mantine/notifications';
import axios from '../axios_config';
import { apiRoutes } from '../apiRoutes';
import { formatBytes } from '../bytes_formatter';
import { t } from 'i18next';
import { DataTable, type DataTableSortStatus } from 'mantine-datatable';
import { PageHeader, Panel } from '@/components/ui';
import { isReadOnlyAdmin, isSuperAdmin } from '../adminAccess';
import { READ_ONLY_LABEL, ReadOnlyTip } from '../components/ReadOnlyTip';
import { ReadOnlyNotice } from '../components/ReadOnlyNotice';

const ICON_16 = { width: 16, height: 16 };

interface SupportingDocument {
    id: number;
    filename: string;
    mime_type: string | null;
    size: number;
    uploaded_at: string;
    uploaded_by: string | null;
}

// The disclaimer shown before every download -- these documents are
// working drafts, not signed-off/released material, so downloaders need
// to explicitly acknowledge that before they get the file.
const DRAFT_DISCLAIMER = t(
    'This document is a working draft. It may be incomplete, out of date, or replaced without notice, and hasn\'t been through final review or sign-off. Do not treat it as an authoritative or released version -- confirm anything critical with the document owner before relying on it.'
);

export default function SupportingDocuments() {
    // Only a full administrator uploads or deletes; read-only admins see those controls switched off
    const isAdmin = isSuperAdmin();
    const readOnly = isReadOnlyAdmin();
    const [documents, setDocuments] = useState<SupportingDocument[]>([]);
    const [loading, setLoading] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [sortStatus, setSortStatus] = useState<DataTableSortStatus<SupportingDocument>>({
        columnAccessor: 'filename',
        direction: 'asc',
    });
    const [deleteTarget, setDeleteTarget] = useState<SupportingDocument | null>(null);
    const [downloadTarget, setDownloadTarget] = useState<SupportingDocument | null>(null);
    const [acceptedDraft, setAcceptedDraft] = useState(false);
    const [downloading, setDownloading] = useState(false);

    function getDocuments() {
        setLoading(true);
        axios.get(apiRoutes.supportingDocuments).then((r) => {
            setLoading(false);
            if (r.status === 200) {
                setDocuments(r.data.results);
            }
        }).catch((err) => {
            setLoading(false);
            notifications.show({
                title: t('Failed to get supporting documents'),
                message: err.response?.data?.error,
                icon: <Dismiss20Regular />,
                color: 'red',
            });
        });
    }

    useEffect(() => { getDocuments(); }, []);

    const sortedDocuments = [...documents].sort((a, b) => {
        const dir = sortStatus.direction === 'asc' ? 1 : -1;
        const accessor = sortStatus.columnAccessor as keyof SupportingDocument;
        const av = a[accessor] ?? '';
        const bv = b[accessor] ?? '';
        return av > bv ? dir : av < bv ? -dir : 0;
    });

    function uploadDocument(file: File | null) {
        if (!file) {return;}
        setUploading(true);
        const formData = new FormData();
        formData.append('file', file);
        axios.post(apiRoutes.supportingDocuments, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
            .then((r) => {
                setUploading(false);
                if (r.status === 200) {
                    notifications.show({
                        message: t('{{filename}} uploaded', { filename: file.name }),
                        icon: <Checkmark20Regular />,
                        color: 'green',
                    });
                    getDocuments();
                }
            }).catch((err) => {
                setUploading(false);
                notifications.show({
                    title: t('Failed to upload document'),
                    message: err.response?.data?.error,
                    icon: <Dismiss20Regular />,
                    color: 'red',
                });
            });
    }

    function deleteDocument() {
        if (!deleteTarget) {return;}
        axios.delete(apiRoutes.supportingDocuments, { params: { id: deleteTarget.id } }).then((r) => {
            if (r.status === 200) {
                notifications.show({
                    message: t('{{filename}} deleted', { filename: deleteTarget.filename }),
                    icon: <Checkmark20Regular />,
                    color: 'green',
                });
                setDeleteTarget(null);
                getDocuments();
            }
        }).catch((err) => {
            notifications.show({
                title: t('Failed to delete document'),
                message: err.response?.data?.error,
                icon: <Dismiss20Regular />,
                color: 'red',
            });
        });
    }

    // Fetch through the shared axios instance rather than navigating the
    // window to the API URL: a 401 (expired session) or 404 would otherwise
    // replace the SPA with a raw JSON error page and skip the axios
    // 401 -> /login interceptor. The blob is handed to a temporary <a download>.
    async function confirmDownload() {
        if (!downloadTarget || !acceptedDraft || downloading) {return;}
        const { id, filename } = downloadTarget;
        setDownloading(true);
        try {
            const r = await axios.get(`${apiRoutes.supportingDocuments}/download`, {
                params: { id },
                responseType: 'blob',
            });
            const url = URL.createObjectURL(r.data as Blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            setDownloadTarget(null);
            setAcceptedDraft(false);
        } catch (err: any) {
            // With responseType 'blob' the JSON error body arrives as a Blob;
            // try to read it, fall back to a generic message.
            let message: string | undefined;
            try {
                const data = err?.response?.data;
                if (data instanceof Blob) {
                    message = JSON.parse(await data.text())?.error;
                } else {
                    message = data?.error;
                }
            } catch {
                message = undefined;
            }
            notifications.show({
                title: t('Failed to download document'),
                message: message ?? t('The document could not be downloaded'),
                icon: <Dismiss20Regular />,
                color: 'red',
            });
        } finally {
            setDownloading(false);
        }
    }

    return (
        <>
            <PageHeader
                title={t('Supporting Documents')}
                actions={(isAdmin || readOnly) && (
                    <ReadOnlyTip>
                        <FileButton onChange={uploadDocument} disabled={readOnly}>
                            {(props) => (
                                <Button {...props} loading={uploading} disabled={readOnly} leftSection={<ArrowUpload20Regular />}>
                                    {t('Upload Document')}
                                </Button>
                            )}
                        </FileButton>
                    </ReadOnlyTip>
                )}
            />
            <ReadOnlyNotice />

            <Panel p={0}>
                <DataTable
                    backgroundColor="var(--raven-surface)"
                    highlightOnHover
                    records={sortedDocuments}
                    idAccessor="id"
                    columns={[
                        { accessor: 'filename', title: t('File Name'), sortable: true },
                        {
                            accessor: 'size',
                            title: t('Size'),
                            sortable: true,
                            render: (row) => formatBytes(row.size),
                        },
                        { accessor: 'uploaded_by', title: t('Uploaded By'), sortable: true, render: (row) => row.uploaded_by ?? '—' },
                        { accessor: 'uploaded_at', title: t('Uploaded'), sortable: true },
                        {
                            accessor: 'actions',
                            title: t('Actions'),
                            textAlign: 'right',
                            render: (row) => (
                                <Group gap={4} wrap="nowrap" justify="flex-end">
                                    <Tooltip label={t('Download')}>
                                        <ActionIcon
                                            variant="subtle"
                                            aria-label={t('Download')}
                                            onClick={() => { setDownloadTarget(row); setAcceptedDraft(false); }}
                                        >
                                            <ArrowDownload20Regular style={ICON_16} />
                                        </ActionIcon>
                                    </Tooltip>
                                    {(isAdmin || readOnly) && (
                                        <Tooltip label={readOnly ? READ_ONLY_LABEL : t('Delete')}>
                                            <ActionIcon
                                                color="red"
                                                variant="subtle"
                                                disabled={readOnly}
                                                aria-label={t('Delete')}
                                                onClick={() => setDeleteTarget(row)}
                                            >
                                                <Delete20Regular style={ICON_16} />
                                            </ActionIcon>
                                        </Tooltip>
                                    )}
                                </Group>
                            ),
                        },
                    ]}
                    sortStatus={sortStatus}
                    onSortStatusChange={setSortStatus}
                    fetching={loading}
                    noRecordsText={t('No supporting documents yet')}
                    minHeight={180}
                />
            </Panel>

            <Modal
                opened={downloadTarget !== null}
                onClose={() => { setDownloadTarget(null); setAcceptedDraft(false); }}
                title={t('Download {{filename}}', { filename: downloadTarget?.filename ?? '' })}
            >
                <Text size="sm" mb="md">{DRAFT_DISCLAIMER}</Text>
                <Checkbox
                    mb="md"
                    label={t('I understand this document is a draft and accept it in its current form')}
                    checked={acceptedDraft}
                    onChange={(e) => setAcceptedDraft(e.currentTarget.checked)}
                />
                <Group justify="flex-end">
                    <Button variant="default" onClick={() => { setDownloadTarget(null); setAcceptedDraft(false); }}>{t('Cancel')}</Button>
                    <Button
                        disabled={!acceptedDraft}
                        loading={downloading}
                        leftSection={<ArrowDownload20Regular />}
                        onClick={confirmDownload}
                    >{t('Download')}</Button>
                </Group>
            </Modal>

            <Modal opened={deleteTarget !== null && !readOnly} onClose={() => setDeleteTarget(null)} title={t('Are you sure?')}>
                <Text mb="md">{t('Delete {{filename}}? This can\'t be undone.', { filename: deleteTarget?.filename })}</Text>
                <Group justify="flex-end">
                    <Button variant="default" onClick={() => setDeleteTarget(null)}>{t('Cancel')}</Button>
                    <Button color="red" onClick={deleteDocument}>{t('Delete')}</Button>
                </Group>
            </Modal>
        </>
    );
}
