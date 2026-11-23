import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
    ActionIcon,
    Badge,
    Box,
    Group,
    Menu,
    Modal,
    Select,
    Stack,
    Text,
    TextInput,
    Tooltip,
    Button,
    TagsInput,
    Switch,
} from '@mantine/core';
import {
    ArrowRight16Regular,
    ArrowSwap16Regular,
    ArrowSwap20Regular,
    Certificate20Regular,
    Delete20Regular,
    Dismiss16Regular,
    Filter12Regular,
    Flowchart20Regular,
    Grid20Regular,
    Key20Regular,
    Live20Regular,
    Organization20Regular,
    ScaleFit20Regular,
    Server20Regular,
    ZoomIn20Regular,
    ZoomOut20Regular,
} from '@fluentui/react-icons';
import { t } from 'i18next';
import {
    autoLayout,
    edgeCurve,
    entityMeta,
    entityName,
    fitView,
    pairEdges,
    partnerAliases,
    type MapEdge,
    type MapKind,
    type MapStatus,
    type XY,
} from './federationMap';

export interface DiagramEntity {
    id: string;
    type: string;
    displayName?: string;
    name?: string;
    description?: string | null;
    config?: Record<string, any>;
}

// Matches Federation Hub's actual GroupsFilterType enum values exactly (decompiled
// from the native admin panel's JS bundle) -- these are NOT the same casing/words
// as anywhere else in this app, and a mismatched value here is silently ignored by
// the broker rather than erroring, so a rule can look configured in this UI while
// doing nothing (or the wrong thing) for real traffic.
export interface DiagramRuleFilter {
    groupsFilterType: 'allGroups' | 'allowed' | 'disallowed' | 'allowedAndDisallowed';
    allowedGroups: string[];
    disallowedGroups: string[];
}

export interface DiagramRule {
    id: string;
    name: string;
    source: string;
    destination: string;
    filter: DiagramRuleFilter;
}

/**
 * What the page knows about a node beyond the policy itself: a friendly name,
 * the partner kind, its live status, and whether it is this server's own node.
 * Everything is optional; the map falls back to the policy and live connections.
 */
export interface DiagramNodeInfo {
    label?: string | null;
    kind?: MapKind;
    status?: MapStatus;
    isOwn?: boolean;
}

// "nodes" is typed loosely on purpose -- Federation Hub stores this as an
// arbitrary blob and doesn't reliably hand back an array (see viewNodesArray).
interface GraphView { nodes?: unknown; settings?: { zoom?: number; canvas_x?: number; canvas_y?: number } }

const NODE_WIDTH = 188;
const NODE_HEIGHT = 58;
const BOARD_HEIGHT_READ_ONLY = 420;
const BOARD_HEIGHT_EDIT = 520;
const MIN_ZOOM = 0.3;
const MAX_ZOOM = 2.5;
/** Fit never enlarges past 100%: a small map stays at its natural size, centred. */
const FIT_MAX_ZOOM = 1;
/** Room for the legend and zoom controls along the bottom edge. */
const FIT_INSETS = { top: 36, right: 40, bottom: 64, left: 40 };
/** Where a freshly auto-arranged editable layout is anchored, so stored coordinates stay positive. */
const LAYOUT_ORIGIN: XY = { x: 560, y: 300 };

const EMPTY_FILTER: DiagramRuleFilter = { groupsFilterType: 'allGroups', allowedGroups: [], disallowedGroups: [] };

// bidirectional is a UI-only convenience on the add-rule form -- it isn't a
// real field in the saved rule, saving with it checked just creates a second
// mirrored rule alongside the first.
type RuleFormState = DiagramRule & { bidirectional?: boolean };

function filterTypeLabel(type: DiagramRuleFilter['groupsFilterType']): string {
    switch (type) {
        case 'allowed': return t('Allowed only');
        case 'disallowed': return t('Except disallowed');
        case 'allowedAndDisallowed': return t('Allowed, except disallowed');
        default: return t('All groups');
    }
}

function isFiltered(rule: DiagramRule): boolean {
    return (rule.filter?.groupsFilterType || 'allGroups') !== 'allGroups';
}

function filterDetail(rule: DiagramRule): string {
    const f = rule.filter ?? EMPTY_FILTER;
    const parts = [filterTypeLabel(f.groupsFilterType || 'allGroups')];
    if (f.allowedGroups?.length && (f.groupsFilterType === 'allowed' || f.groupsFilterType === 'allowedAndDisallowed')) {
        parts.push(`${t('allowed')}: ${f.allowedGroups.join(', ')}`);
    }
    if (f.disallowedGroups?.length && (f.groupsFilterType === 'disallowed' || f.groupsFilterType === 'allowedAndDisallowed')) {
        parts.push(`${t('blocked')}: ${f.disallowedGroups.join(', ')}`);
    }
    return parts.join(' · ');
}

// Federation Hub stores "views" as an arbitrary JsonNode blob and appears to
// normalize an empty array to `{}` on its own round-trip (confirmed live: we
// saved nodes: [] and a subsequent GET came back with nodes: {}) -- so never
// assume it's still an array on the way back in.
function viewNodesArray(nodes: unknown): { id: string; x: number; y: number }[] {
    return Array.isArray(nodes) ? nodes : [];
}

// Loosely typed on purpose -- this only needs whatever identifies which
// entity a live connection belongs to, not the connection table's full shape.
export interface DiagramConnection {
    connectionId?: string;
    remoteServerId?: string;
    groupIdentities?: string[];
}

interface Props {
    entities: DiagramEntity[];
    rules: DiagramRule[];
    views?: GraphView;
    knownGroups: string[];
    connections?: DiagramConnection[];
    onRulesChange?: (rules: DiagramRule[]) => void;
    onViewsChange?: (views: GraphView) => void;
    /** Friendly names, kinds, live status and "this server" for nodes (see DiagramNodeInfo). */
    nodeInfo?: (entity: DiagramEntity) => DiagramNodeInfo | null | undefined;
    /**
     * View-only map: nodes can still be dragged and the board zoomed for a
     * better look, but nothing is saved and rules can't be added, edited or
     * removed. Partner nodes are laid out around this server automatically, and
     * a partner's CA and outgoing nodes are drawn as the one partner they are.
     */
    readOnly?: boolean;
}

const STATUS_COLOR: Record<MapStatus, string> = {
    connected: 'var(--mantine-color-green-6)',
    waiting: 'var(--mantine-color-yellow-6)',
    down: 'var(--mantine-color-red-6)',
    off: 'var(--mantine-color-gray-5)',
};

function statusLabel(s: MapStatus): string {
    switch (s) {
        case 'connected': return t('Connected');
        case 'waiting': return t('Waiting');
        case 'down': return t('Down');
        default: return t('Turned off');
    }
}

function kindLabel(kind: MapKind): string {
    return kind === 'raven_hub' ? t('Raven') : t('TAK Server');
}

