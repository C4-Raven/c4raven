import {
    ActionIcon, Alert, Avatar, Badge, Box, Button, Checkbox, ComboboxItem, Divider, Group as MantineGroup, Modal,
    MultiSelect, NumberInput, Popover, SegmentedControl, Select, SimpleGrid, Stack, Switch, Tabs, Text, TextInput,
    Tooltip,
} from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import { useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { addDays, differenceInHours, format, isValid, parseISO } from 'date-fns';
import axios from '@/axios_config';
import { apiRoutes } from '@/apiRoutes.tsx';
import {
    Add20Regular, ArrowBidirectionalLeftRight20Regular, ArrowDownload20Regular, ArrowUpload20Regular,
    CalendarLtr20Regular, Clock20Regular, Delete20Regular, Dismiss20Regular, Edit20Regular, Eye20Regular,
    Info20Regular, LockClosed20Regular, People16Regular, People20Regular, PeopleAdd20Regular,
    PeopleSettings20Regular, PeopleTeam20Regular, PersonAdd20Regular, Search20Regular,
} from '@fluentui/react-icons';
import { DataTable, type DataTableColumn, type DataTableSortStatus } from 'mantine-datatable';
import UserVisibilityDiagram from '../components/UserVisibilityDiagram';
import { PageHeader, Panel, StatTile } from '@/components/ui';
import { ReadOnlyNotice } from '../components/ReadOnlyNotice';
import { READ_ONLY_LABEL, ReadOnlyTip } from '../components/ReadOnlyTip';
import { isReadOnlyAdmin } from '../adminAccess';
import { EmptyState } from './ServerRequests/shared';
import { DEFAULT_PAGE_SIZE, PAGE_SIZE_OPTIONS } from './pageUtils';

type Access = 'both' | 'IN' | 'OUT';

// Group policy endpoints (kept here rather than in apiRoutes, which only this page uses)
const GROUP_POLICY_ROUTE = '/api/groups/policy';
const GROUP_POLICY_APPLY_ROUTE = '/api/groups/policy/apply';

export interface Group {
    name: string;
    created: string;
    type: string;
    bitpos: number;
    description: string;
    default_access: Access;
    auto_join: boolean;
    default_membership_days: number | null;
    locked: boolean;
    member_count: number;
}

/** One row of GET /api/groups. `bitpos` arrives as a binary string. */
function groupFromApi(row: any): Group {
    return {
        name: row.name,
        created: row.created,
        type: row.type,
        bitpos: parseInt(row.bitpos, 2),
        description: row.description,
        default_access: row.default_access ?? 'both',
        auto_join: Boolean(row.auto_join),
        default_membership_days: row.default_membership_days ?? null,
        locked: Boolean(row.locked),
        member_count: row.member_count ?? 0,
    };
}

// A group's raw membership is one row per (user, direction) -- IN lets the
// user send to the group, OUT lets them receive from it. The Members list
// collapses that to one row per person with a single access setting, so a
// member can be made send-only or receive-only right here.
interface MemberRow {
    username: string;
    directions: string[];
    expiresAt: Date | null;
}

interface GroupForm {
    name: string;
    description: string;
    default_access: Access;
    auto_join: boolean;
    default_membership_days: number | string;
    locked: boolean;
    applyToExisting: boolean;
}

const EMPTY_FORM: GroupForm = {
    name: '',
    description: '',
    default_access: 'both',
    auto_join: false,
    default_membership_days: '',
    locked: false,
    applyToExisting: false,
};

// Sent as the direction when adding/removing; the API accepts BOTH too.
const ACCESS_PARAM: Record<Access, string> = { both: 'BOTH', IN: 'IN', OUT: 'OUT' };

// Small icons inside badges and inputs.
const ICON_SM = { width: 14, height: 14 };
const ICON_MD = { width: 16, height: 16 };

const ACCESS_ICON: Record<Access, React.ReactNode> = {
    both: <ArrowBidirectionalLeftRight20Regular style={ICON_SM} />,
    IN: <ArrowUpload20Regular style={ICON_SM} />,
    OUT: <ArrowDownload20Regular style={ICON_SM} />,
};

// Tables sit on the panel/modal surface rather than the page background.
const TABLE_COLORS = {
    backgroundColor: 'var(--raven-surface)',
    borderColor: 'var(--raven-border)',
    rowBorderColor: 'var(--raven-border)',
} as const;

// Show a members search box once the list gets longer than this.
const MEMBER_SEARCH_THRESHOLD = 8;

function accessOf(directions: string[]): Access {
    const send = directions.includes('IN');
    const receive = directions.includes('OUT');
    if (send && receive) { return 'both'; }
    return send ? 'IN' : 'OUT';
}

function initials(name: string): string {
    return name.slice(0, 2).toUpperCase();
}

function parseServerDate(value: string | null | undefined): Date | null {
    if (!value) { return null; }
    const d = parseISO(value);
    return isValid(d) ? d : null;
}

// Mantine dates v9 hands back "YYYY-MM-DD HH:mm:ss" in local time.
function toPicker(d: Date | null): string | null {
    return d ? format(d, 'yyyy-MM-dd HH:mm:ss') : null;
}

function fromPicker(v: string | null): Date | null {
    if (!v) { return null; }
    const d = parseISO(v.replace(' ', 'T'));
    return isValid(d) ? d : null;
}

function defaultExpiry(group: Group | null): Date | null {
    return group?.default_membership_days ? addDays(new Date(), group.default_membership_days) : null;
}

function groupMembersByUser(raw: { username: string; direction: string; expires_at?: string | null }[]): MemberRow[] {
    const byUser = new Map<string, MemberRow>();
    raw.forEach((row) => {
        let member = byUser.get(row.username);
        if (!member) {
            member = { username: row.username, directions: [], expiresAt: null };
            byUser.set(row.username, member);
        }
        if (!member.directions.includes(row.direction)) { member.directions.push(row.direction); }
        const expires = parseServerDate(row.expires_at);
        if (expires && (!member.expiresAt || expires < member.expiresAt)) { member.expiresAt = expires; }
    });
    return Array.from(byUser.values()).sort((a, b) => a.username.localeCompare(b.username));
}

function errorMessage(err: any): string | undefined {
    return err?.response?.data?.error ?? err?.message;
}

// Wraps a control that's disabled because the group is locked, so the
// tooltip still shows (disabled elements don't fire mouse events).
function LockedTip({ locked, label, children }: { locked: boolean; label: string; children: React.ReactNode }) {
    if (!locked) { return <>{children}</>; }
    return (
        <Tooltip label={label} withinPortal>
            <span style={{ display: 'inline-flex' }}>{children}</span>
        </Tooltip>
    );
}

export default function Groups() {
    const { t } = useTranslation();
    // Table paging; a new page size starts again from the first page.
    const [paging, setPaging] = useState({ page: 1, size: DEFAULT_PAGE_SIZE });
    const goToPage = (page: number) => setPaging((p) => (p.page === page ? p : { ...p, page }));
    const changePageSize = (size: number) => setPaging({ page: 1, size });
    const [totalRecords, setTotalRecords] = useState(0);
    const [loading, setLoading] = useState(false);
    const [sortStatus, setSortStatus] = useState<DataTableSortStatus<Group>>({
        columnAccessor: 'name',
        direction: 'asc',
    });
    const requestIdRef = useRef(0);
    const [groups, setGroups] = useState<Group[]>([]);

    const [groupToDelete, setGroupToDelete] = useState<Group | null>(null);
    const [deleteGroupOpen, setDeleteGroupOpen] = useState(false);

    // Create (editing === null) / edit group modal
    const [formOpen, setFormOpen] = useState(false);
    const [editing, setEditing] = useState<Group | null>(null);
    const [form, setForm] = useState<GroupForm>(EMPTY_FORM);
    const [saving, setSaving] = useState(false);
    const [applying, setApplying] = useState(false);

    // Manage members modal
    const [membersOpen, setMembersOpen] = useState(false);
    const [group, setGroup] = useState<Group | null>(null);
    const [memberRows, setMemberRows] = useState<MemberRow[]>([]);
    const [membersLoading, setMembersLoading] = useState(false);
    const [membersTab, setMembersTab] = useState<string | null>('members');
    const [memberSearch, setMemberSearch] = useState('');
    const membersFullScreen = useMediaQuery('(max-width: 72em)');
    const [busyMember, setBusyMember] = useState<string | null>(null);
    const [addMemberUsers, setAddMemberUsers] = useState<string[]>([]);
    const [addMemberAccess, setAddMemberAccess] = useState<Access>('both');
    const [addMemberExpiry, setAddMemberExpiry] = useState<string | null>(null);
    const [allUsers, setAllUsers] = useState<ComboboxItem[]>([]);
    const [userFilters, setUserFilters] = useState<{ id: number; name: string; usernames: string[] }[]>([]);
    const [addMemberFilterId, setAddMemberFilterId] = useState<string | null>(null);
    const [expiryEditing, setExpiryEditing] = useState<string | null>(null);
    const [expiryDraft, setExpiryDraft] = useState<string | null>(null);

    const groupName = group?.name ?? '';
    // Stable between renders so the diagram only re-scopes when membership changes
    const memberKey = memberRows.map((m) => m.username).join('\n');
    const memberUsernames = useMemo(() => (memberKey ? memberKey.split('\n') : []), [memberKey]);
    const readOnly = isReadOnlyAdmin();
    // A locked group shows its members as plain text; a read-only admin sees the usual
    // controls, greyed out. Either way nothing can be changed.
    const groupLocked = !!group?.locked;
    const locked = groupLocked || readOnly;
    const lockedLabel = readOnly
        ? t('Read-only access: only a full administrator can change members.')
        : t('This group is locked. Unlock it in its policies to change members.');

    const accessOptions = [
        { value: 'both', label: t('Send & receive') },
        { value: 'IN', label: t('Send only') },
        { value: 'OUT', label: t('Receive only') },
    ];
    const accessLabel: Record<Access, string> = {
        both: t('Send & receive'),
        IN: t('Send only'),
        OUT: t('Receive only'),
    };

    function showError(title: string, err: any) {
        console.log(err);
        notifications.show({ title, message: errorMessage(err), icon: <Dismiss20Regular />, color: 'red' });
    }

    function getGroups(): Promise<Group[]> {
        const requestId = ++requestIdRef.current;
        setLoading(true);
        return axios.get(apiRoutes.groupList, {
            params: {
                page: paging.page,
                per_page: paging.size,
                sort_by: sortStatus.columnAccessor,
                sort_direction: sortStatus.direction,
            },
        }).then((r) => {
            if (requestId !== requestIdRef.current) { return []; }
            setLoading(false);
            const rows: Group[] = r.data.results.map(groupFromApi);
            goToPage(r.data.current_page);
            setTotalRecords(r.data.total);
            setGroups(rows);
            return rows;
        }).catch((err) => {
            if (requestId === requestIdRef.current) { setLoading(false); }
            showError(t("Couldn't load groups"), err);
            return [];
        });
    }

    // Keeps the open members modal in step with the table after a policy change.
    function refreshGroups() {
        getGroups().then((rows) => {
            setGroup((current) => (current ? rows.find((g) => g.name === current.name) ?? current : current));
        });
    }

    function openCreate() {
        setEditing(null);
        setForm(EMPTY_FORM);
        setFormOpen(true);
    }

    function openEdit(row: Group) {
        setEditing(row);
        setForm({
            name: row.name,
            description: row.description ?? '',
            default_access: row.default_access,
            auto_join: row.auto_join,
            default_membership_days: row.default_membership_days ?? '',
            locked: row.locked,
            applyToExisting: false,
        });
        setFormOpen(true);
    }

    function policyPayload() {
        return {
            description: form.description,
            default_access: form.default_access,
            auto_join: form.auto_join,
            default_membership_days: typeof form.default_membership_days === 'number' ? form.default_membership_days : null,
            locked: form.locked,
        };
    }

    function applyToExisting(name: string) {
        return axios.post(GROUP_POLICY_APPLY_ROUTE, { group_name: name }).then((r) => {
            const added: string[] = r.data.added ?? [];
            notifications.show({
                title: t('Existing users added'),
                message: added.length === 0
                    ? t('Everyone is already in {{group}}', { group: name })
                    : added.length === 1
                        ? t('Added {{username}} to {{group}}', { username: added[0], group: name })
                        : t('Added {{count}} users to {{group}}', { count: added.length, group: name }),
                color: 'green',
            });
        });
    }

    function saveGroup() {
        if (!editing && !form.name.trim()) { return; }
        setSaving(true);
        const request = editing
            ? axios.patch(GROUP_POLICY_ROUTE, { group_name: editing.name, ...policyPayload() })
            : axios.post(apiRoutes.groupList, { name: form.name.trim(), ...policyPayload() })
                .then(() => (form.auto_join && form.applyToExisting && !form.locked
                    ? applyToExisting(form.name.trim()) : undefined));
        request.then(() => {
            setFormOpen(false);
            refreshGroups();
        }).catch((err) => {
            showError(editing ? t("Couldn't save group") : t("Couldn't create group"), err);
        }).finally(() => setSaving(false));
    }

    function applyNow() {
        if (!editing) { return; }
        setApplying(true);
        applyToExisting(editing.name)
            .then(() => refreshGroups())
            .catch((err) => showError(t('Failed to add existing users'), err))
            .finally(() => setApplying(false));
    }

    function deleteGroup(name: string) {
        axios.delete(apiRoutes.groupList, { params: { group_name: name } }).then(() => {
            getGroups();
        }).catch((err) => showError(t('Failed to delete {{group}}', { group: name }), err));
    }

    function getGroupMembers(name: string) {
        setMembersLoading(true);
        axios.get(apiRoutes.groupMembers, { params: { name } }).then((r) => {
            setMemberRows(groupMembersByUser(r.data));
        }).catch((err) => showError(t("Couldn't load group members"), err))
            .finally(() => setMembersLoading(false));
    }

    function getUserFilters() {
        axios.get(apiRoutes.userFilters).then((r) => setUserFilters(r.data)).catch((err) => console.log(err));
    }

    function getAllUsers() {
        axios.get(apiRoutes.userListAll).then((r) => {
            setAllUsers(r.data.map((row: any) => row.username));
        }).catch((err) => showError(t("Couldn't load users"), err));
    }

    function openMembers(row: Group) {
        setGroup(row);
        setMemberRows([]);
        setMembersTab('members');
        setMemberSearch('');
        setAddMemberUsers([]);
        setAddMemberFilterId(null);
        setAddMemberAccess(row.default_access);
        setAddMemberExpiry(toPicker(defaultExpiry(row)));
        getGroupMembers(row.name);
        // The user list and filters only feed the add-member form, which read-only admins don't get
        if (!readOnly) {
            getAllUsers();
            getUserFilters();
        }
        setMembersOpen(true);
    }

    function closeMembers() {
        setMembersOpen(false);
        // Member counts in the table
        getGroups();
    }

    // Adds each selected user with the chosen access -- both ways (the usual
    // case), send-only (IN) or receive-only (OUT) -- and expiry.
    function addMemberToGroup() {
        if (addMemberUsers.length === 0 || !group) { return; }
        const expires = fromPicker(addMemberExpiry);
        axios.put(apiRoutes.groupList, {
            users: addMemberUsers,
            group_name: group.name,
            direction: ACCESS_PARAM[addMemberAccess],
            expires_at: expires ? expires.toISOString() : null,
        }).then(() => {
            getGroupMembers(group.name);
            setAddMemberUsers([]);
        }).catch((err) => showError(t('Failed to add member'), err));
    }

    function updateMember(row: MemberRow, change: { access?: Access; expires_at?: string | null }, failTitle: string) {
        setBusyMember(row.username);
        return axios.patch(apiRoutes.groupMembers, { username: row.username, group_name: groupName, ...change })
            .then(() => true)
            .catch((err) => { showError(failTitle, err); return false; })
            .finally(() => {
                setBusyMember(null);
                getGroupMembers(groupName);
            });
    }

    function setMemberAccess(row: MemberRow, access: Access) {
        if (access === accessOf(row.directions)) { return; }
        // Show the new choice straight away; the reload after the request
        // puts it back if the server refused.
        setMemberRows((rows) => rows.map((r) => (r.username === row.username
            ? { ...r, directions: access === 'both' ? ['IN', 'OUT'] : [access] } : r)));
        updateMember(row, { access }, t('Failed to change member access'));
    }

    function openExpiryEditor(row: MemberRow) {
        setExpiryDraft(toPicker(row.expiresAt ?? defaultExpiry(group) ?? addDays(new Date(), 7)));
        setExpiryEditing(row.username);
    }

    function saveExpiry(row: MemberRow, value: Date | null) {
        updateMember(row, { expires_at: value ? value.toISOString() : null }, t('Failed to change expiry'))
            .then((ok) => { if (ok) { setExpiryEditing(null); } });
    }

    function removeMemberCompletely(row: MemberRow) {
        axios.delete(apiRoutes.groupMembers, {
            params: { username: row.username, group_name: groupName, direction: 'BOTH' },
        }).then(() => getGroupMembers(groupName))
            .catch((err) => showError(t('Failed to remove member'), err));
    }

    useEffect(() => {
        getGroups();
    }, [paging.page, paging.size, sortStatus]);

    const activeAddMemberFilter = userFilters.find((f) => String(f.id) === addMemberFilterId);
    const addableUsers = allUsers.filter((u) => {
        const username = typeof u === 'string' ? u : u.value;
        if (memberRows.some((m) => m.username === username)) { return false; }
        if (activeAddMemberFilter && !activeAddMemberFilter.usernames.includes(username)) { return false; }
        return true;
    });
    const addExpiryDate = fromPicker(addMemberExpiry);
    const addExpiryInPast = !!addExpiryDate && addExpiryDate.getTime() <= Date.now();
    const draftDate = fromPicker(expiryDraft);
    const draftInPast = !!draftDate && draftDate.getTime() <= Date.now();

    const searchTerm = memberSearch.trim().toLowerCase();
    const visibleMembers = searchTerm
        ? memberRows.filter((m) => m.username.toLowerCase().includes(searchTerm))
        : memberRows;

    // Page-level quick stats. Only the group total is server-wide; the rest
    // count the loaded page when the table is paged.
    const paged = totalRecords > groups.length;
    const pageLabel = (s: string) => (paged ? `${s} · ${t('this page')}` : s);
    const memberTotal = groups.reduce((sum, g) => sum + g.member_count, 0);
    const autoJoinCount = groups.filter((g) => g.auto_join).length;
    const lockedCount = groups.filter((g) => g.locked).length;

    const accessHelp = (
        <MantineGroup gap={4} wrap="nowrap" component="span">
            {t('Access')}
            <Tooltip
                multiline
                w={320}
                withinPortal
                label={t("Send only: the member's data reaches the group but they don't see the group's. Receive only: the opposite. Two-way members see each other both ways, so use these for one-way access.")}
            >
                <span style={{ display: 'inline-flex', color: 'var(--raven-text-muted)' }}>
                    <Info20Regular style={ICON_SM} />
                </span>
            </Tooltip>
        </MantineGroup>
    );

    function policyBadges(row: Group) {
        return (
            <MantineGroup gap={6} wrap="wrap">
                {row.locked && (
                    <Badge variant="light" color="red" tt="none" leftSection={<LockClosed20Regular style={ICON_SM} />}>{t('Locked')}</Badge>
                )}
                <Badge variant="default" tt="none" leftSection={ACCESS_ICON[row.default_access]}>
                    {accessLabel[row.default_access]}
                </Badge>
                {row.auto_join && (
                    <Badge variant="default" tt="none" leftSection={<PersonAdd20Regular style={ICON_SM} />}>{t('Auto-join')}</Badge>
                )}
                {row.default_membership_days && (
                    <Badge variant="default" tt="none" leftSection={<Clock20Regular style={ICON_SM} />}>
                        {t('Expires after {{count}} days', { count: row.default_membership_days })}
                    </Badge>
                )}
            </MantineGroup>
        );
    }

    function expiryChip(row: MemberRow) {
        const expires = row.expiresAt;
        const hoursLeft = expires ? differenceInHours(expires, new Date()) : null;
        const urgent = hoursLeft !== null && hoursLeft < 24;
        const label = expires
            ? t('Expires {{when}}', { when: format(expires, 'd MMM yyyy, HH:mm') })
            : t('No expiry');
        return (
            <Popover
                opened={expiryEditing === row.username}
                onChange={(opened) => { if (!opened) { setExpiryEditing(null); } }}
                position="bottom-end"
                withinPortal
                trapFocus
            >
                <Popover.Target>
                    <span style={{ display: 'inline-flex' }}>
                        <LockedTip locked={locked} label={lockedLabel}>
                            <Button
                                variant="subtle"
                                size="xs"
                                color={urgent ? 'red' : undefined}
                                c={expires ? undefined : 'dimmed'}
                                leftSection={<Clock20Regular style={ICON_SM} />}
                                disabled={locked}
                                aria-label={t('Set expiry for {{username}}', { username: row.username })}
                                onClick={() => {
                                    if (locked) { return; }
                                    if (expiryEditing === row.username) { setExpiryEditing(null); } else { openExpiryEditor(row); }
                                }}
                            >
                                {label}
                            </Button>
                        </LockedTip>
                    </span>
                </Popover.Target>
                <Popover.Dropdown>
                    <Stack gap="xs" w={260}>
                        <DateTimePicker
                            label={t('Membership expires')}
                            description={t('Your local time')}
                            leftSection={<CalendarLtr20Regular style={ICON_MD} />}
                            value={expiryDraft}
                            onChange={setExpiryDraft}
                            minDate={new Date()}
                            valueFormat="ddd D MMM YYYY, HH:mm"
                            popoverProps={{ withinPortal: false }}
                            error={draftInPast ? t('Pick a time in the future') : undefined}
                        />
                        <MantineGroup justify="space-between" gap="xs">
                            <Button
                                size="xs"
                                variant="subtle"
                                color="red"
                                disabled={!row.expiresAt}
                                loading={busyMember === row.username && !draftDate}
                                onClick={() => saveExpiry(row, null)}
                            >
                                {t('Remove expiry')}
                            </Button>
                            <Button
                                size="xs"
                                disabled={!draftDate || draftInPast}
                                loading={busyMember === row.username}
                                onClick={() => saveExpiry(row, draftDate)}
                            >
                                {t('Save')}
                            </Button>
                        </MantineGroup>
                    </Stack>
                </Popover.Dropdown>
            </Popover>
        );
    }

    const memberColumns: DataTableColumn<MemberRow>[] = [
        {
            accessor: 'username',
            title: t('Member'),
            render: (row) => (
                <MantineGroup gap="sm" wrap="nowrap" data-member={row.username}>
                    <Avatar size="sm" variant="light" color="blue">{initials(row.username)}</Avatar>
                    <Text size="sm" fw={600}>{row.username}</Text>
                </MantineGroup>
            ),
        },
        {
            accessor: 'access',
            title: accessHelp,
            width: 190,
            render: (row) => (groupLocked ? (
                <Badge variant="light" color="gray" tt="none" leftSection={ACCESS_ICON[accessOf(row.directions)]}>
                    {accessLabel[accessOf(row.directions)]}
                </Badge>
            ) : (
                <Select
                    size="xs"
                    w={160}
                    data={accessOptions}
                    value={accessOf(row.directions)}
                    allowDeselect={false}
                    onChange={(v) => { if (v) { setMemberAccess(row, v as Access); } }}
                    disabled={locked || busyMember === row.username}
                    aria-label={t('Access for {{username}}', { username: row.username })}
                    comboboxProps={{ withinPortal: true }}
                />
            )),
        },
        {
            accessor: 'expiresAt',
            title: t('Expires'),
            render: (row) => (groupLocked ? (
                <Text size="sm" c={row.expiresAt ? undefined : 'dimmed'}>
                    {row.expiresAt
                        ? t('Expires {{when}}', { when: format(row.expiresAt, 'd MMM yyyy, HH:mm') })
                        : t('No expiry')}
                </Text>
            ) : expiryChip(row)),
        },
        ...(groupLocked ? [] : [{
            accessor: 'actions',
            title: '',
            textAlign: 'right' as const,
            width: 60,
            render: (row: MemberRow) => (
                <Tooltip label={readOnly ? READ_ONLY_LABEL : t('Remove from group')}>
                    <ActionIcon
                        color="red"
                        variant="subtle"
                        disabled={readOnly}
                        aria-label={t('Remove {{username}} from group', { username: row.username })}
                        onClick={() => removeMemberCompletely(row)}
                    >
                        <Delete20Regular />
                    </ActionIcon>
                </Tooltip>
            ),
        }]),
    ];

    return (
        <>
            <PageHeader
                title={t('Groups')}
                description={t('Who can send to and receive from each group, and the policies new members get')}
                actions={<ReadOnlyTip><Button disabled={readOnly} leftSection={<Add20Regular />} onClick={openCreate}>{t('Add Group')}</Button></ReadOnlyTip>}
            />

            <ReadOnlyNotice />

            <SimpleGrid cols={{ base: 2, md: 4 }} spacing="md" mb="xl">
                <StatTile icon={<PeopleTeam20Regular />} value={totalRecords} label={t('Groups')} color="blue" />
                <StatTile icon={<People20Regular />} value={memberTotal} label={pageLabel(t('Members'))} color="teal" />
                <StatTile icon={<PersonAdd20Regular />} value={autoJoinCount} label={pageLabel(t('Auto-join'))} color="violet" />
                <StatTile
                    icon={<LockClosed20Regular />}
                    value={lockedCount}
                    label={pageLabel(t('Locked'))}
                    color={lockedCount > 0 ? 'red' : 'gray'}
                />
            </SimpleGrid>

            <Panel p={0}>
                <DataTable
                    {...TABLE_COLORS}
                    highlightOnHover
                    scrollAreaProps={{ type: 'auto', offsetScrollbars: true }}
                    records={groups}
                    idAccessor="name"
                    columns={[
                        {
                            accessor: 'name',
                            title: t('Name'),
                            sortable: true,
                            render: (row: Group) => (
                                <MantineGroup gap="sm" wrap="nowrap">
                                    <Avatar size="sm" variant="light">{initials(row.name)}</Avatar>
                                    <div style={{ minWidth: 0 }}>
                                        <Text size="sm" fw={600} truncate>{row.name}</Text>
                                        <Text size="xs" c="dimmed" truncate>{row.description || t('No description')}</Text>
                                    </div>
                                </MantineGroup>
                            ),
                        },
                        {
                            accessor: 'member_count',
                            title: t('Members'),
                            textAlign: 'right',
                            width: 120,
                            render: (row: Group) => (
                                <Button
                                    variant="subtle"
                                    color="gray"
                                    size="compact-sm"
                                    leftSection={<People16Regular />}
                                    aria-label={t('Manage members')}
                                    onClick={() => openMembers(row)}
                                    styles={{ label: { fontVariantNumeric: 'tabular-nums' } }}
                                >
                                    {row.member_count}
                                </Button>
                            ),
                        },
                        {
                            accessor: 'policies',
                            title: t('Policies'),
                            render: (row: Group) => policyBadges(row),
                        },
                        {
                            accessor: 'created',
                            title: t('Created'),
                            sortable: true,
                            width: 130,
                            render: (row: Group) => (row.created ? (
                                <Text size="sm" c="dimmed" title={new Date(row.created).toLocaleString()}>
                                    {new Date(row.created).toLocaleDateString()}
                                </Text>
                            ) : '—'),
                        },
                        {
                            accessor: 'actions',
                            title: t('Actions'),
                            textAlign: 'right',
                            width: 140,
                            render: (row: Group) => (
                                <MantineGroup gap={4} justify="flex-end" wrap="nowrap">
                                    <Tooltip label={t('Manage members')}>
                                        <ActionIcon variant="subtle" aria-label={t('Manage members')} onClick={() => openMembers(row)}>
                                            <PeopleSettings20Regular />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip label={readOnly ? READ_ONLY_LABEL : t('Edit group & policies')}>
                                        <ActionIcon variant="subtle" disabled={readOnly} aria-label={t('Edit group & policies')} onClick={() => openEdit(row)}>
                                            <Edit20Regular />
                                        </ActionIcon>
                                    </Tooltip>
                                    <Tooltip
                                        label={readOnly ? READ_ONLY_LABEL : row.name === '__ANON__'
                                            ? t("The default group can't be deleted")
                                            : row.locked ? t('Unlock this group before deleting it') : t('Delete group')}
                                    >
                                        <span style={{ display: 'inline-flex' }}>
                                            <ActionIcon
                                                color="red"
                                                variant="subtle"
                                                aria-label={t('Delete group')}
                                                disabled={readOnly || row.name === '__ANON__' || row.locked}
                                                onClick={() => {
                                                    setGroupToDelete(row);
                                                    setDeleteGroupOpen(true);
                                                }}
                                            >
                                                <Delete20Regular />
                                            </ActionIcon>
                                        </span>
                                    </Tooltip>
                                </MantineGroup>
                            ),
                        },
                    ]}
                    page={paging.page}
                    onPageChange={goToPage}
                    onRecordsPerPageChange={changePageSize}
                    totalRecords={totalRecords}
                    recordsPerPage={paging.size}
                    recordsPerPageOptions={PAGE_SIZE_OPTIONS}
                    sortStatus={sortStatus}
                    onSortStatusChange={setSortStatus}
                    fetching={loading}
                    minHeight={groups.length === 0 ? 320 : undefined}
                    emptyState={(
                        <EmptyState
                            icon={<PeopleTeam20Regular />}
                            title={t('No groups yet')}
                            description={t('Create a group to control who can send to and receive from whom.')}
                            action={<ReadOnlyTip><Button disabled={readOnly} leftSection={<Add20Regular />} onClick={openCreate}>{t('Add Group')}</Button></ReadOnlyTip>}
                        />
                    )}
                />
            </Panel>

            {/* Create / edit group */}
            <Modal
                opened={formOpen}
                onClose={() => setFormOpen(false)}
                title={editing ? t('Edit {{group}}', { group: editing.name }) : t('Add Group')}
                size="lg"
            >
                <Stack gap="md">
                    <Divider
                        labelPosition="left"
                        label={<Text size="xs" fw={600} tt="uppercase" c="dimmed">{t('Details')}</Text>}
                    />
                    <TextInput
                        required
                        label={t('Name')}
                        value={form.name}
                        disabled={!!editing}
                        onChange={(e) => { const name = e.currentTarget.value; setForm((f) => ({ ...f, name })); }}
                    />
                    <TextInput
                        label={t('Description')}
                        value={form.description}
                        onChange={(e) => { const description = e.currentTarget.value; setForm((f) => ({ ...f, description })); }}
                    />

                    <Divider
                        mt="xs"
                        labelPosition="left"
                        label={<Text size="xs" fw={600} tt="uppercase" c="dimmed">{t('Policies')}</Text>}
                    />

                    <div>
                        <Text size="sm" fw={500} mb={4}>{t('Default access for new members')}</Text>
                        <SegmentedControl
                            fullWidth
                            value={form.default_access}
                            onChange={(v) => setForm((f) => ({ ...f, default_access: v as Access }))}
                            data={accessOptions}
                        />
                        <Text size="xs" c="dimmed" mt={4}>
                            {t('Preselected when adding members, and used for auto-join.')}
                        </Text>
                    </div>

                    <Stack gap="xs" align="flex-start">
                        <Switch
                            label={t('Auto-join new users')}
                            description={t('New user accounts are added to this group with the default access.')}
                            checked={form.auto_join}
                            onChange={(e) => { const auto_join = e.currentTarget.checked; setForm((f) => ({ ...f, auto_join })); }}
                        />
                        {editing ? (
                            <LockedTip locked={editing.locked} label={lockedLabel}>
                                <Button
                                    size="xs"
                                    variant="default"
                                    leftSection={<PeopleAdd20Regular style={ICON_MD} />}
                                    loading={applying}
                                    disabled={editing.locked}
                                    onClick={applyNow}
                                >
                                    {t('Add all current users now')}
                                </Button>
                            </LockedTip>
                        ) : form.auto_join && (
                            <Checkbox
                                size="xs"
                                label={t('Also add all current users when the group is created')}
                                checked={form.applyToExisting}
                                onChange={(e) => { const applyToExisting = e.currentTarget.checked; setForm((f) => ({ ...f, applyToExisting })); }}
                            />
                        )}
                    </Stack>

                    <NumberInput
                        label={t('Default membership duration')}
                        description={t('New memberships expire after this many days. Leave empty for no expiry.')}
                        placeholder={t('No expiry')}
                        min={1}
                        max={3650}
                        allowDecimal={false}
                        allowNegative={false}
                        rightSection={<Text size="xs" c="dimmed" pr={8}>{t('days')}</Text>}
                        rightSectionWidth={48}
                        value={form.default_membership_days}
                        onChange={(v) => setForm((f) => ({ ...f, default_membership_days: v }))}
                        maw={260}
                    />

                    <Switch
                        color="red"
                        label={t('Locked')}
                        description={t("A locked group can't be deleted and its members can't be changed until it's unlocked. Auto-join and expiry still apply.")}
                        checked={form.locked}
                        onChange={(e) => { const isLocked = e.currentTarget.checked; setForm((f) => ({ ...f, locked: isLocked })); }}
                    />

                    <MantineGroup justify="flex-end" gap="sm" mt="xs">
                        <Button variant="default" onClick={() => setFormOpen(false)}>{t('Cancel')}</Button>
                        <Button
                            loading={saving}
                            disabled={!editing && !form.name.trim()}
                            leftSection={editing ? undefined : <Add20Regular />}
                            onClick={saveGroup}
                        >
                            {editing ? t('Save') : t('Add Group')}
                        </Button>
                    </MantineGroup>
                </Stack>
            </Modal>

            {/* Members */}
            <Modal
                size={1100}
                fullScreen={!!membersFullScreen}
                opened={membersOpen}
                onClose={closeMembers}
                title={t('Manage {{group}} Members', { group: groupName })}
            >
                {group && <Box mb="md">{policyBadges(group)}</Box>}
                {locked && (
                    <Alert color={readOnly ? 'gray' : 'red'} variant="light" icon={<LockClosed20Regular />} mb="md">
                        {lockedLabel}
                    </Alert>
                )}

                <Tabs value={membersTab} onChange={setMembersTab} keepMounted={false}>
                    <Tabs.List mb="md">
                        <Tabs.Tab
                            value="members"
                            leftSection={<People20Regular style={ICON_MD} />}
                            rightSection={<Badge size="sm" variant="light" color="gray">{memberRows.length}</Badge>}
                        >
                            {t('Members')}
                        </Tabs.Tab>
                        <Tabs.Tab
                            value="visibility"
                            leftSection={<Eye20Regular style={ICON_MD} />}
                            disabled={memberRows.length === 0}
                        >
                            {t('Who Can See Whom')}
                        </Tabs.Tab>
                    </Tabs.List>

                    <Tabs.Panel value="members">
                        {!groupLocked && (
                            <MantineGroup align="flex-end" gap="sm" wrap="wrap" mb="md">
                                {userFilters.length > 0 && (
                                    <Select
                                    disabled={readOnly}
                                        w={160}
                                        label={t('Narrow to filter')}
                                        placeholder={t('All users')}
                                        clearable
                                        value={addMemberFilterId}
                                        onChange={setAddMemberFilterId}
                                        data={userFilters.map((f) => ({ value: String(f.id), label: f.name }))}
                                    />
                                )}
                                <MultiSelect
                                    disabled={readOnly}
                                    style={{ flex: '1 1 260px' }}
                                    placeholder={t('Search users')}
                                    searchable
                                    clearable
                                    nothingFoundMessage={t('Nothing found...')}
                                    label={t('Add member')}
                                    value={addMemberUsers}
                                    onChange={setAddMemberUsers}
                                    data={addableUsers}
                                />
                                <Select
                                    disabled={readOnly}
                                    w={170}
                                    label={accessHelp}
                                    data={accessOptions}
                                    value={addMemberAccess}
                                    allowDeselect={false}
                                    onChange={(v) => { if (v) { setAddMemberAccess(v as Access); } }}
                                />
                                <DateTimePicker
                                    disabled={readOnly}
                                    w={210}
                                    label={t('Expires')}
                                    placeholder={t('No expiry')}
                                    leftSection={<CalendarLtr20Regular style={ICON_MD} />}
                                    clearable
                                    value={addMemberExpiry}
                                    onChange={setAddMemberExpiry}
                                    minDate={new Date()}
                                    valueFormat="D MMM YYYY, HH:mm"
                                    popoverProps={{ withinPortal: true }}
                                    error={addExpiryInPast ? t('Pick a time in the future') : undefined}
                                />
                                <LockedTip locked={locked} label={lockedLabel}>
                                    <Button
                                        leftSection={<Add20Regular />}
                                        onClick={addMemberToGroup}
                                        disabled={locked || addMemberUsers.length === 0 || addExpiryInPast}
                                    >
                                        {t('Add')}
                                    </Button>
                                </LockedTip>
                            </MantineGroup>
                        )}

                        {memberRows.length > MEMBER_SEARCH_THRESHOLD && (
                            <MantineGroup justify="flex-end" mb="xs">
                                <TextInput
                                    size="xs"
                                    w={220}
                                    placeholder={t('Search')}
                                    aria-label={t('Search')}
                                    leftSection={<Search20Regular style={ICON_MD} />}
                                    value={memberSearch}
                                    onChange={(e) => setMemberSearch(e.currentTarget.value)}
                                />
                            </MantineGroup>
                        )}

                        <DataTable
                            withTableBorder
                            {...TABLE_COLORS}
                            highlightOnHover
                            verticalSpacing="xs"
                            idAccessor="username"
                            minHeight={160}
                            noRecordsText={locked ? t('No members yet.') : t('No members yet — add some above.')}
                            records={visibleMembers}
                            fetching={membersLoading}
                            columns={memberColumns}
                        />
                    </Tabs.Panel>

                    <Tabs.Panel value="visibility">
                        <Text size="sm" c="dimmed" mb="md">
                            {readOnly
                                ? t('Drag a member to rearrange them.')
                                : t('Drag a member to rearrange them, then click one and another to connect or disconnect them.')}
                        </Text>
                        {membersOpen && membersTab === 'visibility' && (
                            <UserVisibilityDiagram
                                scopeToUsernames={memberUsernames}
                                onChange={() => getGroupMembers(groupName)}
                            />
                        )}
                    </Tabs.Panel>
                </Tabs>
            </Modal>

            <Modal
                opened={deleteGroupOpen}
                onClose={() => setDeleteGroupOpen(false)}
                title={t('Delete group {{group}}?', { group: groupToDelete?.name ?? '' })}
            >
                {groupToDelete && (
                    <MantineGroup gap="sm" wrap="nowrap" mb="md">
                        <Avatar size="sm" variant="light">{initials(groupToDelete.name)}</Avatar>
                        <Text fw={600}>{groupToDelete.name}</Text>
                        <Text size="sm" c="dimmed">{t('{{count}} members', { count: groupToDelete.member_count })}</Text>
                    </MantineGroup>
                )}
                <Text size="sm" mb="md">
                    {t("Everyone in the group loses access to it straight away. This can't be undone.")}
                </Text>
                <MantineGroup justify="flex-end" gap="sm">
                    <Button variant="default" onClick={() => setDeleteGroupOpen(false)}>{t('Cancel')}</Button>
                    <Button
                        color="red"
                        leftSection={<Delete20Regular />}
                        onClick={() => {
                            if (groupToDelete) { deleteGroup(groupToDelete.name); }
                            setDeleteGroupOpen(false);
                        }}
                    >
                        {t('Delete')}
                    </Button>
                </MantineGroup>
            </Modal>
        </>
    );
}
