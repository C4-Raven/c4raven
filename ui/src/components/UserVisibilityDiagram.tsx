import React, { memo, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ActionIcon, Badge, Box, Button, Group, SegmentedControl, Stack, Switch, Text, Tooltip } from '@mantine/core';
import {
    ArrowRight16Regular,
    ArrowSwap16Regular,
    Delete20Regular,
    Dismiss16Regular,
    Dismiss20Regular,
    People20Regular,
    Person20Regular,
    ScaleFit20Regular,
    Warning20Regular,
    ZoomIn20Regular,
    ZoomOut20Regular,
} from '@fluentui/react-icons';
import axios from '../axios_config';
import { apiRoutes } from '../apiRoutes';
import { isReadOnlyAdmin } from '../adminAccess';
import { READ_ONLY_LABEL } from './ReadOnlyTip';
import { notifications } from '@mantine/notifications';
import { t } from 'i18next';
import { boxExit, fitView, type XY } from './federationMap';
import { contentBox, layoutCentre, linkSummary, visibilityCurve, visibilityLayout, type LinkSummary } from './userVisibilityMap';

interface Edge {
    source: string;
    target: string;
    type: 'solid' | 'dotted';
    // True if a direct (pairwise) connection contributes to this edge, so
    // it can be removed here. False means it's only true because of a
    // shared named group's own Access list -- can't be selectively removed
    // for just this pair without changing that group for everyone in it.
    manual: boolean;
    // Named groups carrying source -> target and target -> source. A one-way
    // line can't take away access one of these already gives.
    source_groups?: string[];
    target_groups?: string[];
}

// The same card and board proportions as the federation connection map.
const NODE_WIDTH = 172;
const NODE_HEIGHT = 52;
const BOARD_HEIGHT = 580;
const MIN_ZOOM = 0.15;
const MAX_ZOOM = 2.5;
/** Fit never enlarges past 100%: a small group stays at its natural size, centred. */
const FIT_MAX_ZOOM = 1;
/** Room for the legend and zoom controls along the bottom edge. */
const FIT_INSETS = { top: 40, right: 36, bottom: 64, left: 36 };
/** Gap between a card's border and the end of a connector (where the arrow tip lands). */
const BORDER_GAP = 5;
/** Extra room the connector layer keeps round the cards for bowed curves and arrowheads. */
const LAYER_PAD = 220;
/** The list under the map shows this many rows before "Show all". */
const LIST_PREVIEW = 8;
const DIM_OPACITY = 0.14;

// ---- Connector styling ---------------------------------------------------
// Two-way vs one-way differ by colour (indigo vs orange -- distinguishable for
// the common colour-vision deficiencies), by arrowheads (both ends vs the
// receiver's end only) and by dash pattern, so no single cue carries the
// meaning alone. Group-inherited edges are thinner and grey so the connections
// someone actually drew here stay the focus. --uvd-two-way / --uvd-one-way
// (global.css) are a lighter shade in dark mode and a darker one in light mode.
type EdgeStyleKey = 'two' | 'one' | 'inherited';
const EDGE_COLOR: Record<EdgeStyleKey, string> = {
    two: 'var(--uvd-two-way, var(--mantine-color-indigo-6))',
    one: 'var(--uvd-one-way, var(--mantine-color-orange-7))',
    inherited: 'var(--raven-text-muted)',
};
const EDGE_WIDTH: Record<EdgeStyleKey, number> = { two: 1.75, one: 1.75, inherited: 1.25 };
const EDGE_WIDTH_ACTIVE: Record<EdgeStyleKey, number> = { two: 2.5, one: 2.5, inherited: 2 };
const ONE_WAY_DASH = '6 5';
const SELECT_COLOR = 'var(--raven-accent)';

function styleKeyFor(edge: Pick<Edge, 'type' | 'manual'>): EdgeStyleKey {
    if (!edge.manual) { return 'inherited'; }
    return edge.type === 'solid' ? 'two' : 'one';
}

function markerId(prefix: string, key: EdgeStyleKey): string {
    return `${prefix}-arrow-${key}`;
}

function edgeKey(edge: Pick<Edge, 'source' | 'target'>): string {
    return `${edge.source}\u0000${edge.target}`;
}

function edgeTypeLabel(edge: Pick<Edge, 'type' | 'manual'>): string {
    if (!edge.manual) { return t('Via a group'); }
    return edge.type === 'solid' ? t('Two-way') : t('One-way');
}

// One fixed-size arrowhead per style (as on the federation map). With
// orient="auto-start-reverse" the same marker points forward at a path's end
// and backward at its start, so a two-way line gets a head at each user.
function ArrowMarkers({ prefix }: { prefix: string }) {
    return (
        <defs>
            {(Object.keys(EDGE_COLOR) as EdgeStyleKey[]).map((key) => (
                <marker
                    key={key}
                    id={markerId(prefix, key)}
                    viewBox="0 0 10 10"
                    markerWidth={key === 'inherited' ? 9 : 11}
                    markerHeight={key === 'inherited' ? 9 : 11}
                    markerUnits="userSpaceOnUse"
                    refX={8.5}
                    refY={5}
                    orient="auto-start-reverse"
                >
                    <path d="M0.5,0.8 L9.5,5 L0.5,9.2 L2.6,5 z" fill={EDGE_COLOR[key]} />
                </marker>
            ))}
        </defs>
    );
}

interface EdgeGeometry {
    key: string;
    edge: Edge;
    style: EdgeStyleKey;
    d: string;
    // Midpoint of the curve (board coords): label pill and keyboard-focus tooltip anchor.
    mid: XY;
}

function nodeCenter(pos: XY): XY {
    return { x: pos.x + NODE_WIDTH / 2, y: pos.y + NODE_HEIGHT / 2 };
}

