import {
    ActionIcon,
    Box,
    Button,
    Checkbox,
    ComboboxItem,
    CopyButton,
    FileButton,
    Group,
    Menu,
    Modal,
    MultiSelect,
    NumberInput,
    PasswordInput,
    SegmentedControl,
    Select,
    Stack,
    Switch,
    Tabs,
    TagsInput,
    Text,
    TextInput,
    Tooltip,
} from '@mantine/core';
import React, { useEffect, useRef, useState } from 'react';
import {
    Add20Regular,
    ArrowClockwise20Regular,
    ArrowDownload20Regular,
    ArrowUpload20Regular,
    Certificate20Regular,
    Checkmark20Regular,
    Copy20Regular,
    Delete20Regular,
    Dismiss20Regular,
    Eraser20Regular,
    Filter20Regular,
    FolderZip20Regular,
    Globe20Regular,
    Key20Regular,
    MoreVertical20Regular,
    NetworkCheck20Regular,
    Password20Regular,
    PersonAdd20Regular,
    PersonDelete20Regular,
    PersonSettings20Regular,
    QrCode20Regular,
    Eye20Regular,
} from '@fluentui/react-icons';
import { notifications } from '@mantine/notifications';
import { QRCode } from 'react-qrcode-logo';
import axios from '../axios_config';
import { apiRoutes } from '../apiRoutes';
import {t} from "i18next";
import { DataTable, type DataTableSortStatus } from 'mantine-datatable';
import { VpnQrPanel } from '../components/VpnQrPanel';
import { UserCertificatesModal } from '../components/UserCertificatesModal';
import { PageHeader, Panel } from '@/components/ui';
import { AdminAreasModal } from '../components/AdminAreasModal';
import { ReadOnlyNotice } from '../components/ReadOnlyNotice';
import { READ_ONLY_LABEL, ReadOnlyTip } from '../components/ReadOnlyTip';
import { isReadOnlyAdmin } from '../adminAccess';

type Direction = 'IN' | 'OUT';

/** A device linked to an account; callsign is null until the device has reported one. */
interface LinkedEud { uid: string; callsign: string | null }

/** One row of GET /api/users. The *_login_* fields stay null until the first sign-in. */
export interface User {
    username: string;
    // Account switches
    active: boolean;
    site_access: boolean;
    roles: { name: string }[];
    /** Read-only admins only: the admin areas a superadmin opened for them. */
    admin_view_areas?: string[];
    euds: LinkedEud[];
    // Sign-in history
    login_count: number;
    current_login_at: string | null;
    current_login_ip: string | null;
    last_login_at: string | null;
    last_login_ip: string | null;
}

const ROLE_OPTIONS = [
    { value: 'administrator', label: t('Administrator') },
    { value: 'admin_viewer', label: t('Read-only admin') },
    { value: 'user', label: t('User') },
];

/** The one role the Users page shows: Administrator beats Read-only admin beats User. */
function roleOf(user: User): string {
    const names = user.roles.map((r) => r.name);
    return ROLE_OPTIONS.find((o) => names.includes(o.value))?.value ?? names[0] ?? 'user';
}

/** What the server is sent for a role picked on this page. A read-only admin keeps the user role. */
const rolesFor = (role: string) => (role === 'admin_viewer' ? ['user', 'admin_viewer'] : [role]);

const roleLabel = (role: string) => ROLE_OPTIONS.find((o) => o.value === role)?.label ?? role;

// One row per group in the Manage Groups modal: the IN and OUT rows the API
// returns for the same group are folded together.
interface Membership {
    group_name: string;
    directions: string[];
    active: boolean;
}

// System/service accounts (e.g. "Server", used to send files) that admins
// can't modify from this page — keep in sync with RAVEN_PROTECTED_USERNAMES
// on the backend, which is the actual enforcement point.
const PROTECTED_USERNAMES = ['Server', 'Admin'];
const isProtectedUser = (username: string) => PROTECTED_USERNAMES.includes(username);

// How many users to fetch per scroll-triggered request. Not user-configurable
// since there's no page control anymore -- the table just keeps growing as
// the admin scrolls down.
const USERS_BATCH_SIZE = 25;

// Tables sit on the panel/modal surface rather than the page background.
const TABLE_COLORS = {
    backgroundColor: 'var(--raven-surface)',
    borderColor: 'var(--raven-border)',
    rowBorderColor: 'var(--raven-border)',
} as const;

function notifyError(title: string, message?: string) {
    notifications.show({ title, message, icon: <Dismiss20Regular />, color: 'red' });
}

function callsignsOf(user: User): string[] {
    return (user.euds ?? [])
        .map((eud) => eud.callsign)
        .filter((callsign): callsign is string => !!callsign);
}

