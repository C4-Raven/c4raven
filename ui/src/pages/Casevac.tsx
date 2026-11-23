import { type ReactNode, useEffect, useState } from 'react';
import { Button, Group, Modal } from '@mantine/core';
import { Checkmark20Regular, Dismiss20Regular, SubtractCircle20Regular } from '@fluentui/react-icons';
import { notifications } from '@mantine/notifications';
import { DataTable, type DataTableColumn, type DataTableSortStatus } from 'mantine-datatable';
import { t } from 'i18next';
import axios from '../axios_config';
import { apiRoutes } from '../apiRoutes';
import { PageHeader, Panel } from '../components/ui';
import { DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS } from './pageUtils';

interface Zmist {
    i: string | null;
    m: string | null;
    s: string | null;
    t: string | null;
    title: string | null;
    z: string | null;
}

export interface Casevac {
    uid: string;
    title: string | null;
    timestamp: string | null;
    callsign?: string | null;
    eud?: { callsign: string | null } | null;
    casevac: boolean | null;
    urgent: number | null;
    priority: number | null;
    hoist: number | null;
    routine: number | null;
    version?: string | null;
    extraction_equipment: boolean | null;
    ventilator: boolean | null;
    equipment_detail: string | null;
    litter: number | null;
    ambulatory: number | null;
    security: number | null;
    hlz_marking: number | null;
    hlz_remarks: string | null;
    us_military: number | null;
    us_civilian: number | null;
    nonus_military: number | null;
    nonus_civilian: number | null;
    epw: number | null;
    child: number | null;
    terrain_slope_dir: string | null;
    terrain_rough: boolean | null;
    terrain_loose: boolean | null;
    terrain_other: boolean | null;
    terrain_other_detail: string | null;
    medline_remarks: string | null;
    zmist?: Zmist | null;
}

/** Shows a value as-is; booleans are spelled out so they don't render as an empty cell. */
function raw(value: unknown) {
    if (typeof value === 'boolean') {
        return String(value);
    }
    return value as ReactNode;
}