function StatusDot({ status, size = 9 }: { status: MapStatus; size?: number }) {
    return (
        <span
            aria-hidden
            style={{
                display: 'inline-block',
                flexShrink: 0,
                width: size,
                height: size,
                borderRadius: '50%',
                background: status === 'off' ? 'transparent' : STATUS_COLOR[status],
                border: status === 'off' ? `1.5px solid ${STATUS_COLOR.off}` : undefined,
                boxShadow: status === 'connected' ? '0 0 0 3px color-mix(in srgb, var(--mantine-color-green-6) 22%, transparent)' : undefined,
            }}
        />
    );
}

type EdgeState = 'live' | 'idle' | 'off' | 'unknown';

const EDGE_COLOR: Record<EdgeState | 'accent', string> = {
    live: 'var(--mantine-color-green-6)',
    idle: 'var(--raven-text-muted)',
    unknown: 'var(--raven-text-muted)',
    off: 'var(--raven-border-strong)',
    accent: 'var(--raven-accent)',
};

interface NodeView {
    id: string;
    entity: DiagramEntity;
    name: string;
    sub: string;
    kind?: MapKind;
    status: MapStatus | null;
    isOwn: boolean;
    icon: React.ReactNode;
    /** Mantine colour for the icon tile; null = this server's accent tile. */
    tone: string | null;
}

/** Card layout for a node; the tooltip carries the full name when the card truncates it. */
function NodeCard({ node, selected, hovered, dimmed, readOnly, dragging, onPointerDown, onClick, onEnter, onLeave, pos }: {
    node: NodeView;
    selected: boolean;
    hovered: boolean;
    dimmed: boolean;
    readOnly: boolean;
    dragging: boolean;
    pos: XY;
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>) => void;
    onClick: () => void;
    onEnter: () => void;
    onLeave: () => void;
}) {
    const ring = selected
        ? '0 0 0 3px color-mix(in srgb, var(--raven-accent) 35%, transparent)'
        : node.isOwn ? '0 0 0 1px var(--raven-accent)' : null;
    const lift = hovered ? '0 4px 14px rgba(0, 0, 0, 0.14)' : '0 1px 2px rgba(0, 0, 0, 0.06)';
    return (
        <Tooltip
            disabled={dragging}
            openDelay={350}
            withArrow
            position="bottom"
            label={(
                <Stack gap={2}>
                    <Text size="sm" fw={600} style={{ wordBreak: 'break-word' }}>{node.name}</Text>
                    <Text size="xs" c="dimmed">{node.sub}</Text>
                    {node.status && (
                        <Group gap={6} wrap="nowrap">
                            <StatusDot status={node.status} size={8} />
                            <Text size="xs">{statusLabel(node.status)}</Text>
                        </Group>
                    )}
                    {!readOnly && <Text size="xs" c="dimmed">{selected ? t('Click another node to draw a rule to it') : t('Click to start a rule from here')}</Text>}
                </Stack>
            )}
            multiline
            maw={280}
        >
            <Box
                role={readOnly ? undefined : 'button'}
                tabIndex={readOnly ? undefined : 0}
                aria-label={readOnly ? undefined : `${node.name}, ${node.sub}${node.status ? `, ${statusLabel(node.status)}` : ''}`}
                aria-pressed={readOnly ? undefined : selected}
                data-map-node={node.id}
                pos="absolute"
                left={pos.x}
                top={pos.y}
                w={NODE_WIDTH}
                h={NODE_HEIGHT}
                style={{
                    cursor: dragging ? 'grabbing' : readOnly ? 'grab' : 'pointer',
                    userSelect: 'none',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 10,
                    padding: '0 10px',
                    borderRadius: 'var(--mantine-radius-md)',
                    background: node.isOwn
                        ? 'color-mix(in srgb, var(--raven-accent) 7%, var(--raven-surface))'
                        : 'var(--raven-surface)',
                    border: `1px solid ${node.isOwn || selected ? 'var(--raven-accent)' : hovered ? 'var(--raven-text-muted)' : 'var(--raven-border-strong)'}`,
                    boxShadow: ring ? `${ring}, ${lift}` : lift,
                    opacity: dimmed ? 0.42 : 1,
                    transition: 'opacity 120ms linear, box-shadow 120ms linear, border-color 120ms linear',
                    zIndex: hovered || selected ? 2 : 1,
                    outlineOffset: 3,
                }}
                onPointerDown={onPointerDown}
                onPointerEnter={onEnter}
                onPointerLeave={onLeave}
                onClick={(ev: React.MouseEvent) => { ev.stopPropagation(); onClick(); }}
                onKeyDown={(ev: React.KeyboardEvent) => {
                    if (!readOnly && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); onClick(); }
                }}
            >
                <Box
                    aria-hidden
                    style={{
                        flexShrink: 0,
                        width: 34,
                        height: 34,
                        borderRadius: 'var(--mantine-radius-sm)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        background: node.tone ? `var(--mantine-color-${node.tone}-light)` : 'var(--raven-accent)',
                        color: node.tone ? `var(--mantine-color-${node.tone}-light-color)` : 'var(--mantine-primary-color-contrast)',
                    }}
                >
                    {node.icon}
                </Box>
                <Stack gap={1} style={{ minWidth: 0, flex: 1 }}>
                    <Text size="sm" fw={600} truncate style={{ lineHeight: 1.25 }}>{node.name}</Text>
                    <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
                        {node.status && <StatusDot status={node.status} size={8} />}
                        <Text size="xs" c="dimmed" truncate style={{ lineHeight: 1.3 }}>{node.sub}</Text>
                    </Group>
                </Stack>
            </Box>
        </Tooltip>
    );
}