function computeGeometry(edges: Edge[], positions: Record<string, XY>, centre: XY): EdgeGeometry[] {
    // Group by unordered pair so several edges between the same two users
    // (shouldn't normally happen -- the API sends one per pair -- but cheap to
    // handle) fan out side by side instead of drawing on top of each other.
    const pairCount = new Map<string, number>();
    const pairIndex: number[] = [];
    const pairKey = (e: Edge) => (e.source < e.target ? `${e.source}\u0000${e.target}` : `${e.target}\u0000${e.source}`);
    edges.forEach((e) => {
        const pk = pairKey(e);
        const n = pairCount.get(pk) ?? 0;
        pairIndex.push(n);
        pairCount.set(pk, n + 1);
    });
    const out: EdgeGeometry[] = [];
    edges.forEach((edge, i) => {
        const pa = positions[edge.source];
        const pb = positions[edge.target];
        if (!pa || !pb || edge.source === edge.target) { return; }
        // Always curve from the alphabetically-first user, so both directions of a pair agree.
        const flip = edge.source > edge.target;
        const lo = nodeCenter(flip ? pb : pa);
        const hi = nodeCenter(flip ? pa : pb);
        const n = pairCount.get(pairKey(edge)) ?? 1;
        const spread = (pairIndex[i] - (n - 1) / 2) * 22;
        const curve = visibilityCurve(lo, hi, centre, NODE_WIDTH, NODE_HEIGHT, BORDER_GAP, spread);
        // The path runs source -> target so the end marker sits at the receiver.
        const d = flip ? reverseQuad(curve.d) : curve.d;
        out.push({ key: edgeKey(edge), edge, style: styleKeyFor(edge), d, mid: curve.mid });
    });
    return out;
}

/** "M a Q c b" -> "M b Q c a". */
function reverseQuad(d: string): string {
    const m = /^M([^ ]+) Q([^ ]+) ([^ ]+)$/.exec(d);
    return m ? `M${m[3]} Q${m[2]} ${m[1]}` : d;
}

function EdgeVisual({ g, active, animate, markerPrefix }: { g: EdgeGeometry; active: boolean; animate: boolean; markerPrefix: string }) {
    const width = active ? EDGE_WIDTH_ACTIVE[g.style] : EDGE_WIDTH[g.style];
    const marker = `url(#${markerId(markerPrefix, g.style)})`;
    const oneWay = g.edge.type !== 'solid';
    return (
        <path
            d={g.d}
            fill="none"
            stroke={EDGE_COLOR[g.style]}
            strokeWidth={width}
            strokeLinecap="round"
            strokeDasharray={oneWay ? ONE_WAY_DASH : undefined}
            markerStart={oneWay ? undefined : marker}
            markerEnd={marker}
            opacity={active || g.edge.manual ? 1 : 0.5}
            className={animate && oneWay ? 'uvd-flow' : undefined}
            style={animate && oneWay ? ({ '--uvd-dash-cycle': '-11px' } as React.CSSProperties) : undefined}
        />
    );
}

// Every edge at rest. Depends only on geometry, so hovering never
// re-renders it -- hover just fades this whole layer as one group.
// Quiet inherited edges paint first, drawn ones over them.
function BaseEdgesImpl({ geometry, markerPrefix }: { geometry: EdgeGeometry[]; markerPrefix: string }) {
    const ordered = [...geometry].sort((x, y) => Number(x.edge.manual) - Number(y.edge.manual));
    return <>{ordered.map((g) => <EdgeVisual key={g.key} g={g} active={false} animate={false} markerPrefix={markerPrefix} />)}</>;
}
const BaseEdges = memo(BaseEdgesImpl);

interface EdgeHandlers {
    onEdgeEnter: (key: string, e: React.PointerEvent<SVGPathElement> | null) => void;
    onEdgeMove: (e: React.PointerEvent<SVGPathElement>) => void;
    onEdgeLeave: () => void;
    onEdgeActivate: (edge: Edge) => void;
}

// Wide invisible hit areas along each curve -- forgiving to hover/click
// without drawing thick. Topmost, so the highlight overlay never steals the
// pointer. Each is keyboard-focusable (see .uvd-edge-hit in global.css).
function HitAreasImpl({ geometry, onEdgeEnter, onEdgeMove, onEdgeLeave, onEdgeActivate }: { geometry: EdgeGeometry[] } & EdgeHandlers) {
    return (
        <>
            {geometry.map((g) => (
                <path
                    key={g.key}
                    d={g.d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth={16}
                    strokeLinecap="round"
                    pointerEvents="stroke"
                    className="uvd-edge-hit"
                    tabIndex={0}
                    role="button"
                    aria-label={g.edge.type === 'solid'
                        ? t('{{a}} and {{b}} both share data', { a: g.edge.source, b: g.edge.target })
                        : t('{{b}} receives {{a}}\'s data', { a: g.edge.source, b: g.edge.target })}
                    style={{ cursor: g.edge.manual ? 'pointer' : 'not-allowed' }}
                    onPointerEnter={(e) => onEdgeEnter(g.key, e)}
                    onPointerMove={onEdgeMove}
                    onPointerLeave={onEdgeLeave}
                    onPointerDown={(e) => e.stopPropagation()}
                    onFocus={() => onEdgeEnter(g.key, null)}
                    onBlur={onEdgeLeave}
                    onClick={() => onEdgeActivate(g.edge)}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onEdgeActivate(g.edge); }
                    }}
                />
            ))}
        </>
    );
}
const HitAreas = memo(HitAreasImpl);

// ---- Nodes ---------------------------------------------------------------

/** The coloured dot in a card's subtitle: indigo = has a two-way link, orange = one-way only, hollow = none shown. */
function LinkDot({ summary, size = 8 }: { summary: LinkSummary | undefined; size?: number }) {
    const drawn = summary ? summary.twoWay + summary.oneWay : 0;
    const color = !summary || summary.total === 0
        ? null
        : summary.twoWay > 0 ? EDGE_COLOR.two : summary.oneWay > 0 ? EDGE_COLOR.one : EDGE_COLOR.inherited;
    return (
        <span
            aria-hidden
            style={{
                display: 'inline-block',
                flexShrink: 0,
                width: size,
                height: size,
                borderRadius: '50%',
                background: color ?? 'transparent',
                border: color ? undefined : '1.5px solid var(--raven-border-strong)',
                opacity: drawn === 0 && color ? 0.7 : 1,
            }}
        />
    );
}