/** Read-only, server-paginated list of CasEvac (9-line MEDEVAC) reports sent by EUDs. */
export default function Casevac() {
    const [records, setRecords] = useState<Casevac[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
    const [sortStatus, setSortStatus] = useState<DataTableSortStatus<Casevac>>({
        columnAccessor: 'title',
        direction: 'desc',
    });
    const [loading, setLoading] = useState(false);
    const [deleteUid, setDeleteUid] = useState<string | null>(null);

    const load = () => {
        setLoading(true);
        axios
            .get(apiRoutes.casevacList, {
                params: {
                    page,
                    per_page: pageSize,
                    sort_by: sortStatus.columnAccessor,
                    sort_direction: sortStatus.direction,
                },
            })
            .then((r) => {
                if (r.status === 200) {
                    setRecords(r.data.results ?? []);
                    setTotal(r.data.total ?? 0);
                    if (r.data.current_page) {
                        setPage(r.data.current_page);
                    }
                }
            })
            .catch((err) => {
                notifications.show({
                    title: t('Failed to get CasEvacs'),
                    message: err?.response?.data?.error,
                    color: 'red',
                    icon: <Dismiss20Regular />,
                });
            })
            .finally(() => setLoading(false));
    };

    useEffect(load, [page, pageSize, sortStatus]);

    // Size and page change together (batched), so a new page size sends one request, for page 1.
    const changePageSize = (size: number) => {
        setPageSize(size);
        setPage(1);
    };

    const confirmDelete = () => {
        const uid = deleteUid;
        setDeleteUid(null);
        if (!uid) {
            return;
        }
        axios
            .delete(apiRoutes.casevacList, { params: { uid } })
            .then((r) => {
                if (r.status === 200) {
                    load();
                    notifications.show({
                        message: t('Successfully Deleted CasEvac'),
                        color: 'green',
                        icon: <Checkmark20Regular />,
                    });
                }
            })
            .catch((err) => {
                notifications.show({
                    title: t('Failed to delete CasEvac'),
                    message: err?.response?.data?.error,
                    color: 'red',
                    icon: <Dismiss20Regular />,
                });
            });
    };

    const col = (accessor: string, title: string, sortable = true): DataTableColumn<Casevac> => ({
        accessor,
        title: t(title),
        sortable,
        render: (row) => {
            const value = accessor.split('.').reduce<unknown>(
                (obj, key) => (obj && typeof obj === 'object' ? (obj as Record<string, unknown>)[key] : undefined),
                row,
            );
            return raw(value);
        },
    });

    const columns: DataTableColumn<Casevac>[] = [
        col('title', 'Title'),
        col('timestamp', 'Timestamp'),
        col('eud.callsign', 'Callsign', false),
        col('casevac', 'CasEvac'),
        col('urgent', 'Urgent'),
        col('hoist', 'Hoist', false),
        col('routine', 'Routine', false),
        col('version', 'Version', false),
        col('extraction_equipment', 'Extraction Equipment'),
        col('ventilator', 'Ventilator'),
        col('equipment_detail', 'Equipment Detail'),
        col('litter', 'Litter'),
        col('ambulatory', 'Ambulatory'),
        col('security', 'Security'),
        col('hlz_marking', 'HLZ Marking'),
        col('hlz_remarks', 'HLZ Remarks'),
        col('us_military', 'Coalition Military'),
        col('us_civilian', 'Coalition Civilian'),
        col('nonus_military', 'Non-Coalition Military'),
        col('nonus_civilian', 'Non-Coalition Civilian'),
        col('epw', 'Opposing Force or Detainee'),
        col('child', 'Children'),
        col('terrain_slope_dir', 'Terrain Slope Direction'),
        col('terrain_rough', 'Rough Terrain'),
        col('terrain_loose', 'Loose Terrain'),
        col('terrain_other_detail', 'Terrain Remarks'),
        col('medline_remarks', 'Remarks'),
        col('zmist.i', 'Injuries Sustained', false),
        col('zmist.m', 'Mechanism of Injury', false),
        col('zmist.s', 'Symptoms and Signs', false),
        col('zmist.t', 'Treatment Given', false),
        col('zmist.title', 'Title', false),
        col('zmist.z', 'Zap Number', false),
        {
            accessor: 'delete_button',
            title: t('Delete'),
            render: (row) => (
                <Button
                    color="red"
                    variant="subtle"
                    aria-label={t('Delete')}
                    onClick={() => setDeleteUid(row.uid)}
                >
                    <SubtractCircle20Regular />
                </Button>
            ),
        },
    ];

    return (
        <>
            <PageHeader title={t('CasEvac')} />
            <Panel p={0}>
                <DataTable
                    backgroundColor="var(--raven-surface)"
                    borderColor="var(--raven-border)"
                    rowBorderColor="var(--raven-border)"
                    highlightOnHover
                    minHeight={180}
                    fetching={loading}
                    idAccessor="uid"
                    records={records}
                    columns={columns}
                    totalRecords={total}
                    page={page}
                    onPageChange={setPage}
                    recordsPerPage={pageSize}
                    recordsPerPageOptions={PAGE_SIZE_OPTIONS}
                    onRecordsPerPageChange={changePageSize}
                    sortStatus={sortStatus}
                    onSortStatusChange={setSortStatus}
                />
            </Panel>

            <Modal
                opened={deleteUid !== null}
                onClose={() => setDeleteUid(null)}
                title={t('Are you sure you want to delete this CasEvac?')}
            >
                <Group gap="xs">
                    <Button color="red" onClick={confirmDelete}>
                        {t('Yes')}
                    </Button>
                    <Button variant="default" onClick={() => setDeleteUid(null)}>
                        {t('No')}
                    </Button>
                </Group>
            </Modal>
        </>
    );
}