export default function FederationPolicyDiagram({ entities, rules, views, knownGroups, connections = [], onRulesChange = () => {}, onViewsChange, nodeInfo, readOnly = false }: Props) {
    const markerPrefix = `fp${React.useId().replace(/[^a-zA-Z0-9]/g, '')}`;

    // An entity counts as live if any current connection names it -- either
    // as the remote server itself (FederationOutgoing) or as one of the
    // group identities it authenticated with (FederationGroup/CA). This is
    // the same "is it actually connected right now" signal the Connections
    // table already lists; here it's just projected onto the nodes and edges.
    const liveNames = useMemo(() => {
        const names = new Set<string>();
        connections.forEach((c) => {
            if (c.remoteServerId) { names.add(c.remoteServerId); }
            (c.groupIdentities ?? []).forEach((g) => names.add(g));
        });
        return names;
    }, [connections]);
    const isEntityLive = useCallback((entity: DiagramEntity) => {
        const name = entity.name;
        if (!name) { return false; }
        if (liveNames.has(name)) { return true; }
        return entity.type === 'FederationOutgoing' && connections.some((c) => !!c.connectionId && c.connectionId.startsWith(name));
    }, [liveNames, connections]);

    // Read-only: a partner's outgoing node folds into its CA node (one partner, one card).
    const aliases = useMemo(() => (readOnly ? partnerAliases(entities) : {}), [readOnly, entities]);

    const nodes: NodeView[] = useMemo(() => {
        const mergedInto: Record<string, DiagramEntity[]> = {};
        entities.forEach((e) => {
            const target = aliases[e.id];
            if (target) { (mergedInto[target] ??= []).push(e); }
        });
        return entities.filter((e) => !aliases[e.id]).map((e) => {
            const info = nodeInfo?.(e) ?? undefined;
            const meta = entityMeta(e);
            const kind = info?.kind ?? meta.kind;
            const isOwn = !!info?.isOwn;
            const group = [e, ...(mergedInto[e.id] ?? [])];
            let status: MapStatus | null = null;
            if (e.config?.enabled === false) {
                status = 'off';
            } else if (info?.status) {
                status = info.status;
            } else if (connections.length > 0) {
                status = group.some(isEntityLive) ? 'connected' : 'waiting';
            }
            const isOutgoing = e.type === 'FederationOutgoing';
            const isToken = e.type === 'FederationTokenGroup';
            let typeText = isOutgoing ? t('Outgoing connection') : isToken ? t('Token group') : t('CA group');
            if (kind && !readOnly) { typeText = isOutgoing ? `${t('Outgoing')} · ${kindLabel(kind)}` : `${t('CA')} · ${kindLabel(kind)}`; }
            const sub = isOwn
                ? t('This Raven')
                : readOnly ? (kind ? kindLabel(kind) : t('Federation partner')) : typeText;
            const fallback = isOwn ? t('This Raven') : isOutgoing ? t('Outgoing connection') : t('Unnamed partner');
            const name = entityName(e, info?.label) || fallback;
            let icon: React.ReactNode;
            let tone: string | null;
            if (isOwn) { icon = <Organization20Regular />; tone = null; }
            else if (isOutgoing && !readOnly) { icon = <ArrowSwap20Regular />; tone = kind === 'other' ? 'cyan' : 'indigo'; }
            else if (kind === 'raven_hub') { icon = <Organization20Regular />; tone = 'indigo'; }
            else if (kind === 'other') { icon = <Live20Regular />; tone = 'cyan'; }
            else if (kind === 'tak_server') { icon = <Server20Regular />; tone = 'gray'; }
            else if (isOutgoing) { icon = <ArrowSwap20Regular />; tone = 'indigo'; }
            else if (isToken) { icon = <Key20Regular />; tone = 'grape'; }
            else { icon = <Certificate20Regular />; tone = 'gray'; }
            return { id: e.id, entity: e, name, sub, kind, status, isOwn, icon, tone };
        });
    }, [entities, aliases, nodeInfo, connections.length, isEntityLive, readOnly]);

    const nodeById = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);
    const ownId = nodes.find((n) => n.isOwn)?.id ?? null;
    const visibleIds = nodes.map((n) => n.id);
    const visibleKey = visibleIds.join(',');
    const edges = useMemo(() => pairEdges(rules, new Set(visibleIds), aliases), [rules, visibleKey, aliases]); // eslint-disable-line react-hooks/exhaustive-deps

    const hasStoredZoom = !!views?.settings && typeof views.settings.zoom === 'number'
        && !(views.settings.zoom === 1 && !views.settings.canvas_x && !views.settings.canvas_y);

    const layoutFor = useCallback((ids: string[]) => {
        const auto = autoLayout(ids, ownId, NODE_WIDTH, NODE_HEIGHT);
        if (readOnly) { return auto; }
        const shifted: Record<string, XY> = {};
        Object.entries(auto).forEach(([id, p]) => { shifted[id] = { x: p.x + LAYOUT_ORIGIN.x, y: p.y + LAYOUT_ORIGIN.y }; });
        return shifted;
    }, [ownId, readOnly]);

    const [positions, setPositions] = useState<Record<string, XY>>(() => {
        if (readOnly) { return layoutFor(visibleIds); }
        const stored: Record<string, XY> = {};
        viewNodesArray(views?.nodes).forEach((n) => { stored[n.id] = { x: n.x, y: n.y }; });
        const anyStored = entities.some((e) => stored[e.id]);
        return anyStored ? { ...placeMissing(stored, entities.map((e) => e.id)) } : layoutFor(entities.map((e) => e.id));
    });

    const [zoom, setZoom] = useState(readOnly || !hasStoredZoom ? 1 : (views?.settings?.zoom || 1));
    const [pan, setPan] = useState<XY>(readOnly || !hasStoredZoom ? { x: 0, y: 0 } : { x: views?.settings?.canvas_x || 0, y: views?.settings?.canvas_y || 0 });
    const needsFit = useRef(readOnly || !hasStoredZoom);
    const [boardWidth, setBoardWidth] = useState(0);
    const [selectedNode, setSelectedNode] = useState<string | null>(null);
    const [hoveredNode, setHoveredNode] = useState<string | null>(null);
    const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
    const [dragging, setDragging] = useState<string | null>(null);
    const [edgeMenu, setEdgeMenu] = useState<{ x: number; y: number; edge: MapEdge<DiagramRule> } | null>(null);

    const [showRuleModal, setShowRuleModal] = useState(false);
    const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
    const [ruleForm, setRuleForm] = useState<RuleFormState>({ id: '', name: '', source: '', destination: '', filter: EMPTY_FILTER, bidirectional: false });

    const boardRef = useRef<HTMLDivElement>(null);
    const dragRef = useRef<{ id: string; offsetX: number; offsetY: number; startX: number; startY: number; moved: boolean } | null>(null);
    const suppressClick = useRef(false);
    const panDragRef = useRef<{ startClientX: number; startClientY: number; startPanX: number; startPanY: number } | null>(null);
    const positionsRef = useRef(positions);
    positionsRef.current = positions;
    const panRef = useRef(pan);
    panRef.current = pan;
    const zoomRef = useRef(zoom);
    zoomRef.current = zoom;

    const boardHeight = readOnly ? BOARD_HEIGHT_READ_ONLY : BOARD_HEIGHT_EDIT;

    // The link layer only needs to cover the nodes (plus room for curves and labels). A
    // huge fixed SVG is slow: the browser repaints the whole layer on every pan, zoom and hover.
    const svgBox = useMemo(() => {
        const pts = Object.values(positions);
        const pad = 400;
        if (pts.length === 0) { return { x: -pad, y: -pad, w: 2 * pad, h: 2 * pad }; }
        const minX = Math.min(...pts.map((p) => p.x)) - pad;
        const minY = Math.min(...pts.map((p) => p.y)) - pad;
        const maxX = Math.max(...pts.map((p) => p.x)) + NODE_WIDTH + pad;
        const maxY = Math.max(...pts.map((p) => p.y)) + NODE_HEIGHT + pad;
        return { x: Math.floor(minX), y: Math.floor(minY), w: Math.ceil(maxX - minX), h: Math.ceil(maxY - minY) };
    }, [positions]);

    function persistViews(nextPositions: Record<string, XY>, nextZoom: number, nextPan: XY) {
        if (readOnly || !onViewsChange) { return; }
        onViewsChange({
            nodes: entities.map((e) => ({ id: e.id, x: nextPositions[e.id]?.x ?? 0, y: nextPositions[e.id]?.y ?? 0 })),
            settings: { zoom: nextZoom, canvas_x: nextPan.x, canvas_y: nextPan.y },
        });
    }

    const computeFit = useCallback((pos: Record<string, XY>) => {
        const width = boardRef.current?.clientWidth ?? 0;
        const pts = visibleIds.map((id) => pos[id]).filter(Boolean);
        return fitView(pts, NODE_WIDTH, NODE_HEIGHT, { width, height: boardHeight }, FIT_INSETS, { min: MIN_ZOOM, max: FIT_MAX_ZOOM });
    }, [visibleKey, boardHeight]); // eslint-disable-line react-hooks/exhaustive-deps

    // Track the board's width: fit once it is known, and keep a view-only map fitted as it resizes.
    useLayoutEffect(() => {
        const el = boardRef.current;
        if (!el) { return undefined; }
        setBoardWidth(el.clientWidth);
        const ro = new ResizeObserver(() => setBoardWidth(el.clientWidth));
        ro.observe(el);
        return () => ro.disconnect();
    }, [entities.length === 0]); // eslint-disable-line react-hooks/exhaustive-deps

    useLayoutEffect(() => {
        if (boardWidth <= 0 || (!needsFit.current && !readOnly)) { return; }
        const fit = computeFit(positionsRef.current);
        setZoom(fit.zoom);
        setPan(fit.pan);
        needsFit.current = false;
    }, [boardWidth, computeFit, readOnly]);

    // Nodes can be added/removed after mount (e.g. a new outgoing connection).
    // View-only: re-run the automatic layout. Editable: give new nodes a spot
    // (their stored one if the view has it) and keep every existing position.
    const entityIds = entities.map((e) => e.id).join(',');
    const layoutKey = `${visibleKey}|${ownId ?? ''}`;
    const firstLayout = useRef(true);
    useEffect(() => {
        if (firstLayout.current) { firstLayout.current = false; return; }
        if (readOnly) {
            const next = layoutFor(visibleIds);
            setPositions(next);
            if (boardRef.current?.clientWidth) {
                const fit = computeFit(next);
                setZoom(fit.zoom);
                setPan(fit.pan);
            }
            return;
        }
        setPositions((prev) => {
            const missing = entities.filter((e) => !prev[e.id]);
            if (missing.length === 0) { return prev; }
            const stored: Record<string, XY> = {};
            viewNodesArray(views?.nodes).forEach((n) => { stored[n.id] = { x: n.x, y: n.y }; });
            const next = { ...prev };
            missing.forEach((e) => { if (stored[e.id]) { next[e.id] = stored[e.id]; } });
            return placeMissing(next, entities.map((e) => e.id));
        });
    }, [readOnly ? layoutKey : entityIds]); // eslint-disable-line react-hooks/exhaustive-deps

    // Escape cancels a half-drawn rule.
    useEffect(() => {
        if (!selectedNode) { return undefined; }
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setSelectedNode(null); } };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [selectedNode]);

    function onNodePointerDown(e: React.PointerEvent<HTMLDivElement>, id: string) {
        const board = boardRef.current;
        if (!board || e.button !== 0) { return; }
        const rect = board.getBoundingClientRect();
        const pos = positionsRef.current[id];
        if (!pos) { return; }
        const p = panRef.current;
        dragRef.current = {
            id,
            offsetX: (e.clientX - rect.left - p.x) / zoomRef.current - pos.x,
            offsetY: (e.clientY - rect.top - p.y) / zoomRef.current - pos.y,
            startX: e.clientX,
            startY: e.clientY,
            moved: false,
        };
        e.currentTarget.setPointerCapture(e.pointerId);
        e.stopPropagation();
    }

    function onBackgroundPointerDown(e: React.PointerEvent<HTMLDivElement>) {
        if (e.target !== e.currentTarget) { return; }
        panDragRef.current = { startClientX: e.clientX, startClientY: e.clientY, startPanX: panRef.current.x, startPanY: panRef.current.y };
        e.currentTarget.setPointerCapture(e.pointerId);
    }

    function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
        const drag = dragRef.current;
        if (drag && boardRef.current) {
            if (!drag.moved && Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < 4) { return; }
            if (!drag.moved) { drag.moved = true; setDragging(drag.id); }
            const rect = boardRef.current.getBoundingClientRect();
            const p = panRef.current;
            const x = (e.clientX - rect.left - p.x) / zoomRef.current - drag.offsetX;
            const y = (e.clientY - rect.top - p.y) / zoomRef.current - drag.offsetY;
            setPositions((prev) => ({ ...prev, [drag.id]: { x, y } }));
            return;
        }
        if (panDragRef.current) {
            const { startClientX, startClientY, startPanX, startPanY } = panDragRef.current;
            setPan({ x: startPanX + (e.clientX - startClientX), y: startPanY + (e.clientY - startClientY) });
        }
    }

    function onPointerUp() {
        const drag = dragRef.current;
        if (drag) {
            dragRef.current = null;
            if (drag.moved) {
                // A drag ends in a click on the node: don't treat it as "start a rule here".
                suppressClick.current = true;
                setDragging(null);
                persistViews(positionsRef.current, zoomRef.current, panRef.current);
            }
        }
        if (panDragRef.current) {
            panDragRef.current = null;
            persistViews(positionsRef.current, zoomRef.current, panRef.current);
        }
    }

    function zoomBy(delta: number) {
        const current = zoomRef.current;
        const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round((current + delta) * 20) / 20));
        // Zoom around the board's centre rather than its corner.
        const width = boardRef.current?.clientWidth ?? 0;
        const c = { x: width / 2, y: boardHeight / 2 };
        const p = panRef.current;
        const nextPan = { x: c.x - ((c.x - p.x) * next) / current, y: c.y - ((c.y - p.y) * next) / current };
        setZoom(next);
        setPan(nextPan);
        persistViews(positionsRef.current, next, nextPan);
    }

    function fitToView() {
        const fit = computeFit(positionsRef.current);
        setZoom(fit.zoom);
        setPan(fit.pan);
        persistViews(positionsRef.current, fit.zoom, fit.pan);
    }

    function autoArrange() {
        const next = { ...positionsRef.current, ...layoutFor(entities.map((e) => e.id)) };
        setPositions(next);
        const fit = computeFit(next);
        setZoom(fit.zoom);
        setPan(fit.pan);
        persistViews(next, fit.zoom, fit.pan);
    }

    function center(id: string): XY {
        const p = positions[id] ?? { x: 0, y: 0 };
        return { x: p.x + NODE_WIDTH / 2, y: p.y + NODE_HEIGHT / 2 };
    }

    function entityLabel(id: string): string {
        const target = aliases[id] ?? id;
        const node = nodeById[target];
        if (node) { return node.name; }
        const e = entities.find((x) => x.id === id);
        return e ? (entityName(e, nodeInfo?.(e)?.label) || t('Unnamed partner')) : t('Missing node');
    }

    /** A rule end in the editor's list: the node's name, tagged when it is an outgoing connection. */
    function endpoint(id: string) {
        const e = entities.find((x) => x.id === id);
        return (
            <Group gap={6} wrap="nowrap" style={{ minWidth: 0 }}>
                <Text size="sm" fw={600} truncate>{entityLabel(id)}</Text>
                {e?.type === 'FederationOutgoing' && <Badge size="xs" variant="outline" color="gray" tt="none" style={{ flexShrink: 0 }}>{t('outgoing')}</Badge>}
            </Group>
        );
    }

    function handleNodeClick(id: string) {
        if (suppressClick.current) { suppressClick.current = false; return; }
        if (readOnly) { return; }
        if (!selectedNode) {
            setSelectedNode(id);
            return;
        }
        if (selectedNode === id) {
            setSelectedNode(null);
            return;
        }
        const source = selectedNode;
        setSelectedNode(null);
        openAddRule(source, id);
    }

    function openAddRule(source: string, destination: string) {
        setRuleForm({
            id: crypto.randomUUID(),
            name: `${entityLabel(source)} -> ${entityLabel(destination)}`,
            source,
            destination,
            filter: { ...EMPTY_FILTER },
            bidirectional: false,
        });
        setEditingRuleId(null);
        setShowRuleModal(true);
    }

    function openEditRule(rule: DiagramRule) {
        if (readOnly) { return; }
        setRuleForm({ ...rule, filter: rule.filter || { ...EMPTY_FILTER }, bidirectional: false });
        setEditingRuleId(rule.id);
        setShowRuleModal(true);
    }

    function saveRule() {
        const { bidirectional, ...rule } = ruleForm;
        if (editingRuleId) {
            onRulesChange(rules.map((r) => (r.id === rule.id ? rule : r)));
        } else if (bidirectional) {
            const reverse: DiagramRule = {
                ...rule,
                id: crypto.randomUUID(),
                name: `${entityLabel(rule.destination)} -> ${entityLabel(rule.source)}`,
                source: rule.destination,
                destination: rule.source,
            };
            onRulesChange([...rules, rule, reverse]);
        } else {
            onRulesChange([...rules, rule]);
        }
        setShowRuleModal(false);
    }

    function deleteRule(id: string) {
        if (readOnly) { return; }
        onRulesChange(rules.filter((r) => r.id !== id));
    }

    function onEdgeClick(e: React.MouseEvent, edge: MapEdge<DiagramRule>) {
        if (readOnly) { return; }
        e.stopPropagation();
        const all = [...edge.ab, ...edge.ba];
        if (all.length === 1) {
            openEditRule(all[0]);
            return;
        }
        const rect = boardRef.current?.getBoundingClientRect();
        if (!rect) { return; }
        setEdgeMenu({ x: e.clientX - rect.left, y: e.clientY - rect.top, edge });
    }

    function edgeState(edge: MapEdge): EdgeState {
        const a = nodeById[edge.a]?.status ?? null;
        const b = nodeById[edge.b]?.status ?? null;
        if (a === 'off' || b === 'off') { return 'off'; }
        if (a === null || b === null) { return 'unknown'; }
        return a === 'connected' && b === 'connected' ? 'live' : 'idle';
    }

    const neighbours = useMemo(() => {
        const focus = hoveredNode ?? selectedNode;
        if (!focus) { return null; }
        const set = new Set<string>([focus]);
        edges.forEach((e) => {
            if (e.a === focus) { set.add(e.b); }
            if (e.b === focus) { set.add(e.a); }
        });
        return set;
    }, [hoveredNode, selectedNode, edges]);
    const hoveredEdgeObj = hoveredEdge ? edges.find((e) => e.key === hoveredEdge) : undefined;
    const focusNode = hoveredNode ?? selectedNode;

    function edgeHighlighted(edge: MapEdge): boolean {
        if (hoveredEdge) { return edge.key === hoveredEdge; }
        return !!focusNode && (edge.a === focusNode || edge.b === focusNode);
    }
    function edgeDimmed(edge: MapEdge): boolean {
        if (hoveredEdge) { return edge.key !== hoveredEdge; }
        return !!focusNode && edge.a !== focusNode && edge.b !== focusNode;
    }
    function nodeDimmed(id: string): boolean {
        if (dragging) { return false; }
        if (hoveredEdgeObj) { return id !== hoveredEdgeObj.a && id !== hoveredEdgeObj.b; }
        return !!neighbours && !neighbours.has(id);
    }

    /** Edge endpoints in reading order: this server first, else as drawn. */
    function orderedEnds(edge: MapEdge<DiagramRule>): { from: string; to: string; both: boolean; fromRules: DiagramRule[]; toRules: DiagramRule[] } {
        const both = edge.ab.length > 0 && edge.ba.length > 0;
        if (both) {
            return edge.b === ownId
                ? { from: edge.b, to: edge.a, both, fromRules: edge.ba, toRules: edge.ab }
                : { from: edge.a, to: edge.b, both, fromRules: edge.ab, toRules: edge.ba };
        }
        return edge.ab.length > 0
            ? { from: edge.a, to: edge.b, both, fromRules: edge.ab, toRules: [] }
            : { from: edge.b, to: edge.a, both, fromRules: edge.ba, toRules: [] };
    }

    function edgeTitle(edge: MapEdge<DiagramRule>): string {
        const lines: string[] = [];
        edge.ab.forEach((r) => lines.push(`${entityLabel(edge.a)} → ${entityLabel(edge.b)}: ${filterDetail(r)}`));
        edge.ba.forEach((r) => lines.push(`${entityLabel(edge.b)} → ${entityLabel(edge.a)}: ${filterDetail(r)}`));
        if (!readOnly) { lines.push(t('Click to edit')); }
        return lines.join('\n');
    }

    const showAllowed = ruleForm.filter.groupsFilterType === 'allowed' || ruleForm.filter.groupsFilterType === 'allowedAndDisallowed';
    const showDisallowed = ruleForm.filter.groupsFilterType === 'disallowed' || ruleForm.filter.groupsFilterType === 'allowedAndDisallowed';
    const statusesShown = (['connected', 'waiting', 'down', 'off'] as MapStatus[])
        .filter((s) => s !== 'off' || nodes.some((n) => n.status === 'off'));
    const anyStatus = nodes.some((n) => n.status !== null);
    const gridSize = 22 * zoom;

    const arrowVariants: (EdgeState | 'accent')[] = ['live', 'idle', 'off', 'accent'];

    return (
        <Stack gap="md">
            {readOnly && (
                <Text size="sm" c="dimmed">
                    {t('Arrows show which way data is allowed to flow. A solid green line means both ends are connected right now. Hover a server to pick out its links.')}
                </Text>
            )}

            {entities.length === 0 ? (
                <Box
                    h={220}
                    bg="var(--raven-bg)"
                    style={{ border: '1px dashed var(--raven-border-strong)', borderRadius: 'var(--mantine-radius-md)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}
                >
                    <Stack align="center" gap={8} maw={420}>
                        <Box
                            aria-hidden
                            style={{ width: 44, height: 44, borderRadius: 'var(--mantine-radius-md)', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--raven-surface)', border: '1px solid var(--raven-border)', color: 'var(--raven-text-muted)' }}
                        >
                            <Flowchart20Regular style={{ width: 24, height: 24 }} />
                        </Box>
                        <Text fw={600} size="sm">{t('Nothing to map yet')}</Text>
                        <Text size="sm" c="dimmed" ta="center">
                            {readOnly
                                ? t('The active policy is empty. Connect a server to see it here.')
                                : t('No federation partners yet — add a CA group or an outgoing connection to see them mapped out here.')}
                        </Text>
                    </Stack>
                </Box>
            ) : (
                <Box
                    ref={boardRef}
                    pos="relative"
                    w="100%"
                    h={boardHeight}
                    role="group"
                    aria-label={t('Connection map')}
                    style={{
                        border: '1px solid var(--raven-border)',
                        borderRadius: 'var(--mantine-radius-md)',
                        overflow: 'hidden',
                        touchAction: 'none',
                        cursor: 'grab',
                        backgroundColor: 'var(--raven-bg)',
                        backgroundImage: 'radial-gradient(circle, var(--raven-border-strong) 1px, transparent 1.2px)',
                        backgroundSize: `${gridSize}px ${gridSize}px`,
                        backgroundPosition: `${pan.x}px ${pan.y}px`,
                    }}
                    onPointerDown={onBackgroundPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                    onPointerCancel={onPointerUp}
                    onClick={(e) => { if (e.target === e.currentTarget) { setSelectedNode(null); } }}
                >
                    <Box
                        pos="absolute"
                        left={0}
                        top={0}
                        w={0}
                        h={0}
                        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: '0 0' }}
                    >
                        <svg
                            width={svgBox.w}
                            height={svgBox.h}
                            viewBox={`${svgBox.x} ${svgBox.y} ${svgBox.w} ${svgBox.h}`}
                            style={{ position: 'absolute', left: svgBox.x, top: svgBox.y, pointerEvents: 'none', overflow: 'visible' }}
                            aria-hidden
                        >
                            <defs>
                                {arrowVariants.map((v) => (
                                    <marker
                                        key={v}
                                        id={`${markerPrefix}-arrow-${v}`}
                                        viewBox="0 0 10 10"
                                        markerWidth={11}
                                        markerHeight={11}
                                        markerUnits="userSpaceOnUse"
                                        refX={8.5}
                                        refY={5}
                                        orient="auto-start-reverse"
                                    >
                                        <path d="M0.5,0.8 L9.5,5 L0.5,9.2 L2.6,5 z" fill={EDGE_COLOR[v]} />
                                    </marker>
                                ))}
                            </defs>
                            {edges.map((edge) => {
                                if (!positions[edge.a] || !positions[edge.b]) { return null; }
                                const geo = edgeCurve(center(edge.a), center(edge.b), NODE_WIDTH, NODE_HEIGHT, 5);
                                const state = edgeState(edge);
                                const hi = edgeHighlighted(edge);
                                const dim = edgeDimmed(edge);
                                const colorKey = hi ? 'accent' : state === 'unknown' ? 'idle' : state;
                                const color = EDGE_COLOR[colorKey];
                                const startMarker = edge.ba.length > 0 ? `url(#${markerPrefix}-arrow-${colorKey})` : undefined;
                                const endMarker = edge.ab.length > 0 ? `url(#${markerPrefix}-arrow-${colorKey})` : undefined;
                                const dash = state === 'idle' ? '6 5' : state === 'off' ? '2 5' : undefined;
                                return (
                                    <g key={edge.key} opacity={dim ? 0.16 : 1} style={{ transition: 'opacity 120ms linear' }}>
                                        <path
                                            d={geo.d}
                                            fill="none"
                                            stroke={color}
                                            strokeWidth={hi ? 2.5 : 1.75}
                                            strokeDasharray={dash}
                                            strokeLinecap="round"
                                            markerStart={startMarker}
                                            markerEnd={endMarker}
                                        />
                                        <path
                                            d={geo.d}
                                            fill="none"
                                            stroke="transparent"
                                            strokeWidth={16}
                                            pointerEvents="stroke"
                                            style={{ cursor: readOnly ? 'default' : 'pointer' }}
                                            onPointerEnter={() => setHoveredEdge(edge.key)}
                                            onPointerLeave={() => setHoveredEdge((k) => (k === edge.key ? null : k))}
                                            onPointerDown={(e) => e.stopPropagation()}
                                            onClick={(e) => onEdgeClick(e, edge)}
                                        >
                                            <title>{edgeTitle(edge)}</title>
                                        </path>
                                    </g>
                                );
                            })}
                        </svg>

                        {edges.map((edge) => {
                            const filtered = [...edge.ab, ...edge.ba].filter(isFiltered);
                            if (filtered.length === 0 || !positions[edge.a] || !positions[edge.b]) { return null; }
                            const geo = edgeCurve(center(edge.a), center(edge.b), NODE_WIDTH, NODE_HEIGHT, 5);
                            const dim = edgeDimmed(edge);
                            return (
                                <Tooltip key={`chip-${edge.key}`} label={<span style={{ whiteSpace: 'pre-line' }}>{edgeTitle(edge)}</span>} multiline maw={320} withArrow>
                                    <Box
                                        pos="absolute"
                                        left={geo.mid.x}
                                        top={geo.mid.y}
                                        onPointerDown={(e: React.PointerEvent) => e.stopPropagation()}
                                        onPointerEnter={() => setHoveredEdge(edge.key)}
                                        onPointerLeave={() => setHoveredEdge((k) => (k === edge.key ? null : k))}
                                        onClick={(e: React.MouseEvent) => onEdgeClick(e, edge)}
                                        style={{
                                            transform: 'translate(-50%, -50%)',
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: 4,
                                            padding: '2px 8px',
                                            borderRadius: 'var(--mantine-radius-sm)',
                                            border: '1px solid var(--raven-border-strong)',
                                            background: 'var(--raven-surface)',
                                            color: 'var(--raven-text-muted)',
                                            fontSize: 11,
                                            fontWeight: 600,
                                            lineHeight: '16px',
                                            whiteSpace: 'nowrap',
                                            cursor: readOnly ? 'default' : 'pointer',
                                            opacity: dim ? 0.3 : 1,
                                            zIndex: 1,
                                        }}
                                    >
                                        <Filter12Regular />
                                        {t('Filtered')}
                                    </Box>
                                </Tooltip>
                            );
                        })}

                        {nodes.map((n) => {
                            const pos = positions[n.id];
                            if (!pos) { return null; }
                            return (
                                <NodeCard
                                    key={n.id}
                                    node={n}
                                    pos={pos}
                                    selected={selectedNode === n.id}
                                    hovered={hoveredNode === n.id}
                                    dimmed={nodeDimmed(n.id)}
                                    readOnly={readOnly}
                                    dragging={dragging === n.id}
                                    onPointerDown={(ev) => onNodePointerDown(ev, n.id)}
                                    onClick={() => handleNodeClick(n.id)}
                                    onEnter={() => setHoveredNode(n.id)}
                                    onLeave={() => setHoveredNode((h) => (h === n.id ? null : h))}
                                />
                            );
                        })}

                        {nodes.length === 1 && edges.length === 0 && positions[nodes[0].id] && (
                            <Text
                                pos="absolute"
                                left={positions[nodes[0].id].x + NODE_WIDTH / 2}
                                top={positions[nodes[0].id].y + NODE_HEIGHT + 18}
                                size="sm"
                                c="dimmed"
                                ta="center"
                                w={300}
                                style={{ transform: 'translateX(-50%)', pointerEvents: 'none' }}
                            >
                                {t('No partners linked yet. Connected servers appear around this one.')}
                            </Text>
                        )}
                    </Box>

                    {selectedNode && !readOnly && (
                        <Group
                            gap={8}
                            wrap="nowrap"
                            pos="absolute"
                            top={10}
                            left="50%"
                            px={12}
                            py={6}
                            style={{ transform: 'translateX(-50%)', background: 'var(--raven-surface)', border: '1px solid var(--raven-accent)', borderRadius: 'var(--mantine-radius-sm)', boxShadow: '0 2px 8px rgba(0,0,0,0.1)', zIndex: 3, maxWidth: 'calc(100% - 24px)' }}
                            onPointerDown={(e) => e.stopPropagation()}
                        >
                            <Text size="sm" truncate>
                                {t('New rule from {{name}}: click the node data should flow to.', { name: entityLabel(selectedNode), interpolation: { escapeValue: false } })}
                            </Text>
                            <ActionIcon size="sm" variant="subtle" color="gray" aria-label={t('Cancel')} onClick={() => setSelectedNode(null)}>
                                <Dismiss16Regular />
                            </ActionIcon>
                        </Group>
                    )}

                    <Group
                        gap={14}
                        pos="absolute"
                        left={10}
                        bottom={10}
                        px={10}
                        py={6}
                        wrap="wrap"
                        onPointerDown={(e) => e.stopPropagation()}
                        style={{ background: 'var(--raven-surface)', border: '1px solid var(--raven-border)', borderRadius: 'var(--mantine-radius-sm)', zIndex: 3, maxWidth: 'calc(100% - 200px)', cursor: 'default' }}
                    >
                        {anyStatus && statusesShown.map((s) => (
                            <Group key={s} gap={6} wrap="nowrap">
                                <StatusDot status={s} size={8} />
                                <Text size="xs" c="dimmed">{statusLabel(s)}</Text>
                            </Group>
                        ))}
                        {anyStatus && <Box w={1} h={14} bg="var(--raven-border)" />}
                        <Group gap={6} wrap="nowrap">
                            <svg width={26} height={10} aria-hidden>
                                <line x1={2} y1={5} x2={19} y2={5} stroke={EDGE_COLOR.live} strokeWidth={1.75} />
                                <path d="M17,1 L25,5 L17,9 L19,5 z" fill={EDGE_COLOR.live} />
                            </svg>
                            <Text size="xs" c="dimmed">{t('Data flows')}</Text>
                        </Group>
                        <Group gap={6} wrap="nowrap">
                            <svg width={26} height={10} aria-hidden>
                                <line x1={2} y1={5} x2={19} y2={5} stroke={EDGE_COLOR.idle} strokeWidth={1.75} strokeDasharray="4 3" />
                                <path d="M17,1 L25,5 L17,9 L19,5 z" fill={EDGE_COLOR.idle} />
                            </svg>
                            <Text size="xs" c="dimmed">{t('Allowed, not connected now')}</Text>
                        </Group>
                    </Group>

                    <ActionIcon.Group
                        pos="absolute"
                        right={10}
                        bottom={10}
                        style={{ zIndex: 3 }}
                        onPointerDown={(e) => e.stopPropagation()}
                    >
                        <Tooltip label={t('Zoom out')}>
                            <ActionIcon variant="default" size="md" aria-label={t('Zoom out')} onClick={() => zoomBy(-0.1)} disabled={zoom <= MIN_ZOOM}>
                                <ZoomOut20Regular style={{ width: 16, height: 16 }} />
                            </ActionIcon>
                        </Tooltip>
                        <ActionIcon.GroupSection variant="default" size="md" miw={48} style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums', color: 'var(--raven-text-muted)' }}>
                            {Math.round(zoom * 100)}%
                        </ActionIcon.GroupSection>
                        <Tooltip label={t('Zoom in')}>
                            <ActionIcon variant="default" size="md" aria-label={t('Zoom in')} onClick={() => zoomBy(0.1)} disabled={zoom >= MAX_ZOOM}>
                                <ZoomIn20Regular style={{ width: 16, height: 16 }} />
                            </ActionIcon>
                        </Tooltip>
                        <Tooltip label={t('Fit to view')}>
                            <ActionIcon variant="default" size="md" aria-label={t('Fit to view')} onClick={fitToView}>
                                <ScaleFit20Regular style={{ width: 16, height: 16 }} />
                            </ActionIcon>
                        </Tooltip>
                        {!readOnly && (
                            <Tooltip label={t('Arrange around this server')}>
                                <ActionIcon variant="default" size="md" aria-label={t('Arrange around this server')} onClick={autoArrange}>
                                    <Grid20Regular style={{ width: 16, height: 16 }} />
                                </ActionIcon>
                            </Tooltip>
                        )}
                    </ActionIcon.Group>

                    <Menu opened={edgeMenu !== null} onChange={(o) => { if (!o) { setEdgeMenu(null); } }} position="bottom-start" withinPortal shadow="md">
                        <Menu.Target>
                            <Box pos="absolute" left={edgeMenu?.x ?? 0} top={edgeMenu?.y ?? 0} w={1} h={1} style={{ pointerEvents: 'none' }} />
                        </Menu.Target>
                        <Menu.Dropdown>
                            <Menu.Label>{t('Edit rule')}</Menu.Label>
                            {edgeMenu && [...edgeMenu.edge.ab, ...edgeMenu.edge.ba].map((r) => (
                                <Menu.Item key={r.id} onClick={() => { setEdgeMenu(null); openEditRule(r); }}>
                                    {entityLabel(r.source)} → {entityLabel(r.destination)}
                                    <Text span size="xs" c="dimmed">{` · ${filterTypeLabel(r.filter?.groupsFilterType || 'allGroups')}`}</Text>
                                </Menu.Item>
                            ))}
                        </Menu.Dropdown>
                    </Menu>
                </Box>
            )}

            <Stack gap={6}>
                <Text size="sm" fw={600} c="dimmed">
                    {readOnly
                        ? `${t('Links')}${edges.length > 0 ? ` (${edges.length})` : ''}`
                        : `${t('Federation Rules')}${rules.length > 0 ? ` (${rules.length})` : ''}`}
                </Text>
                {rules.length === 0 ? (
                    <Text size="sm" c="dimmed">
                        {readOnly
                            ? t('No rules in the active policy, so no data flows between partners.')
                            : t('No rules yet — connect two partners above to allow data to flow between them.')}
                    </Text>
                ) : readOnly ? (
                    edges.map((edge) => {
                        const ends = orderedEnds(edge);
                        const fromFilters = Array.from(new Set(ends.fromRules.map((r) => filterTypeLabel(r.filter?.groupsFilterType || 'allGroups'))));
                        const toFilters = Array.from(new Set(ends.toRules.map((r) => filterTypeLabel(r.filter?.groupsFilterType || 'allGroups'))));
                        const same = ends.both && fromFilters.join() === toFilters.join();
                        const anyFiltered = [...edge.ab, ...edge.ba].some(isFiltered);
                        return (
                            <RuleRow key={edge.key} hovered={hoveredEdge === edge.key} onHover={(h) => setHoveredEdge(h ? edge.key : null)}>
                                <Group gap={8} wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
                                    <Text size="sm" fw={600} truncate>{entityLabel(ends.from)}</Text>
                                    <Box c="dimmed" style={{ display: 'flex', flexShrink: 0 }} aria-label={ends.both ? t('both ways') : t('to')}>
                                        {ends.both ? <ArrowSwap16Regular /> : <ArrowRight16Regular />}
                                    </Box>
                                    <Text size="sm" fw={600} truncate>{entityLabel(ends.to)}</Text>
                                </Group>
                                <Group gap={6} wrap="nowrap" style={{ flexShrink: 0 }}>
                                    {same ? (
                                        <Badge variant="light" color={anyFiltered ? 'indigo' : 'gray'} tt="none">{fromFilters.join(', ')}{ends.both ? ` · ${t('both ways')}` : ''}</Badge>
                                    ) : (
                                        <>
                                            <Badge variant="light" color={ends.fromRules.some(isFiltered) ? 'indigo' : 'gray'} tt="none">→ {fromFilters.join(', ')}</Badge>
                                            {ends.both && <Badge variant="light" color={ends.toRules.some(isFiltered) ? 'indigo' : 'gray'} tt="none">← {toFilters.join(', ')}</Badge>}
                                        </>
                                    )}
                                </Group>
                            </RuleRow>
                        );
                    })
                ) : (
                    rules.map((rule) => {
                        const s = aliases[rule.source] ?? rule.source;
                        const d = aliases[rule.destination] ?? rule.destination;
                        const key = s < d ? `${s}|${d}` : `${d}|${s}`;
                        const customName = rule.name && !rule.name.includes(' -> ') ? rule.name : '';
                        return (
                            <RuleRow key={rule.id} hovered={hoveredEdge === key} onHover={(h) => setHoveredEdge(h ? key : null)}>
                                <Group gap={8} wrap="nowrap" style={{ flex: 1, cursor: 'pointer', minWidth: 0 }} onClick={() => openEditRule(rule)}>
                                    {endpoint(rule.source)}
                                    <Box c="dimmed" style={{ display: 'flex', flexShrink: 0 }}><ArrowRight16Regular /></Box>
                                    {endpoint(rule.destination)}
                                    {customName && <Text size="xs" c="dimmed" truncate>{customName}</Text>}
                                </Group>
                                <Badge variant="light" color={isFiltered(rule) ? 'indigo' : 'gray'} tt="none" style={{ flexShrink: 0 }}>
                                    {filterTypeLabel(rule.filter?.groupsFilterType || 'allGroups')}
                                </Badge>
                                <Tooltip label={t('Remove rule')}>
                                    <ActionIcon color="red" variant="subtle" aria-label={t('Remove rule')} onClick={() => deleteRule(rule.id)}>
                                        <Delete20Regular style={{ width: 16, height: 16 }} />
                                    </ActionIcon>
                                </Tooltip>
                            </RuleRow>
                        );
                    })
                )}
            </Stack>

            <Modal opened={showRuleModal} onClose={() => setShowRuleModal(false)} title={editingRuleId ? t('Edit Rule') : t('Add Rule')}>
                <Stack gap="sm">
                    <Group gap={8} wrap="nowrap">
                        <Text size="sm" fw={600} truncate>{entityLabel(ruleForm.source)}</Text>
                        <Box c="dimmed" style={{ display: 'flex', flexShrink: 0 }}><ArrowRight16Regular /></Box>
                        <Text size="sm" fw={600} truncate>{entityLabel(ruleForm.destination)}</Text>
                    </Group>
                    <TextInput
                        label={t('Name')}
                        value={ruleForm.name}
                        onChange={(e) => setRuleForm({ ...ruleForm, name: e.currentTarget.value })}
                    />
                    <Select
                        label={t('Group Filter')}
                        value={ruleForm.filter.groupsFilterType}
                        onChange={(v) => setRuleForm({ ...ruleForm, filter: { ...ruleForm.filter, groupsFilterType: (v as any) || 'allGroups' } })}
                        data={[
                            { value: 'allGroups', label: t('All groups') },
                            { value: 'allowed', label: t('Only allowed groups') },
                            { value: 'disallowed', label: t('All except disallowed groups') },
                            { value: 'allowedAndDisallowed', label: t('Allowed, except disallowed') },
                        ]}
                    />
                    {!editingRuleId && (
                        <Switch
                            label={t('Also allow the reverse direction')}
                            description={t('Creates a second rule for destination -> source with the same filter')}
                            checked={ruleForm.bidirectional}
                            onChange={(e) => setRuleForm({ ...ruleForm, bidirectional: e.currentTarget.checked })}
                        />
                    )}
                    {showAllowed && (
                        <TagsInput
                            label={t('Allowed Groups')}
                            data={knownGroups}
                            value={ruleForm.filter.allowedGroups}
                            onChange={(v) => setRuleForm({ ...ruleForm, filter: { ...ruleForm.filter, allowedGroups: v } })}
                        />
                    )}
                    {showDisallowed && (
                        <TagsInput
                            label={t('Disallowed Groups')}
                            data={knownGroups}
                            value={ruleForm.filter.disallowedGroups}
                            onChange={(v) => setRuleForm({ ...ruleForm, filter: { ...ruleForm.filter, disallowedGroups: v } })}
                        />
                    )}
                </Stack>
                <Group justify="space-between" mt="md">
                    {editingRuleId ? (
                        <Button color="red" variant="subtle" onClick={() => { deleteRule(ruleForm.id); setShowRuleModal(false); }}>
                            {t('Delete Rule')}
                        </Button>
                    ) : <div />}
                    <Group>
                        <Button variant="default" onClick={() => setShowRuleModal(false)}>{t('Cancel')}</Button>
                        <Button onClick={saveRule}>{t('Save')}</Button>
                    </Group>
                </Group>
            </Modal>
        </Stack>
    );
}

function RuleRow({ children, hovered, onHover }: { children: React.ReactNode; hovered: boolean; onHover: (h: boolean) => void }) {
    return (
        <Group
            gap="sm"
            wrap="nowrap"
            px="sm"
            py={8}
            bg="var(--raven-surface)"
            onMouseEnter={() => onHover(true)}
            onMouseLeave={() => onHover(false)}
            style={{
                border: `1px solid ${hovered ? 'var(--raven-border-strong)' : 'var(--raven-border)'}`,
                borderRadius: 'var(--mantine-radius-sm)',
                transition: 'border-color var(--raven-transition)',
            }}
        >
            {children}
        </Group>
    );
}

/** Stored positions, plus a spot to the right of the layout for any node that has none. */
function placeMissing(stored: Record<string, XY>, ids: string[]): Record<string, XY> {
    const next = { ...stored };
    const placed = ids.filter((id) => next[id]);
    const missing = ids.filter((id) => !next[id]);
    if (missing.length === 0) { return next; }
    const maxX = placed.length ? Math.max(...placed.map((id) => next[id].x)) : LAYOUT_ORIGIN.x - NODE_WIDTH;
    const minY = placed.length ? Math.min(...placed.map((id) => next[id].y)) : LAYOUT_ORIGIN.y;
    missing.forEach((id, i) => {
        next[id] = { x: maxX + NODE_WIDTH + 60, y: minY + i * (NODE_HEIGHT + 28) };
    });
    return next;
}