function summaryText(s: LinkSummary | undefined): string {
    if (!s || s.total === 0) { return t('No links shown'); }
    return s.total === 1 ? t('1 link') : t('{{count}} links', { count: s.total });
}

interface NodeCardProps {
    name: string;
    pos: XY;
    summary: LinkSummary | undefined;
    selected: boolean;
    target: boolean;
    hovered: boolean;
    dimmed: boolean;
    readOnly: boolean;
    connecting: boolean;
    onPointerDown: (e: React.PointerEvent<HTMLDivElement>, name: string) => void;
    onActivate: (name: string) => void;
    onHover: (name: string, on: boolean) => void;
}

/** One user card, memoised so a hover only re-renders the cards whose look changes. */
const NodeCard = memo(({ name, pos, summary, selected, target, hovered, dimmed, readOnly, connecting, onPointerDown, onActivate, onHover }: NodeCardProps) => {
    const accent = selected || target;
    const ring = accent ? '0 0 0 3px color-mix(in srgb, var(--raven-accent) 30%, transparent)' : null;
    const lift = hovered ? '0 4px 12px rgba(0, 0, 0, 0.12)' : '0 1px 2px rgba(0, 0, 0, 0.06)';
    const linked = !!summary && summary.total > 0;
    return (
        <div
            role={readOnly ? undefined : 'button'}
            tabIndex={readOnly ? undefined : 0}
            aria-pressed={readOnly ? undefined : selected}
            aria-label={`${name}, ${summaryText(summary)}`}
            title={name}
            data-uvd-node={name}
            style={{
                position: 'absolute',
                left: pos.x,
                top: pos.y,
                width: NODE_WIDTH,
                height: NODE_HEIGHT,
                boxSizing: 'border-box',
                cursor: connecting && !selected ? 'pointer' : 'grab',
                userSelect: 'none',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '0 10px',
                borderRadius: 'var(--mantine-radius-md)',
                background: selected ? 'color-mix(in srgb, var(--raven-accent) 8%, var(--raven-surface))' : 'var(--raven-surface)',
                border: `1px solid ${accent ? SELECT_COLOR : hovered ? 'var(--raven-text-muted)' : 'var(--raven-border-strong)'}`,
                boxShadow: ring ? `${ring}, ${lift}` : lift,
                opacity: dimmed ? 0.4 : 1,
                transition: 'opacity 120ms linear, border-color 120ms linear',
                zIndex: hovered || accent ? 2 : 1,
                outlineOffset: 3,
            }}
            onPointerDown={(e: React.PointerEvent<HTMLDivElement>) => onPointerDown(e, name)}
            onPointerEnter={() => onHover(name, true)}
            onPointerLeave={() => onHover(name, false)}
            onClick={(e: React.MouseEvent) => { e.stopPropagation(); onActivate(name); }}
            onKeyDown={(e: React.KeyboardEvent) => {
                if (!readOnly && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onActivate(name); }
            }}
        >
            <div
                aria-hidden
                style={{
                    flexShrink: 0,
                    width: 32,
                    height: 32,
                    borderRadius: 'var(--mantine-radius-sm)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: selected ? 'var(--raven-accent)' : linked ? 'var(--mantine-color-indigo-light)' : 'var(--mantine-color-gray-light)',
                    color: selected ? 'var(--mantine-primary-color-contrast)' : linked ? 'var(--mantine-color-indigo-light-color)' : 'var(--mantine-color-gray-light-color)',
                }}
            >
                <Person20Regular />
            </div>
            {/* Plain elements (not Mantine Text/Group) keep a re-render of many cards cheap. */}
            <div style={{ minWidth: 0, flex: 1, display: 'flex', flexDirection: 'column', gap: 1 }}>
                <span style={{ fontSize: 'var(--mantine-font-size-sm)', fontWeight: 600, lineHeight: 1.25, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                    <LinkDot summary={summary} />
                    <span style={{ fontSize: 'var(--mantine-font-size-xs)', color: 'var(--raven-text-muted)', lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{summaryText(summary)}</span>
                </span>
            </div>
        </div>
    );
});
NodeCard.displayName = 'NodeCard';

function LegendLine({ style }: { style: EdgeStyleKey }) {
    const color = EDGE_COLOR[style];
    const oneWay = style === 'one';
    return (
        <svg width={30} height={10} aria-hidden style={{ flexShrink: 0 }}>
            <line x1={oneWay ? 2 : 6} y1={5} x2={23} y2={5} stroke={color} strokeWidth={EDGE_WIDTH[style]} strokeDasharray={oneWay ? '4 3' : undefined} />
            <path d="M21,1 L29,5 L21,9 L23,5 z" fill={color} />
            {!oneWay && <path d="M9,1 L1,5 L9,9 L7,5 z" fill={color} />}
        </svg>
    );
}

function notifyError(title: string, err: any) {
    notifications.show({ title, message: err.response?.data?.error, icon: <Dismiss20Regular />, color: 'red' });
}

interface UserVisibilityDiagramProps {
    // When given, only these users are shown as nodes (and only connections
    // between them), e.g. scoped to one group's current roster. Omit to show
    // every active user system-wide.
    scopeToUsernames?: string[];
    // Called after a connection is successfully created or removed, so a
    // parent showing other data about the same users (e.g. a group's own
    // membership table) can refresh and stay in sync.
    onChange?: () => void;
}

export default function UserVisibilityDiagram({ scopeToUsernames, onChange }: UserVisibilityDiagramProps) {
    // Read-only admins can look at the diagram but not connect or disconnect anyone
    const readOnly = isReadOnlyAdmin();
    const [users, setUsers] = useState<string[]>([]);
    const [edges, setEdges] = useState<Edge[]>([]);
    const [positions, setPositions] = useState<Record<string, XY>>({});
    const [selected, setSelected] = useState<string | null>(null);
    const [mode, setMode] = useState<'solid' | 'dotted'>('solid');
    // A member's own group access already implies a connection to every
    // other member with matching access -- drawing all of those by default
    // turns any group past a handful of people into a solid mesh of lines.
    // Only the connections someone actually drew here (manual/removable)
    // are shown by default; the inherited ones are one toggle away.
    const [showInherited, setShowInherited] = useState(false);
    const [showAllLinks, setShowAllLinks] = useState(false);
    const [zoom, setZoom] = useState(1);
    const [pan, setPan] = useState<XY>({ x: 0, y: 0 });
    const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
    const [hoveredNode, setHoveredNode] = useState<string | null>(null);
    // Tooltip anchor, in board (unscaled, un-panned) px.
    const [tipPos, setTipPos] = useState<XY | null>(null);
    // Pointer position in layout coords while a node is selected, for the
    // "now click another user" preview line.
    const [pointer, setPointer] = useState<XY | null>(null);
    const [boardWidth, setBoardWidth] = useState(0);
    const [dragging, setDragging] = useState(false);

    // useId() output contains ':' which isn't safe inside url(#...).
    const markerPrefix = `uvd${useId().replace(/[^a-zA-Z0-9]/g, '')}`;

    const boardRef = useRef<HTMLDivElement>(null);
    const dragRef = useRef<{ name: string; offsetX: number; offsetY: number; startX: number; startY: number; moved: boolean } | null>(null);
    const suppressClick = useRef(false);
    const panDragRef = useRef<{ startClientX: number; startClientY: number; startPanX: number; startPanY: number } | null>(null);
    const positionsRef = useRef(positions);
    positionsRef.current = positions;
    const panRef = useRef(pan);
    panRef.current = pan;
    const zoomRef = useRef(zoom);
    zoomRef.current = zoom;
    const needsFit = useRef(true);

    // Track the board's width only (its height is fixed), so this can never
    // feed back into its own size.
    const hasUsers = users.length > 0;
    useLayoutEffect(() => {
        const el = boardRef.current;
        if (!el) { return undefined; }
        setBoardWidth(el.clientWidth);
        if (typeof ResizeObserver === 'undefined') { return undefined; }
        const ro = new ResizeObserver(() => setBoardWidth(el.clientWidth));
        ro.observe(el);
        return () => ro.disconnect();
    }, [hasUsers]);

    function computeFit(pos: Record<string, XY>) {
        const width = boardRef.current?.clientWidth || boardWidth;
        return fitView(Object.values(pos), NODE_WIDTH, NODE_HEIGHT, { width, height: BOARD_HEIGHT }, FIT_INSETS, { min: MIN_ZOOM, max: FIT_MAX_ZOOM });
    }

    // Fit once the board's width is known (after the users arrive or change).
    useLayoutEffect(() => {
        if (boardWidth <= 0 || !needsFit.current || Object.keys(positions).length === 0) { return; }
        const fit = computeFit(positions);
        setZoom(fit.zoom);
        setPan(fit.pan);
        needsFit.current = false;
    }, [boardWidth, positions]); // eslint-disable-line react-hooks/exhaustive-deps

    function applyUsers(names: string[]) {
        const aspect = Math.max(1, (boardRef.current?.clientWidth || 1000) / BOARD_HEIGHT);
        setUsers(names);
        setPositions((prev) => {
            const next = visibilityLayout(names, NODE_WIDTH, NODE_HEIGHT, aspect);
            names.forEach((n: string) => { if (prev[n]) { next[n] = prev[n]; } });
            return next;
        });
        needsFit.current = true;
    }

    function loadUsers() {
        if (scopeToUsernames) {
            applyUsers(scopeToUsernames);
            return;
        }
        axios.get(apiRoutes.userListAll).then((r) => {
            const names = r.data
                .filter((u: any) => u.active)
                .map((u: any) => u.username as string);
            applyUsers(names);
        }).catch((err) => {
            notifyError(t('Failed to load users'), err);
        });
    }

    function loadEdges() {
        axios.get(apiRoutes.userVisibility).then((r) => {
            let data = r.data as Edge[];
            if (scopeToUsernames) {
                const scope = new Set(scopeToUsernames);
                data = data.filter((e) => scope.has(e.source) && scope.has(e.target));
            }
            setEdges(data);
        }).catch((err) => {
            notifyError(t('Failed to load visibility'), err);
        });
    }

    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { loadUsers(); loadEdges(); }, [JSON.stringify(scopeToUsernames)]);

    // Escape cancels a half-drawn connection.
    useEffect(() => {
        if (!selected) { return undefined; }
        const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setSelected(null); } };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [selected]);

    function connect(source: string, target: string) {
        if (readOnly) { return; }
        axios.put(apiRoutes.userVisibility, { source, target, type: mode }).then((r) => {
            loadEdges();
            onChange?.();
            const overriddenBy: string[] = r.data?.overridden_by ?? [];
            if (overriddenBy.length > 0) {
                notifications.show({
                    title: t('Saved, but still two-way'),
                    message: t(
                        '{{target}} still sends to {{source}} through: {{groups}}. A one-way line can only add access, not take it away. In those groups, set {{target}} to "Receive only" or {{source}} to "Send only" (or remove them).',
                        { source, target, groups: overriddenBy.join(', ') },
                    ),
                    icon: <Warning20Regular />,
                    color: 'orange',
                    autoClose: 15000,
                });
            }
        }).catch((err) => {
            notifyError(t('Failed to connect users'), err);
        });
    }

    function disconnect(edge: Edge) {
        if (readOnly) { return; }
        axios.delete(apiRoutes.userVisibility, { params: { source: edge.source, target: edge.target } }).then(() => {
            loadEdges();
            onChange?.();
        }).catch((err) => {
            notifyError(t('Failed to remove connection'), err);
        });
    }

    function handleNodeClick(name: string) {
        if (suppressClick.current) { suppressClick.current = false; return; }
        if (readOnly) { return; }
        if (!selected) {
            setSelected(name);
            return;
        }
        if (selected === name) {
            setSelected(null);
            return;
        }
        const source = selected;
        setSelected(null);
        connect(source, name);
    }

    function onNodePointerDown(e: React.PointerEvent<HTMLDivElement>, name: string) {
        const board = boardRef.current;
        if (!board || e.button !== 0) { return; }
        const boardRect = board.getBoundingClientRect();
        const pos = positionsRef.current[name];
        if (!pos) { return; }
        const p = panRef.current;
        dragRef.current = {
            name,
            offsetX: (e.clientX - boardRect.left - p.x) / zoomRef.current - pos.x,
            offsetY: (e.clientY - boardRect.top - p.y) / zoomRef.current - pos.y,
            startX: e.clientX,
            startY: e.clientY,
            moved: false,
        };
        e.currentTarget.setPointerCapture(e.pointerId);
        e.stopPropagation();
    }

    // Panning starts only when the pointer goes down directly on the
    // background (not bubbled up from a node), so it never fights dragging.
    function onBackgroundPointerDown(e: React.PointerEvent<HTMLDivElement>) {
        if (e.target !== e.currentTarget) { return; }
        panDragRef.current = {
            startClientX: e.clientX,
            startClientY: e.clientY,
            startPanX: panRef.current.x,
            startPanY: panRef.current.y,
        };
        e.currentTarget.setPointerCapture(e.pointerId);
    }

    function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
        const board = boardRef.current;
        if (!board) { return; }
        const rect = board.getBoundingClientRect();
        const p = panRef.current;
        const drag = dragRef.current;
        if (drag) {
            if (!drag.moved && Math.hypot(e.clientX - drag.startX, e.clientY - drag.startY) < 4) { return; }
            if (!drag.moved) { drag.moved = true; setDragging(true); }
            const x = (e.clientX - rect.left - p.x) / zoomRef.current - drag.offsetX;
            const y = (e.clientY - rect.top - p.y) / zoomRef.current - drag.offsetY;
            setPositions((prev) => ({ ...prev, [drag.name]: { x, y } }));
            return;
        }
        if (panDragRef.current) {
            const { startClientX, startClientY, startPanX, startPanY } = panDragRef.current;
            setPan({ x: startPanX + (e.clientX - startClientX), y: startPanY + (e.clientY - startClientY) });
            return;
        }
        if (selected) {
            setPointer({ x: (e.clientX - rect.left - p.x) / zoomRef.current, y: (e.clientY - rect.top - p.y) / zoomRef.current });
        }
    }

    function onPointerUp() {
        const drag = dragRef.current;
        if (drag) {
            dragRef.current = null;
            if (drag.moved) {
                // A drag ends in a click on the card: don't treat it as "connect".
                suppressClick.current = true;
                setDragging(false);
            }
        }
        panDragRef.current = null;
    }

    function zoomBy(delta: number) {
        const current = zoomRef.current;
        const next = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Math.round((current + delta) * 20) / 20));
        // Zoom around the board's centre rather than its corner.
        const c = { x: (boardRef.current?.clientWidth || boardWidth) / 2, y: BOARD_HEIGHT / 2 };
        const p = panRef.current;
        setZoom(next);
        setPan({ x: c.x - ((c.x - p.x) * next) / current, y: c.y - ((c.y - p.y) * next) / current });
    }

    function fitToView() {
        const fit = computeFit(positionsRef.current);
        setZoom(fit.zoom);
        setPan(fit.pan);
    }

    const inheritedCount = useMemo(() => edges.filter((e) => !e.manual).length, [edges]);
    const visibleEdges = useMemo(() => (showInherited ? edges : edges.filter((e) => e.manual)), [edges, showInherited]);
    const positionList = useMemo(() => Object.values(positions), [positions]);
    const centre = useMemo(() => layoutCentre(positionList, NODE_WIDTH, NODE_HEIGHT), [positionList]);
    // The connector layer only covers the cards (plus room for curves): a huge
    // fixed SVG is repainted in full on every pan, zoom and hover.
    const svgBox = useMemo(() => contentBox(positionList, NODE_WIDTH, NODE_HEIGHT, LAYER_PAD), [positionList]);
    const geometry = useMemo(() => computeGeometry(visibleEdges, positions, centre), [visibleEdges, positions, centre]);
    const geometryByKey = useMemo(() => new Map(geometry.map((g) => [g.key, g])), [geometry]);
    const hoveredGeometry = hoveredEdge ? geometryByKey.get(hoveredEdge) : undefined;
    const summaries = useMemo(() => linkSummary(users, visibleEdges), [users, visibleEdges]);
    const missingPositions = users.some((u) => !positions[u]);

    // Stable callbacks for the memoised layers: they only touch setters and
    // refs, so a ref to the latest handler is all that's needed.
    const disconnectRef = useRef(disconnect);
    disconnectRef.current = disconnect;
    const nodeClickRef = useRef(handleNodeClick);
    nodeClickRef.current = handleNodeClick;
    const nodePointerDownRef = useRef(onNodePointerDown);
    nodePointerDownRef.current = onNodePointerDown;
    const nodeHandlers = useMemo(() => ({
        onPointerDown: (e: React.PointerEvent<HTMLDivElement>, name: string) => nodePointerDownRef.current(e, name),
        onActivate: (name: string) => nodeClickRef.current(name),
        onHover: (name: string, on: boolean) => setHoveredNode((h) => (on ? name : h === name ? null : h)),
    }), []);
    const edgeHandlers: EdgeHandlers = useMemo(() => {
        const boardPoint = (e: React.PointerEvent<SVGPathElement>): XY | null => {
            const rect = boardRef.current?.getBoundingClientRect();
            return rect ? { x: e.clientX - rect.left, y: e.clientY - rect.top } : null;
        };
        return {
            onEdgeEnter: (key: string, e: React.PointerEvent<SVGPathElement> | null) => {
                setHoveredEdge(key);
                // Keyboard focus has no pointer -- null means "anchor at the edge's midpoint".
                setTipPos(e ? boardPoint(e) : null);
            },
            onEdgeMove: (e: React.PointerEvent<SVGPathElement>) => setTipPos(boardPoint(e)),
            onEdgeLeave: () => { setHoveredEdge(null); setTipPos(null); },
            onEdgeActivate: (edge: Edge) => {
                if (readOnly) { return; }
                if (edge.manual) {
                    disconnectRef.current(edge);
                    setHoveredEdge(null);
                    setTipPos(null);
                } else {
                    notifications.show({ title: t('Not directly removable'), message: t('This comes from a shared group\'s Access list — remove one of them from that group instead.'), color: 'yellow' });
                }
            },
        };
    }, []); // eslint-disable-line react-hooks/exhaustive-deps

    // Which edges and users are picked out: a hovered connector, else the
    // hovered (or selected) user's connectors.
    const focusNode = dragging ? null : (hoveredNode ?? null);
    const activeGeometry = useMemo(() => {
        if (hoveredGeometry) { return [hoveredGeometry]; }
        if (!focusNode) { return []; }
        return geometry.filter((g) => g.edge.source === focusNode || g.edge.target === focusNode);
    }, [hoveredGeometry, focusNode, geometry]);
    const connectedToFocus = useMemo(() => {
        if (hoveredGeometry) { return new Set<string>([hoveredGeometry.edge.source, hoveredGeometry.edge.target]); }
        if (!focusNode) { return null; }
        const set = new Set<string>([focusNode]);
        activeGeometry.forEach((g) => { set.add(g.edge.source); set.add(g.edge.target); });
        return set;
    }, [hoveredGeometry, focusNode, activeGeometry]);
    const focusActive = activeGeometry.length > 0 || !!focusNode;

    // Tooltip placement in board px: at the pointer, or at the edge's
    // midpoint for keyboard focus; flipped left/up near the right/bottom.
    let tooltip: React.ReactNode = null;
    if (hoveredGeometry) {
        const anchor = tipPos ?? { x: hoveredGeometry.mid.x * zoom + pan.x, y: hoveredGeometry.mid.y * zoom + pan.y };
        const { edge } = hoveredGeometry;
        const flipX = anchor.x > (boardWidth || 1000) - 340;
        const flipY = anchor.y > BOARD_HEIGHT - 120;
        tooltip = (
            <Box
                pos="absolute"
                left={anchor.x + (flipX ? -14 : 14)}
                top={anchor.y + (flipY ? -14 : 14)}
                px={12}
                py={8}
                style={{
                    pointerEvents: 'none',
                    zIndex: 5,
                    maxWidth: 320,
                    transform: `translate(${flipX ? '-100%' : '0'}, ${flipY ? '-100%' : '0'})`,
                    background: 'var(--raven-surface)',
                    border: '1px solid var(--raven-border-strong)',
                    borderRadius: 'var(--mantine-radius-sm)',
                    boxShadow: '0 4px 14px rgba(0, 0, 0, 0.14)',
                }}
            >
                <Group gap={8} wrap="nowrap" mb={2}>
                    <Text size="sm" fw={600}>
                        {edge.type === 'solid' ? `${edge.source} ⇄ ${edge.target}` : `${edge.source} → ${edge.target}`}
                    </Text>
                    <Badge size="xs" variant="light" tt="none" color={!edge.manual ? 'gray' : edge.type === 'solid' ? 'indigo' : 'orange'}>{edgeTypeLabel(edge)}</Badge>
                </Group>
                <Text size="sm">
                    {edge.type === 'solid'
                        ? t('Both share data')
                        : t('{{b}} receives {{a}}\'s data', { a: edge.source, b: edge.target })}
                </Text>
                {(edge.source_groups?.length ?? 0) > 0 && (
                    <Text size="xs" c="dimmed">
                        {t('{{a}} → {{b}} via {{groups}}', { a: edge.source, b: edge.target, groups: edge.source_groups!.join(', ') })}
                    </Text>
                )}
                {(edge.target_groups?.length ?? 0) > 0 && (
                    <Text size="xs" c="dimmed">
                        {t('{{a}} → {{b}} via {{groups}}', { a: edge.target, b: edge.source, groups: edge.target_groups!.join(', ') })}
                    </Text>
                )}
                <Text size="xs" c="dimmed">
                    {edge.manual ? t('Click to disconnect') : t('Via a shared group\'s Access list — not directly removable here')}
                </Text>
            </Box>
        );
    }

    // "Click another user" preview: from the selected card toward the
    // pointer, snapping to the border of whichever card is under it.
    let preview: React.ReactNode = null;
    const selectedPos = selected ? positions[selected] : undefined;
    if (selectedPos && pointer) {
        const ca = nodeCenter(selectedPos);
        const inside = Math.abs(pointer.x - ca.x) <= NODE_WIDTH / 2 && Math.abs(pointer.y - ca.y) <= NODE_HEIGHT / 2;
        if (!inside) {
            const style: EdgeStyleKey = mode === 'solid' ? 'two' : 'one';
            const targetName = hoveredNode && hoveredNode !== selected ? hoveredNode : null;
            const targetPos = targetName ? positions[targetName] : undefined;
            const end = targetPos ? nodeCenter(targetPos) : pointer;
            const a = boxExit(ca, end, NODE_WIDTH, NODE_HEIGHT, BORDER_GAP);
            const b = targetPos ? boxExit(end, ca, NODE_WIDTH, NODE_HEIGHT, BORDER_GAP) : pointer;
            const marker = `url(#${markerId(markerPrefix, style)})`;
            preview = (
                <path
                    d={`M${a.x.toFixed(1)},${a.y.toFixed(1)} L${b.x.toFixed(1)},${b.y.toFixed(1)}`}
                    fill="none"
                    stroke={EDGE_COLOR[style]}
                    strokeWidth={EDGE_WIDTH_ACTIVE[style]}
                    strokeLinecap="round"
                    strokeDasharray={mode === 'solid' ? undefined : ONE_WAY_DASH}
                    markerStart={mode === 'solid' ? marker : undefined}
                    markerEnd={marker}
                    opacity={targetPos ? 1 : 0.7}
                    pointerEvents="none"
                />
            );
        }
    }

    const nodeElements = users.map((name) => {
        const pos = positions[name];
        if (!pos) { return null; }
        const isSelected = selected === name;
        return (
            <NodeCard
                key={name}
                name={name}
                pos={pos}
                summary={summaries[name]}
                selected={isSelected}
                target={!!selected && !isSelected && hoveredNode === name}
                hovered={hoveredNode === name}
                dimmed={!dragging && connectedToFocus !== null && !connectedToFocus.has(name) && !isSelected}
                readOnly={readOnly}
                connecting={!!selected}
                onPointerDown={nodeHandlers.onPointerDown}
                onActivate={nodeHandlers.onActivate}
                onHover={nodeHandlers.onHover}
            />
        );
    });

    // Small pills naming the kind of each picked-out connector (only those, so
    // a busy map never fills with labels).
    const pills = activeGeometry.length <= 40 ? activeGeometry.map((g) => (
        <Box
            key={`pill-${g.key}`}
            pos="absolute"
            left={g.mid.x}
            top={g.mid.y}
            style={{
                transform: 'translate(-50%, -50%)',
                padding: '1px 7px',
                borderRadius: 'var(--mantine-radius-sm)',
                border: `1px solid ${EDGE_COLOR[g.style]}`,
                background: 'var(--raven-surface)',
                color: EDGE_COLOR[g.style],
                fontSize: 11,
                fontWeight: 600,
                lineHeight: '16px',
                whiteSpace: 'nowrap',
                pointerEvents: 'none',
                zIndex: 3,
            }}
        >
            {edgeTypeLabel(g.edge)}
        </Box>
    )) : null;

    const linkRows = useMemo(() => {
        // Drawn connections first, then group-inherited ones.
        const sorted = [...visibleEdges].sort((x, y) => Number(y.manual) - Number(x.manual));
        return sorted;
    }, [visibleEdges]);
    const shownRows = showAllLinks ? linkRows : linkRows.slice(0, LIST_PREVIEW);

    // Dot spacing follows the zoom, but never packs tighter than 11px (a grey haze when zoomed out).
    let gridSize = 22 * zoom;
    while (gridSize < 11) { gridSize *= 2; }

    return (
        <Stack gap="md">
            <Group justify="space-between" gap="sm" wrap="wrap">
                <Group gap="xs" wrap="wrap">
                    <Text size="sm" c="dimmed">{t('Click a user, choose a connection type, then click another user:')}</Text>
                    <Tooltip label={READ_ONLY_LABEL} disabled={!readOnly}>
                        <div>
                            <SegmentedControl
                                size="xs"
                                value={mode}
                                disabled={readOnly}
                                onChange={(v) => setMode(v as 'solid' | 'dotted')}
                                data={[
                                    { label: t('⇄ Two-Way'), value: 'solid' },
                                    { label: t('→ One-Way'), value: 'dotted' },
                                ]}
                            />
                        </div>
                    </Tooltip>
                </Group>
                {inheritedCount > 0 && (
                    <Tooltip label={t('Connections implied by shared groups — off by default to keep the diagram readable')}>
                        <Switch
                            size="sm"
                            checked={showInherited}
                            onChange={(e) => setShowInherited(e.currentTarget.checked)}
                            label={t('Show {{count}} group-inherited', { count: inheritedCount })}
                        />
                    </Tooltip>
                )}
            </Group>

            {users.length === 0 ? (
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
                            <People20Regular style={{ width: 24, height: 24 }} />
                        </Box>
                        <Text fw={600} size="sm">{t('Nothing to map yet')}</Text>
                        <Text size="sm" c="dimmed" ta="center">
                            {scopeToUsernames ? t('No members in this group yet — add some above first.') : t('No users yet.')}
                        </Text>
                    </Stack>
                </Box>
            ) : (
                <Box
                    ref={boardRef}
                    pos="relative"
                    w="100%"
                    h={BOARD_HEIGHT}
                    role="group"
                    aria-label={t('Who Can See Whom')}
                    style={{
                        border: '1px solid var(--raven-border)',
                        borderRadius: 'var(--mantine-radius-md)',
                        overflow: 'hidden',
                        touchAction: 'none',
                        cursor: selected ? 'crosshair' : 'grab',
                        backgroundColor: 'var(--raven-bg)',
                        backgroundImage: 'radial-gradient(circle, var(--raven-border-strong) 1px, transparent 1.2px)',
                        backgroundSize: `${gridSize}px ${gridSize}px`,
                        backgroundPosition: `${pan.x}px ${pan.y}px`,
                    }}
                    onPointerDown={onBackgroundPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                    onPointerCancel={onPointerUp}
                    onPointerLeave={() => setPointer(null)}
                    onClick={(e) => { if (e.target === e.currentTarget) { setSelected(null); } }}
                >
                    {/* A zero-size, transformed anchor: every layer inside is sized to its own content. */}
                    <Box
                        pos="absolute"
                        left={0}
                        top={0}
                        w={0}
                        h={0}
                        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, transformOrigin: '0 0' }}
                    >
                        {/* Drawn beneath the cards: lines end just outside each card,
                            and any that pass behind a third user are hidden by it
                            rather than scribbled across its name. */}
                        {!missingPositions && (
                            <svg
                                width={svgBox.w}
                                height={svgBox.h}
                                viewBox={`${svgBox.x} ${svgBox.y} ${svgBox.w} ${svgBox.h}`}
                                style={{ position: 'absolute', left: svgBox.x, top: svgBox.y, pointerEvents: 'none', overflow: 'visible' }}
                            >
                                <ArrowMarkers prefix={markerPrefix} />
                                <g pointerEvents="none" opacity={focusActive ? DIM_OPACITY : 1} style={{ transition: 'opacity 120ms linear' }}>
                                    <BaseEdges geometry={geometry} markerPrefix={markerPrefix} />
                                </g>
                                <g pointerEvents="none">
                                    {activeGeometry.map((g) => <EdgeVisual key={g.key} g={g} active animate={g.key === hoveredEdge} markerPrefix={markerPrefix} />)}
                                </g>
                                <HitAreas geometry={geometry} {...edgeHandlers} />
                                {preview}
                            </svg>
                        )}

                        {pills}
                        {nodeElements}
                    </Box>

                    {tooltip}

                    {selected && (
                        <Group
                            gap={8}
                            wrap="nowrap"
                            pos="absolute"
                            top={10}
                            left="50%"
                            px={12}
                            py={6}
                            style={{ transform: 'translateX(-50%)', background: 'var(--raven-surface)', border: `1px solid ${SELECT_COLOR}`, borderRadius: 'var(--mantine-radius-sm)', boxShadow: '0 2px 8px rgba(0,0,0,0.1)', zIndex: 4, maxWidth: 'calc(100% - 24px)', cursor: 'default' }}
                            onPointerDown={(e) => e.stopPropagation()}
                            onClick={(e) => e.stopPropagation()}
                        >
                            <Text size="sm" truncate>
                                {t('Now click another user to connect')} <Text component="span" fw={600} size="sm">{selected}</Text>
                                <Text component="span" c="dimmed" size="sm"> · {t('click it again to cancel')}</Text>
                            </Text>
                            <ActionIcon size="sm" variant="subtle" color="gray" aria-label={t('Cancel')} onClick={() => setSelected(null)}>
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
                        style={{ background: 'var(--raven-surface)', border: '1px solid var(--raven-border)', borderRadius: 'var(--mantine-radius-sm)', zIndex: 4, maxWidth: 'calc(100% - 220px)', cursor: 'default' }}
                    >
                        <Group gap={6} wrap="nowrap">
                            <LegendLine style="two" />
                            <Text size="xs" c="dimmed">{t('Two-way')}</Text>
                        </Group>
                        <Group gap={6} wrap="nowrap">
                            <LegendLine style="one" />
                            <Text size="xs" c="dimmed">{t('One-way (arrow points at the receiver)')}</Text>
                        </Group>
                        {showInherited && inheritedCount > 0 && (
                            <Group gap={6} wrap="nowrap">
                                <LegendLine style="inherited" />
                                <Text size="xs" c="dimmed">{t('Via a shared group')}</Text>
                            </Group>
                        )}
                    </Group>

                    <ActionIcon.Group
                        pos="absolute"
                        right={10}
                        bottom={10}
                        style={{ zIndex: 4 }}
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
                    </ActionIcon.Group>
                </Box>
            )}

            {users.length > 0 && (
                <Stack gap={6}>
                    <Text size="sm" fw={600} c="dimmed">
                        {`${t('Links')}${visibleEdges.length > 0 ? ` (${visibleEdges.length})` : ''}`}
                    </Text>
                    {visibleEdges.length === 0 ? (
                        <Text size="sm" c="dimmed">
                            {readOnly
                                ? t('No direct links between these users.')
                                : t('No direct links yet — click one user on the map, then another, to connect them.')}
                        </Text>
                    ) : (
                        <>
                            {shownRows.map((edge) => {
                                const key = edgeKey(edge);
                                const hovered = hoveredEdge === key;
                                return (
                                    <Group
                                        key={key}
                                        gap="sm"
                                        wrap="nowrap"
                                        px="sm"
                                        py={8}
                                        bg="var(--raven-surface)"
                                        onMouseEnter={() => setHoveredEdge(key)}
                                        onMouseLeave={() => setHoveredEdge((k) => (k === key ? null : k))}
                                        style={{
                                            border: `1px solid ${hovered ? 'var(--raven-border-strong)' : 'var(--raven-border)'}`,
                                            borderRadius: 'var(--mantine-radius-sm)',
                                            transition: 'border-color var(--raven-transition)',
                                        }}
                                    >
                                        <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
                                            <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                                                <Text size="sm" fw={600} truncate>{edge.source}</Text>
                                                <Box c="dimmed" style={{ display: 'flex', flexShrink: 0 }} aria-label={edge.type === 'solid' ? t('both ways') : t('to')}>
                                                    {edge.type === 'solid' ? <ArrowSwap16Regular /> : <ArrowRight16Regular />}
                                                </Box>
                                                <Text size="sm" fw={600} truncate>{edge.target}</Text>
                                            </Group>
                                            <Text size="xs" c="dimmed">
                                                {edge.type === 'solid'
                                                    ? t('{{a}} and {{b}} exchange permitted TAK data (position, chat, and more) with each other — a consequence of this is that they can see each other on the map.', { a: edge.source, b: edge.target })
                                                    : t('{{b}} receives {{a}}\'s permitted TAK data (position, chat, and more) — including seeing them on the map — but {{a}} does not receive {{b}}\'s.', { a: edge.source, b: edge.target })}
                                            </Text>
                                        </Stack>
                                        <Badge variant="light" tt="none" color={!edge.manual ? 'gray' : edge.type === 'solid' ? 'indigo' : 'orange'} style={{ flexShrink: 0 }}>
                                            {edge.manual ? edgeTypeLabel(edge) : `${edge.type === 'solid' ? t('Two-way') : t('One-way')} · ${t('via a shared group')}`}
                                        </Badge>
                                        {edge.manual ? (
                                            <Tooltip label={readOnly ? READ_ONLY_LABEL : t('Remove this connection')}>
                                                <ActionIcon color="red" variant="subtle" style={{ flexShrink: 0 }} disabled={readOnly} aria-label={t('Remove this connection')} onClick={() => disconnectRef.current(edge)}>
                                                    <Delete20Regular style={{ width: 16, height: 16 }} />
                                                </ActionIcon>
                                            </Tooltip>
                                        ) : (
                                            <Tooltip label={t('Comes from a shared group\'s Access list — remove one of them from that group instead')}>
                                                <ActionIcon color="gray" variant="subtle" style={{ flexShrink: 0 }} disabled aria-label={t('Not directly removable')}>
                                                    <Delete20Regular style={{ width: 16, height: 16 }} />
                                                </ActionIcon>
                                            </Tooltip>
                                        )}
                                    </Group>
                                );
                            })}
                            {linkRows.length > LIST_PREVIEW && (
                                <Button variant="subtle" color="gray" size="compact-sm" style={{ alignSelf: 'flex-start' }} onClick={() => setShowAllLinks((v) => !v)}>
                                    {showAllLinks ? t('Show fewer') : t('Show all {{count}}', { count: linkRows.length })}
                                </Button>
                            )}
                        </>
                    )}
                </Stack>
            )}
        </Stack>
    );
}