function saveBlob(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

// Blob requests get their JSON error body back as a Blob too.
async function blobErrorMessage(err: any): Promise<string | undefined> {
    try {
        const data = err?.response?.data;
        return data instanceof Blob ? JSON.parse(await data.text())?.error : data?.error;
    } catch {
        return undefined;
    }
}

// Pick groups for one direction and add them -- shown once for send, once for receive.
function GroupPicker({ title, groups, value, onChange, onAdd }: {
    title: string;
    groups: ComboboxItem[];
    value: string[];
    onChange: (value: string[]) => void;
    onAdd: () => void;
}) {
    return (
        <Group align="flex-end" gap="sm" wrap="nowrap">
            <MultiSelect
                style={{ flex: 1 }}
                label={title}
                description={t("Select Groups")}
                placeholder={t("Search")}
                searchable
                clearable
                nothingFoundMessage={t("Nothing found...")}
                value={value}
                onChange={onChange}
                data={groups}
            />
            <Button leftSection={<Add20Regular />} onClick={onAdd} disabled={value.length === 0}>{t("Add")}</Button>
        </Group>
    );
}

export default function Users() {
    const [users, setUsers] = useState<User[]>([]);
    const [userCount, setUserCount] = useState<number>(0);
    const [page, setPage] = useState(1);
    const [hasMore, setHasMore] = useState(true);
    const [loadingMore, setLoadingMore] = useState(false);
    // Monotonic id of the most recent getUsers() call. Responses whose id is
    // no longer current (e.g. a page-2 append that was in flight when the sort
    // changed and page 1 was re-requested) are dropped so stale rows from the
    // old ordering can't be appended onto the new list.
    const requestIdRef = useRef(0);
    // Mirror of the in-flight state that updates synchronously, so two
    // onScrollToBottom events in the same tick can't both fire a request
    // before React has re-rendered with loading/loadingMore set.
    const inFlightRef = useRef(false);
    const loadMoreSentinelRef = useRef<HTMLDivElement | null>(null);
    const [sentinelVisible, setSentinelVisible] = useState(false);
    const [sortStatus, setSortStatus] = useState<DataTableSortStatus<User>>({
        columnAccessor: 'username',
        direction: 'asc',
    });
    const [addUserOpen, setAddUserOpen] = useState(false);
    const [showDeleteUser, setShowDeleteUser] = useState(false);
    const [showClearContent, setShowClearContent] = useState(false);
    const [clearContentUsername, setClearContentUsername] = useState('');
    // The target's device callsigns; the admin must retype one of these to arm
    // the wipe, so a mis-click can't clear the wrong person's device.
    const [clearContentCallsigns, setClearContentCallsigns] = useState<string[]>([]);
    const [clearAck, setClearAck] = useState(false);
    const [clearConfirm, setClearConfirm] = useState('');
    const [clearMaps, setClearMaps] = useState(false);
    const [queueOffline, setQueueOffline] = useState(false);
    const [clearing, setClearing] = useState(false);
    const [tempPasswordInfo, setTempPasswordInfo] = useState<{ username: string; password: string } | null>(null);
    const [showManageGroups, setShowManageGroups] = useState(false);
    const [showManageFilters, setShowManageFilters] = useState(false);
    const [newFilterName, setNewFilterName] = useState('');
    const [showSendFile, setShowSendFile] = useState(false);
    const [sendFileUsername, setSendFileUsername] = useState('');
    const [sendFileFile, setSendFileFile] = useState<File | null>(null);
    const [sendingFile, setSendingFile] = useState(false);
    const [showJoinQr, setShowJoinQr] = useState(false);
    const [certificatesUsername, setCertificatesUsername] = useState('');
    const [joinQrUsername, setJoinQrUsername] = useState('');
    const [joinQrString, setJoinQrString] = useState('');
    const [joinQrTab, setJoinQrTab] = useState<string | null>('domain');
    const [enrollMode, setEnrollMode] = useState<string>('qr');
    // '' = no per-user override, i.e. the server default (RAVEN_CA_EXPIRATION_TIME)
    const [certDays, setCertDays] = useState<string | number>('');
    const [savedCertDays, setSavedCertDays] = useState<number | null>(null);
    const [certDaysLimits, setCertDaysLimits] = useState<{ default_days: number; max_days: number } | null>(null);
    const [generatingCertPackage, setGeneratingCertPackage] = useState(false);
    // Ignores a validity lookup that resolves after the admin has opened the
    // modal for a different user.
    const enrollUsernameRef = useRef('');
    const [username, setUsername] = useState('');
    const [password, setPassword] = useState('');
    const [confirm_password, setConfirmPassword] = useState('');
    const [role, setRole] = useState('user');
    const readOnly = isReadOnlyAdmin();
    const [areasUser, setAreasUser] = useState<User | null>(null);
    const [allGroups, setAllGroups] = useState<ComboboxItem[]>([]);
    // Groups picked but not yet added, kept per direction (IN = send, OUT = receive)
    // so pressing Add in one box never grants what is picked in the other.
    const [picked, setPicked] = useState<Record<Direction, string[]>>({ IN: [], OUT: [] });
    const pick = (direction: Direction) => (groups: string[]) => setPicked((p) => ({ ...p, [direction]: groups }));
    const [loading, setLoading] = useState(false);
    const [memberships, setMemberships] = useState<Membership[]>([]);
    const [userFilters, setUserFilters] = useState<{ id: number; name: string; usernames: string[] }[]>([]);
    const [activeFilterId, setActiveFilterId] = useState<string | null>(null);
    // The Federation Hub bridge creates an inactive "fed-<callsign>" account
    // for every federated device so it can be put in groups. The server leaves
    // those out of /api/users unless asked.
    const [showFederated, setShowFederated] = useState(false);

    const currentUsername = localStorage.getItem('username');

    function getUserFilters() {
        axios.get(apiRoutes.userFilters).then((r) => {
            setUserFilters(r.data);
        }).catch((err) => {
            console.log(err);
        });
    }

    // Which filters a given username currently belongs to, by name --
    // what the per-row TagsInput both displays and diffs against.
    function filterNamesFor(user: string): string[] {
        return userFilters.filter((f) => f.usernames.includes(user)).map((f) => f.name);
    }

    // TagsInput hands back the full desired tag list on every change --
    // diff it against what the user is actually in and make only the
    // add/remove calls that changed, creating a new filter by that name
    // the first time it's used (see the backend's PUT /api/users/filters).
    function setUserFilterTags(targetUsername: string, nextNames: string[]) {
        const current = filterNamesFor(targetUsername);
        const toAdd = nextNames.filter((n) => !current.includes(n));
        const toRemove = current.filter((n) => !nextNames.includes(n));
        Promise.all([
            ...toAdd.map((filter_name) => axios.put(apiRoutes.userFilters, { username: targetUsername, filter_name })),
            ...toRemove.map((filter_name) => axios.delete(apiRoutes.userFilters, { params: { username: targetUsername, filter_name } })),
        ]).then(() => {
            getUserFilters();
        }).catch((err) => {
            notifyError(t('Failed to update filters for {{username}}', { username: targetUsername }), err.response?.data?.error);
        });
    }

    function createFilter() {
        if (!newFilterName.trim()) { return; }
        axios.post(apiRoutes.userFilters, { name: newFilterName.trim() }).then(() => {
            setNewFilterName('');
            getUserFilters();
        }).catch((err) => notifyError(t('Failed to create filter'), err.response?.data?.error));
    }

    function deleteFilter(filterId: number) {
        axios.delete(`${apiRoutes.userFilters}/${filterId}`).then(() => {
            if (activeFilterId === String(filterId)) { setActiveFilterId(null); }
            getUserFilters();
        }).catch((err) => notifyError(t('Failed to delete filter'), err.response?.data?.error));
    }

    // pageToLoad/replace let this double as both "start over from page 1"
    // (filter or sort changed, or the list needs refreshing after an action)
    // and "append the next page" (the DataTable scrolled to the bottom).
    function getUsers(pageToLoad: number, replace: boolean) {
        const requestId = ++requestIdRef.current;
        inFlightRef.current = true;
        if (replace) { setLoading(true); } else { setLoadingMore(true); }
        axios.get(apiRoutes.userList, {
            params: {
                page: pageToLoad,
                per_page: USERS_BATCH_SIZE,
                sort_by: sortStatus.columnAccessor,
                sort_direction: sortStatus.direction,
                filter_id: activeFilterId,
                include_federated: showFederated || undefined,
            },
        }).then(r => {
            // A newer request superseded this one -- it owns the flags now.
            if (requestId !== requestIdRef.current) { return; }
            inFlightRef.current = false;
            setLoading(false);
            setLoadingMore(false);
            if (r.status === 200) {
                setUsers(prev => replace ? r.data.results : [...prev, ...r.data.results]);
                setPage(r.data.current_page);
                setUserCount(r.data.total);
                setHasMore(r.data.current_page < r.data.total_pages);
            }
        }).catch((err) => {
            if (requestId !== requestIdRef.current) { return; }
            inFlightRef.current = false;
            setLoading(false);
            setLoadingMore(false);
            notifyError(t("Couldn't load users"), err.response?.data?.error);
        });
    }

    function loadMoreUsers() {
        if (inFlightRef.current || loading || loadingMore || !hasMore) { return; }
        getUsers(page + 1, false);
    }

    useEffect(() => { getUserFilters(); }, []);
    useEffect(() => { getUsers(1, true); }, [activeFilterId, sortStatus, showFederated]);

    // The table grows with the page instead of scrolling in its own box, so
    // load the next page when a sentinel just below it nears the viewport.
    // The observer only reports changes, so the effect below also keeps
    // pulling pages while the sentinel stays visible (short first page).
    useEffect(() => {
        const sentinel = loadMoreSentinelRef.current;
        if (!sentinel) { return; }
        const observer = new IntersectionObserver(
            ([entry]) => setSentinelVisible(entry.isIntersecting),
            { rootMargin: '400px' },
        );
        observer.observe(sentinel);
        return () => observer.disconnect();
    }, []);

    useEffect(() => {
        if (sentinelVisible && users.length > 0) {
            loadMoreUsers();
        }
    }, [sentinelVisible, users, hasMore, loading, loadingMore]);

    function getAllGroups() {
        axios.get(apiRoutes.groupListAll).then(r => {
            if (r.status === 200) {
                setAllGroups(r.data.map((row: any) => row.name));
            }
        }).catch(err => notifyError(t("Couldn't load groups"), err.response?.data?.error));
    }

    function removeUserFromGroup(user_name: string, group_name: string, direction: string) {
        axios.delete(apiRoutes.groupMembers, {params: {username: user_name, group_name, direction}}).then((r) => {
            if (r.status === 200) {
                getMemberships(user_name);
            }
        }).catch(err => notifyError(t("Couldn't remove user from group"), err.response?.data?.error));
    }

    function getMemberships(user_name: string) {
        axios.get(apiRoutes.userGroups, {params: {username: user_name}}).then(r => {
            if (r.status === 200) {
                // Consolidate the IN and OUT rows the API returns for the same
                // group into one row -- Tx/Rx when both are present (mutual),
                // otherwise just whichever single direction this user has.
                const byGroup = new Map<string, Membership>();
                r.data.results.forEach((row: any) => {
                    const existing = byGroup.get(row.group_name);
                    if (existing) {
                        if (!existing.directions.includes(row.direction)) { existing.directions.push(row.direction); }
                    } else {
                        byGroup.set(row.group_name, { group_name: row.group_name, directions: [row.direction], active: row.active });
                    }
                });
                setMemberships(Array.from(byGroup.values()));
            }
        });
    }

    function directionLabel(directions: string[]): string {
        if (directions.includes('IN') && directions.includes('OUT')) { return t('Tx/Rx'); }
        return directions.includes('IN') ? t('Tx (send)') : t('Rx (receive)');
    }

    async function joinPickedGroups(direction: Direction) {
        try {
            await axios.put(apiRoutes.userGroups, { username, direction, groups: picked[direction] });
        } catch (err: any) {
            notifyError(t("Couldn't add user to group"), err.response?.data?.error);
            return;
        }
        pick(direction)([]);
        getMemberships(username);
    }

    function confirmDeleteUser() {
        setShowDeleteUser(false);
        deleteUser();
    }

    function deleteUser() {
        axios.post(apiRoutes.userDelete, { username })
            .then(r => {
                if (r.status === 200) {
                    notifications.show({
                        message: t('User deleted'),
                        icon: <Checkmark20Regular />,
                        color: 'green',
                    });
                    getUsers(1, true);
                }
            }).catch(err => notifyError(t("Couldn't delete user"), err.response?.data?.error));
    }

    function addUser(e: React.MouseEvent) {
        e.preventDefault();
        axios.post(
            apiRoutes.userAdd,
            { username, password, confirm_password, roles: rolesFor(role) }
        ).then(r => {
            if (r.status === 200) {
                setPassword('');
                setConfirmPassword('');
                setAddUserOpen(false);
                getUsers(1, true);
            }
        }).catch(err => notifyError(t("Couldn't add user"), err.response?.data?.error));
    }

    // The one-click account actions all POST { username } and report back the
    // same way; `refresh` reloads the table for the ones it shows.
    function postUserAction(route: string, body: object, success: string, failure: string, refresh = true) {
        axios.post(route, body).then(r => {
            if (r.status === 200) {
                if (refresh) { getUsers(1, true); }
                notifications.show({ message: success, color: 'green' });
            }
        }).catch(err => notifyError(failure, err.response?.data?.error));
    }

    function changeRole(target: string, newRole: string) {
        postUserAction(apiRoutes.userRole, { username: target, roles: rolesFor(newRole) },
            `Changed ${target}'s role to ${newRole}`, `Failed to change ${target}'s role`);
    }

    function setActive(target: string, active: boolean) {
        if (active) {
            postUserAction(apiRoutes.userActivate, { username: target },
                `${target} has been activated`, `Failed to activate ${target}`);
        } else {
            postUserAction(apiRoutes.userDeactivate, { username: target },
                `${target} has been deactivated`, `Failed to deactivate ${target}`);
        }
    }

    function setSiteAccess(target: string, granted: boolean) {
        if (granted) {
            postUserAction(apiRoutes.userSiteAccessGrant, { username: target },
                `${target} has been granted website access`, `Failed to grant ${target} website access`);
        } else {
            postUserAction(apiRoutes.userSiteAccessRevoke, { username: target },
                `${target}'s website access has been revoked`, `Failed to revoke ${target}'s website access`);
        }
    }

    function forcePasswordReset(target: string) {
        postUserAction(apiRoutes.userForcePasswordReset, { username: target },
            `${target} will be asked to set a new password next time they log in`,
            `Failed to flag ${target} for a password reset`, false);
    }

    function downloadPlugin() {
        axios.get(apiRoutes.pluginRavenClearApk, { responseType: 'blob' })
            .then(r => saveBlob(r.data as Blob, 'C4Raven-RemoteClear.apk'))
            .catch(async (err) => {
                const message = await blobErrorMessage(err);
                notifyError(
                    t('Remote Clear plugin not available'),
                    message ?? t('The plugin APK has not been uploaded to the server yet.'),
                );
            });
    }

    // Whether the retyped callsign matches one of the target's device callsigns
    // (or, if none of the devices have a callsign, the username as a fallback).
    function clearConfirmValid() {
        const expected = clearContentCallsigns.length ? clearContentCallsigns : [clearContentUsername];
        return clearAck && expected.includes(clearConfirm.trim());
    }

    function openClearContent(row: User) {
        setClearContentUsername(row.username);
        setClearContentCallsigns(callsignsOf(row));
        setClearAck(false);
        setClearConfirm('');
        setClearMaps(false);
        setQueueOffline(false);
        setShowClearContent(true);
    }

    function clearContent() {
        if (!clearConfirmValid()) { return; }
        setClearing(true);
        axios.post(apiRoutes.userClearContent, { username: clearContentUsername, clearmaps: clearMaps, queue_if_offline: queueOffline })
            .then(r => {
                if (r.status === 200) {
                    const sentNow = r.data.sent_now ?? r.data.devices;
                    const queued = r.data.queued ?? 0;
                    const reconnects = r.data.reconnect_requested ?? 0;
                    notifications.show({
                        message: queued
                            ? t('Clear command queued for {{queued}} device(s); {{reconnects}} online device(s) asked to reconnect and receive it now', { queued, reconnects })
                            : t('Clear command sent to {{count}} device(s)', { count: sentNow }),
                        color: 'green',
                    });
                    setShowClearContent(false);
                }
            })
            .catch(err => notifyError(t('Failed to send clear command'), err.response?.data?.error ?? t('The clear command could not be sent')))
            .finally(() => setClearing(false));
    }

    function sendFileToUser() {
        if (!sendFileFile) { return; }
        setSendingFile(true);
        const formData = new FormData();
        formData.append('username', sendFileUsername);
        formData.append('file', sendFileFile);
        axios.post(apiRoutes.userSendFile, formData, { headers: { 'Content-Type': 'multipart/form-data' } })
            .then(r => {
                setSendingFile(false);
                if (r.status === 200) {
                    notifications.show({
                        message: `${sendFileFile.name} sent to ${sendFileUsername}`,
                        icon: <Checkmark20Regular />,
                        color: 'green',
                    });
                    setShowSendFile(false);
                    setSendFileFile(null);
                }
            }).catch(err => {
                setSendingFile(false);
                notifyError(`Failed to send file to ${sendFileUsername}`, err.response?.data?.error);
            });
    }

    // ATAK/WinTAK only -- an iTAK variant was tried (a best-effort guess at
    // its bundle identifier convention, since it's a separate app from ATAK
    // with no authoritative QR format reference available here) and
    // confirmed not to work, so it's been removed rather than leave a
    // non-functional option in the UI.
    //
    // The enroll link needs a signed token (see /api/atak_qr_string in
    // token_api.py) or ATAK just opens without adding the server/user --
    // host+username alone isn't enough to authenticate the CSR request
    // handled by certificate_enrollment_api.py.
    async function generateJoinQr(targetUsername: string) {
        if (!(await saveCertDays())) {
            return;
        }
        axios.post(apiRoutes.qrAtak, { username: targetUsername }).then(r => {
            if (r.status === 200 && r.data.qr_string) {
                setJoinQrString(r.data.qr_string);
            }
        }).catch(err => notifyError(`Failed to generate join QR code for ${targetUsername}`, err.response?.data?.error));
    }

    // One modal for both ways of getting a device onto the server: the QR
    // codes (ATAK enrolls itself) or a certificate data package the admin
    // hands over. Both issue certs valid for the user's stored number of days,
    // which the server reads at signing time, so it's saved before either.
    function openEnroll(targetUsername: string) {
        enrollUsernameRef.current = targetUsername;
        setJoinQrUsername(targetUsername);
        setJoinQrString('');
        setJoinQrTab('domain');
        setEnrollMode('qr');
        setCertDays('');
        setSavedCertDays(null);
        setCertDaysLimits(null);
        setShowJoinQr(true);
        axios.get(apiRoutes.userCertificateValidity, { params: { username: targetUsername } }).then(r => {
            if (r.status === 200 && enrollUsernameRef.current === targetUsername) {
                setSavedCertDays(r.data.cert_validity_days ?? null);
                setCertDays(r.data.cert_validity_days ?? '');
                setCertDaysLimits({ default_days: r.data.default_days, max_days: r.data.max_days });
            }
        }).catch(err => notifyError(t('Failed to load certificate validity'), err.response?.data?.error));
    }

    // Resolves false (after notifying) if the days couldn't be saved, so the
    // caller doesn't issue a cert with a validity the admin didn't ask for.
    async function saveCertDays(): Promise<boolean> {
        const days = certDays === '' ? null : Number(certDays);
        if (days === savedCertDays) {
            return true;
        }
        try {
            await axios.put(apiRoutes.userCertificateValidity, { username: joinQrUsername, cert_validity_days: days });
            setSavedCertDays(days);
            return true;
        } catch (err: any) {
            notifyError(t('Failed to save certificate validity'), err.response?.data?.error);
            return false;
        }
    }

    async function downloadCertPackage() {
        const targetUsername = joinQrUsername;
        setGeneratingCertPackage(true);
        try {
            if (!(await saveCertDays())) {
                return;
            }
            // download: true returns the zip itself -- admins can't fetch another
            // user's private package through /api/data_packages/download.
            const r = await axios.post(
                apiRoutes.certCreate,
                { username: targetUsername, download: true },
                { responseType: 'blob' },
            );
            const disposition: string = r.headers['content-disposition'] ?? '';
            // Prefer filename* -- for non-ASCII usernames werkzeug's plain
            // filename= is an ASCII-only fallback with those letters dropped.
            const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition) ?? /filename="?([^";]+)"?/i.exec(disposition);
            saveBlob(r.data as Blob, match ? decodeURIComponent(match[1]) : `${targetUsername}_CONFIG.zip`);
        } catch (err: any) {
            notifyError(`${t('Failed to generate certificate package for')} ${targetUsername}`, await blobErrorMessage(err));
        } finally {
            setGeneratingCertPackage(false);
        }
    }

    function issueTempPassword(target: string) {
        axios.post(apiRoutes.userTempPassword, { username: target }).then(r => {
            if (r.status === 200) {
                setTempPasswordInfo({ username: target, password: r.data.password });
            }
        }).catch(err => notifyError(`Failed to issue a temporary password for ${target}`, err.response?.data?.error));
    }

    function openManageGroups(target: string) {
        setShowManageGroups(true);
        getAllGroups();
        getMemberships(target);
        setUsername(target);
    }

    function openSendFile(target: string) {
        setSendFileUsername(target);
        setSendFileFile(null);
        setShowSendFile(true);
    }

    function renderActions(row: User) {
        const isSelf = row.username === currentUsername;
        // A protected account (e.g. "Admin") can still manage itself --
        // it's only other admins who are locked out of it.
        const lockedForCaller = isProtectedUser(row.username) && !isSelf;
        return (
            <Group gap={4} wrap="nowrap" justify="flex-end">
                <Tooltip label={readOnly ? READ_ONLY_LABEL : lockedForCaller ? t("This account can't be modified") : t('Manage groups')}>
                    <ActionIcon
                        variant="subtle"
                        disabled={lockedForCaller || readOnly}
                        aria-label={t('Manage groups')}
                        onClick={() => openManageGroups(row.username)}
                    >
                        <PersonSettings20Regular />
                    </ActionIcon>
                </Tooltip>
                <Menu width={230} position="bottom-end" withinPortal>
                    <Menu.Target>
                        <ActionIcon variant="subtle" disabled={lockedForCaller} aria-label={t('Actions')}>
                            <MoreVertical20Regular />
                        </ActionIcon>
                    </Menu.Target>
                    <Menu.Dropdown>
                        <Menu.Label>{t('Password')}</Menu.Label>
                        <Menu.Item leftSection={<Password20Regular />} disabled={readOnly} onClick={() => forcePasswordReset(row.username)}>
                            {t('Force password reset')}
                        </Menu.Item>
                        <Menu.Item leftSection={<Key20Regular />} disabled={readOnly} onClick={() => issueTempPassword(row.username)}>
                            {t('Issue temporary password')}
                        </Menu.Item>
                        <Menu.Divider />
                        <Menu.Label>{t('Device')}</Menu.Label>
                        <Menu.Item leftSection={<QrCode20Regular />} disabled={readOnly} onClick={() => openEnroll(row.username)}>
                            {t('Enroll device')}
                        </Menu.Item>
                        <Menu.Item leftSection={<Certificate20Regular />} onClick={() => setCertificatesUsername(row.username)}>
                            {t('Certificates')}
                        </Menu.Item>
                        <Menu.Item leftSection={<ArrowUpload20Regular />} disabled={readOnly} onClick={() => openSendFile(row.username)}>
                            {t('Send file')}
                        </Menu.Item>
                        <Menu.Divider />
                        <Menu.Item
                            color="red"
                            leftSection={<PersonDelete20Regular />}
                            disabled={isSelf || readOnly}
                            onClick={() => {
                                setUsername(row.username);
                                setShowDeleteUser(true);
                            }}
                        >
                            {isSelf ? t("Delete user (can't delete yourself)") : t('Delete user')}
                        </Menu.Item>
                        <Menu.Item
                            color="red"
                            leftSection={<Eraser20Regular />}
                            disabled={lockedForCaller || readOnly}
                            onClick={() => openClearContent(row)}
                        >
                            {t('Remote clear content')}
                        </Menu.Item>
                    </Menu.Dropdown>
                </Menu>
            </Group>
        );
    }

    return (
        <>
            <PageHeader
                title={t('Users')}
                description={t('{{count}} users', { count: userCount })}
                actions={(
                    <>
                        <Button variant="default" onClick={() => downloadPlugin()} leftSection={<ArrowDownload20Regular />}>{t('Remote Clear plugin')}</Button>
                        <ReadOnlyTip>
                            <Button disabled={readOnly} onClick={() => { setAddUserOpen(true); }} leftSection={<PersonAdd20Regular />}>{t('Add User')}</Button>
                        </ReadOnlyTip>
                    </>
                )}
            />

            <ReadOnlyNotice />

            <Group mb="sm" gap="xs">
                <Select
                    placeholder={t('All users')}
                    clearable
                    w={220}
                    leftSection={<Filter20Regular style={{ width: 16, height: 16 }} />}
                    value={activeFilterId}
                    onChange={setActiveFilterId}
                    data={userFilters.map((f) => ({ value: String(f.id), label: f.name }))}
                />
                <ReadOnlyTip>
                    <Button variant="subtle" disabled={readOnly} onClick={() => { setShowManageFilters(true); }}>{t('Manage Filters')}</Button>
                </ReadOnlyTip>
                <Switch
                  ml="auto"
                  label={t('Show federated accounts')}
                  checked={showFederated}
                  onChange={(e) => setShowFederated(e.currentTarget.checked)}
                />
            </Group>

            <Panel p={0}>
                <DataTable
                    {...TABLE_COLORS}
                    highlightOnHover
                    horizontalSpacing="xs"
                    scrollAreaProps={{ type: 'auto', offsetScrollbars: true }}
                    storeColumnsKey="users-table-columns"
                    defaultColumnProps={{ resizable: true }}
                    records={users}
                    idAccessor="username"
                    columns={[
                        {
                            accessor: 'username',
                            title: t('Username'),
                            sortable: true,
                            render: (row) => <Text size="sm">{row.username}</Text>,
                        },
                        {
                            accessor: 'filters',
                            title: t('Filters'),
                            render: (row) => (
                                <TagsInput
                                    variant="unstyled"
                                    readOnly={readOnly}
                                    placeholder={readOnly ? '' : t('Add to filter…')}
                                    data={userFilters.map((f) => f.name)}
                                    value={filterNamesFor(row.username)}
                                    onChange={(names) => setUserFilterTags(row.username, names)}
                                />
                            ),
                        },
                        {
                            accessor: 'callsign',
                            title: t('Callsign'),
                            render: (row) => {
                                const callsigns = callsignsOf(row);
                                return callsigns.length
                                    ? <Text size="sm">{callsigns.join(', ')}</Text>
                                    : <Text size="sm" c="dimmed">—</Text>;
                            },
                        },
                        {
                            accessor: 'role',
                            title: t('Role'),
                            render: (row) => (row.username === currentUsername || isProtectedUser(row.username)
                                ? roleLabel(roleOf(row))
                                : (
                                    <Group gap={4} wrap="nowrap">
                                        <Select
                                            disabled={readOnly}
                                            value={roleOf(row)}
                                            onChange={(_value, option) => { if (option) {changeRole(row.username, option.value);} }}
                                            data={ROLE_OPTIONS}
                                            allowDeselect={false}
                                            placeholder="Role"
                                            w={170}
                                        />
                                        {roleOf(row) === 'admin_viewer' && (
                                            <Tooltip label={readOnly ? READ_ONLY_LABEL : t('Choose which admin pages they can see')}>
                                                <ActionIcon variant="subtle" disabled={readOnly} aria-label={t('Admin areas')} onClick={() => setAreasUser(row)}>
                                                    <Eye20Regular />
                                                </ActionIcon>
                                            </Tooltip>
                                        )}
                                    </Group>
                                )),
                        },
                        {
                            accessor: 'active',
                            title: t('Active'),
                            render: (row) => (
                                <Switch
                                    disabled={readOnly || row.username === currentUsername || isProtectedUser(row.username)}
                                    checked={row.active}
                                    onChange={(e) => setActive(row.username, e.target.checked)}
                                />
                            ),
                        },
                        {
                            accessor: 'site_access',
                            title: t('Website Access'),
                            render: (row) => (
                                <Tooltip label={t('Controls access to this website only — EUDs and TAK clients are unaffected')}>
                                    <Switch
                                        disabled={readOnly || row.username === currentUsername || isProtectedUser(row.username)}
                                        checked={row.site_access}
                                        onChange={(e) => setSiteAccess(row.username, e.target.checked)}
                                    />
                                </Tooltip>
                            ),
                        },
                        {
                            accessor: 'current_login_ip',
                            title: t('Last IP'),
                            // The server refuses read-only admins a sort by IP: it would leak the order
                            sortable: !readOnly,
                            render: (row: User) => <Text ff="monospace" size="sm">{row.current_login_ip ?? '—'}</Text>,
                        },
                        {
                            accessor: 'actions',
                            title: t('Actions'),
                            textAlign: 'right',
                            render: renderActions,
                        },
                    ]}
                    sortStatus={sortStatus}
                    onSortStatusChange={setSortStatus}
                    fetching={loading}
                    minHeight={180}
                />
            </Panel>
            <div ref={loadMoreSentinelRef} />
            <Text size="xs" c="dimmed" ta="center" mt="xs">
                {loadingMore
                    ? t('Loading more users…')
                    : hasMore
                        ? t('Showing {{shown}} of {{total}} users — scroll for more', { shown: users.length, total: userCount })
                        : t('{{count}} users', { count: userCount })}
            </Text>

            <Modal opened={showManageFilters} onClose={() => setShowManageFilters(false)} title={t('Manage Filters')}>
                <Group align="flex-end" gap="sm" wrap="nowrap" mb="md">
                    <TextInput
                        style={{ flex: 1 }}
                        label={t('New filter name')}
                        value={newFilterName}
                        onChange={(e) => setNewFilterName(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') { createFilter(); } }}
                    />
                    <Button leftSection={<Add20Regular />} onClick={createFilter} disabled={!newFilterName.trim()}>{t('Create')}</Button>
                </Group>
                {userFilters.length === 0 ? (
                    <Text size="sm" c="dimmed">{t('No filters yet — create one above.')}</Text>
                ) : userFilters.map((f) => (
                    <Group
                        key={f.id}
                        gap="sm"
                        wrap="nowrap"
                        py={6}
                        style={{ borderTop: '1px solid var(--raven-border)' }}
                    >
                        <Text fw={600} style={{ flex: 1 }}>
                            {f.name}
                            <Text component="span" size="xs" c="dimmed" ml={6}>
                                {t('({{count}} members)', { count: f.usernames.length })}
                            </Text>
                        </Text>
                        <Tooltip label={t('Delete filter')}>
                            <ActionIcon color="red" variant="subtle" aria-label={t('Delete filter')} onClick={() => deleteFilter(f.id)}>
                                <Delete20Regular />
                            </ActionIcon>
                        </Tooltip>
                    </Group>
                ))}
            </Modal>

            <Modal size="lg" opened={showManageGroups} onClose={() => setShowManageGroups(false)} title={`Manage Groups for ${username}`}>
                <Stack gap="md">
                    <GroupPicker
                        title={`${t("Direction")}: ${t("Tx (send)")}`}
                        groups={allGroups}
                        value={picked.IN}
                        onChange={pick('IN')}
                        onAdd={() => joinPickedGroups('IN')}
                    />
                    <GroupPicker
                        title={`${t("Direction")}: ${t("Rx (receive)")}`}
                        groups={allGroups}
                        value={picked.OUT}
                        onChange={pick('OUT')}
                        onAdd={() => joinPickedGroups('OUT')}
                    />
                    <Text fw={600} mt="sm">{t("Memberships")}</Text>
                    <DataTable
                        {...TABLE_COLORS}
                        withTableBorder
                        highlightOnHover
                        storeColumnsKey="users-memberships-table-columns"
                        defaultColumnProps={{ resizable: true }}
                        records={memberships}
                        idAccessor="group_name"
                        columns={[
                            { accessor: 'group_name', title: t('Group Name') },
                            {
                                accessor: 'direction',
                                title: t('Direction'),
                                render: (row) => directionLabel(row.directions),
                            },
                            {
                                accessor: 'active',
                                title: t('Active'),
                                render: (row) => (
                                    <Tooltip refProp="rootRef" label={t('Turned on or off by the user from their device')}>
                                        <Switch checked={row.active} readOnly />
                                    </Tooltip>
                                ),
                            },
                            {
                                accessor: 'actions',
                                title: '',
                                textAlign: 'right',
                                render: (row) => (
                                    <Button
                                        size="xs"
                                        color="red"
                                        variant="subtle"
                                        leftSection={<PersonDelete20Regular style={{ width: 16, height: 16 }} />}
                                        onClick={() => { row.directions.forEach((direction) => removeUserFromGroup(username, row.group_name, direction)); }}
                                    >Remove</Button>
                                ),
                            },
                        ]}
                        minHeight={120}
                    />
                </Stack>
            </Modal>

            <Modal opened={addUserOpen} onClose={() => setAddUserOpen(false)} title={t("Add User")}>
                <Stack gap="md">
                    <TextInput required label={t("Username")} onChange={e => { setUsername(e.target.value); }} />
                    <PasswordInput
                        label={t("Password")}
                        required
                        onChange={(e) => setPassword(e.target.value)}
                        value={password}
                    />
                    <PasswordInput
                        label={t("Confirm Password")}
                        required
                        onChange={(e) => setConfirmPassword(e.target.value)}
                        value={confirm_password}
                    />
                    <Select
                        label={t("Role")}
                        data={ROLE_OPTIONS}
                        defaultValue="user"
                        allowDeselect={false}
                        onChange={(_value, option) => { setRole(option.value); }}
                    />
                    <Group justify="flex-end" mt="xs">
                        <Button variant="default" onClick={() => setAddUserOpen(false)}>{t('Cancel')}</Button>
                        <Button leftSection={<PersonAdd20Regular />} onClick={(e) => { addUser(e); }}>{t('Add User')}</Button>
                    </Group>
                </Stack>
            </Modal>

            <UserCertificatesModal
                username={certificatesUsername}
                opened={!!certificatesUsername}
                onClose={() => setCertificatesUsername('')}
            />

            <Modal opened={showDeleteUser} onClose={() => setShowDeleteUser(false)} title={t('Are you sure?')}>
                <Text mb="md">{t('Delete {{username}}? This can\'t be undone.', { username })}</Text>
                <Group justify="flex-end">
                    <Button variant="default" onClick={() => setShowDeleteUser(false)}>{t('Cancel')}</Button>
                    <Button color="red" onClick={confirmDeleteUser}>{t('Delete')}</Button>
                </Group>
            </Modal>

            <Modal opened={showClearContent} onClose={() => setShowClearContent(false)} title={t('Remote clear content')}>
                <Stack gap="md">
                    <Text fw={600} c="red">{t('This permanently wipes ATAK content on {{username}}\'s devices.', { username: clearContentUsername })}</Text>
                    <Text size="sm">{t('A signed command is sent to this user\'s online devices that have the C4 Raven Remote Clear plugin installed (ATAK Android only). Each device clears its local ATAK data and the app exits. This cannot be undone. Devices that are offline or without the plugin are unaffected. Server-side data is not deleted.')}</Text>
                    <Checkbox
                        checked={clearMaps}
                        onChange={(e) => setClearMaps(e.currentTarget.checked)}
                        label={t('Also clear downloaded maps & imagery')}
                    />
                    <Checkbox
                        checked={queueOffline}
                        onChange={(e) => setQueueOffline(e.currentTarget.checked)}
                        label={t('Queue: deliver on each device\'s next connection')}
                        description={queueOffline
                            ? t('The command is stored for every device and delivered the next time each one connects. Devices that are online now are reconnected so they receive it immediately.')
                            : t('Only devices online right now will be wiped; offline devices are unaffected.')}
                    />
                    <Checkbox
                        checked={clearAck}
                        onChange={(e) => setClearAck(e.currentTarget.checked)}
                        label={t('I understand this is irreversible and wipes the device.')}
                    />
                    <TextInput
                        label={clearContentCallsigns.length
                            ? t('Type a device callsign to confirm: {{callsigns}}', { callsigns: clearContentCallsigns.join(', ') })
                            : t('This user has no device callsign; type the username "{{username}}" to confirm', { username: clearContentUsername })}
                        placeholder={clearContentCallsigns[0] ?? clearContentUsername}
                        value={clearConfirm}
                        onChange={(e) => setClearConfirm(e.currentTarget.value)}
                    />
                    <Group justify="flex-end">
                        <Button variant="default" onClick={() => setShowClearContent(false)}>{t('Cancel')}</Button>
                        <Button
                            color="red"
                            loading={clearing}
                            disabled={!clearConfirmValid()}
                            leftSection={<Eraser20Regular />}
                            onClick={() => clearContent()}
                        >{t('Wipe device')}</Button>
                    </Group>
                </Stack>
            </Modal>

            <Modal
                opened={tempPasswordInfo !== null}
                onClose={() => setTempPasswordInfo(null)}
                title={`Temporary password for ${tempPasswordInfo?.username}`}
            >
                <Text size="sm" c="dimmed" mb="md">
                    {t('Give this to the user directly. It only works once — they\'ll be forced to set their own password immediately after logging in.')}
                </Text>
                <Group justify="center" gap="xs">
                    <Text size="xl" fw={700} ff="monospace">{tempPasswordInfo?.password}</Text>
                    <CopyButton value={tempPasswordInfo?.password ?? ''}>
                        {({ copied, copy }) => (
                            <Tooltip label={copied ? t('Copied') : t('Copy')}>
                                <ActionIcon variant="subtle" onClick={copy} aria-label={t('Copy')}>
                                    {copied ? <Checkmark20Regular /> : <Copy20Regular />}
                                </ActionIcon>
                            </Tooltip>
                        )}
                    </CopyButton>
                </Group>
            </Modal>

            <Modal
                opened={showSendFile}
                onClose={() => { setShowSendFile(false); setSendFileFile(null); }}
                title={`${t('Send File to')} ${sendFileUsername}`}
            >
                <Text size="sm" c="dimmed" mb="md">
                    {t('The file will be pushed to every TAK device this user is signed into.')}
                </Text>
                <Group justify="space-between">
                    <FileButton onChange={setSendFileFile}>
                        {(props) => <Button variant="default" {...props}>{sendFileFile ? sendFileFile.name : t('Choose File')}</Button>}
                    </FileButton>
                    <Button
                        disabled={!sendFileFile}
                        loading={sendingFile}
                        leftSection={<ArrowUpload20Regular />}
                        onClick={() => { sendFileToUser(); }}
                    >{t('Send')}</Button>
                </Group>
            </Modal>

            <Modal
                opened={showJoinQr}
                onClose={() => setShowJoinQr(false)}
                title={`${t('Enroll')} ${joinQrUsername}`}
                size="lg"
            >
                <Stack gap="md">
                    <SegmentedControl
                        fullWidth
                        value={enrollMode}
                        onChange={setEnrollMode}
                        data={[
                            { value: 'qr', label: t('Auto-enroll (QR code)') },
                            { value: 'zip', label: t('Certificate package (.zip)') },
                        ]}
                    />
                    <NumberInput
                        label={t('Certificate valid for (days)')}
                        description={certDaysLimits ? `${t('Leave empty for the server default')} (${certDaysLimits.default_days} ${t('days')})` : undefined}
                        placeholder={certDaysLimits ? `${certDaysLimits.default_days}` : undefined}
                        min={1}
                        max={certDaysLimits?.max_days}
                        allowDecimal={false}
                        allowNegative={false}
                        clampBehavior="strict"
                        // Until the stored value loads, an edit would be overwritten by it
                        disabled={!certDaysLimits}
                        value={certDays}
                        onChange={setCertDays}
                    />
                    {enrollMode === 'qr' ? (
                        <Tabs value={joinQrTab} onChange={setJoinQrTab}>
                            <Tabs.List mb="md" grow>
                                <Tabs.Tab value="domain" leftSection={<Globe20Regular />}>
                                    {t('Connect by domain')}
                                </Tabs.Tab>
                                <Tabs.Tab value="vpn" leftSection={<NetworkCheck20Regular />}>
                                    {t('Connect over VPN / IP')}
                                </Tabs.Tab>
                            </Tabs.List>
                            <Tabs.Panel value="domain">
                                <Text size="sm" c="dimmed" mb="md">
                                    {t('Scan this in ATAK or WinTAK to add the server and enroll this user automatically. Treat it like a password and don\'t share it with anyone.')}
                                </Text>
                                <Stack align="center" gap="xs">
                                    {joinQrString !== '' && (
                                        // QR codes need a white quiet zone in both themes to scan.
                                        <Box p="md" bg="#fff" style={{ border: '1px solid var(--raven-border)', borderRadius: 'var(--mantine-radius-md)' }}>
                                            <QRCode value={joinQrString} size={280} quietZone={10} ecLevel="L" qrStyle="squares" />
                                        </Box>
                                    )}
                                    <Button
                                        variant={joinQrString !== '' ? 'subtle' : 'filled'}
                                        size={joinQrString !== '' ? 'xs' : 'sm'}
                                        leftSection={<ArrowClockwise20Regular />}
                                        onClick={() => generateJoinQr(joinQrUsername)}
                                    >
                                        {joinQrString !== '' ? t('Regenerate') : t('Generate')}
                                    </Button>
                                </Stack>
                            </Tabs.Panel>
                            <Tabs.Panel value="vpn">
                                {showJoinQr && joinQrUsername !== '' && (
                                    <VpnQrPanel username={joinQrUsername} beforeGenerate={saveCertDays} />
                                )}
                            </Tabs.Panel>
                        </Tabs>
                    ) : (
                        <Stack gap="sm">
                            <Text size="sm" c="dimmed">
                                {t('Contains the server truststore, the user\'s certificate and a manifest. Import it in ATAK or WinTAK. It holds the user\'s private key, so treat it like a password.')}
                            </Text>
                            <Group justify="flex-end">
                                <Button
                                    leftSection={<FolderZip20Regular />}
                                    loading={generatingCertPackage}
                                    onClick={() => downloadCertPackage()}
                                >
                                    {t('Generate & download')}
                                </Button>
                            </Group>
                        </Stack>
                    )}
                </Stack>
            </Modal>
            <AdminAreasModal
                user={areasUser}
                onClose={() => setAreasUser(null)}
                onSaved={() => getUsers(1, true)}
            />
        </>
    );
}
