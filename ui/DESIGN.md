# Raven UI design rules

Follow these exactly when restyling a page. Tokens live in `src/theme.ts` (Mantine theme) and `src/global.css` (`--raven-*` CSS variables, defined per colour scheme).

## Tokens
| Token | Light | Dark | Use |
|---|---|---|---|
| `--raven-bg` | `#f3f3f3` | `dark.7` `#0e0f11` | page background (content sits directly on it) |
| `--raven-chrome` | `#ebebeb` | `dark.8` `#1c1d21` | header and sidebar |
| `--raven-surface` | `#ffffff` | `dark.6` `#17181b` | Panel / Paper / Card / modal / dropdown background |
| `--raven-border` | `#e5e5e5` | `dark.4` | hairline borders and dividers |
| `--raven-border-strong` | `#d1d1d1` | `dark.3` | input-like outlines |
| `--raven-hover` / `--raven-selected` | 4.5% / 6.5% black | 5% / 8% white | hover and selected fills |
| `--raven-text-muted` | `#5f5f5f` | `dark.1` | secondary text (`c="dimmed"` is fine too) |
| `--raven-accent` | primary (`ravenSteel`) | primary | accent bars, focus rings, active icons |

Radius (Raven UI geometry): 6px (`sm`, the default) for controls — buttons, inputs, checkboxes, badges, tooltips, map controls; switches are pill-shaped; 8px (`md`) for stand-alone containers — `Panel`, `StatTile`, cards, modals, menus, popovers, the login card; 0 where straight edges meet other edges — the sidebar and header (including nav items and the avatar), table cells, tab strips, hairline row lists, drawers. Shadows: none on page content; the theme gives modals, menus and popovers one small shadow. Fonts: Segoe UI Variable stack (set in the theme; do not set `ff`). Primary colour `ravenSteel` (`color` prop default).

## Rules
- **Corners come from the theme.** Don't pass `radius` props; the defaults already give controls 6px, switches a pill shape and containers 8px. Keep the shell (sidebar, header) square.
- **Flat.** No gradients (`variant="gradient"`, `linear-gradient`, `raven-mesh`), no glow, no `shadow` on Paper/Card.
- **One level of surface.** Content sits on the page background. At most ONE bordered surface (`Panel`) around a group; never Panel/Paper/Card inside another. Inside a Panel, separate content with spacing, `Divider`, or small uppercase labels.
- **Both themes.** No hard-coded dark colours (`bg="dark.7"`, `c="white"`, `color="white"`, `var(--mantine-color-dark-4)` borders, `#1c1d21`…). Use the `--raven-*` variables, `c="dimmed"`, or `light-dark(a, b)` in CSS modules.
- **No new animation.** Only colour transitions of at most ~100ms (`var(--raven-transition)`). No transforms on hover, slide-ins, shimmer or scale. Existing `raven-pulse` live dots may stay.
- **Focus stays visible.** Do not remove outlines.
- Keep every API call, route, permission check and `t()` string unchanged.
- Legacy classes `raven-surface` / `raven-surface--tile` are flattened in global.css; replace them with `Panel` (or nothing) as you restyle. `raven-cta` no longer exists, so remove it.

## Building blocks (`import { PageHeader, Section, Panel, StatTile } from '@/components/ui';`)
```tsx
<PageHeader title={t('Users')} description={t('Manage accounts')} actions={<Button leftSection={<Add20Regular />}>{t('Add')}</Button>} />
<SimpleGrid cols={{ base: 2, md: 4 }} spacing="md" mb="xl">
  <StatTile icon={<People20Regular />} value={42} label={t('Users')} color="blue" />   {/* blue, teal, yellow, violet, red, gray… */}
</SimpleGrid>
<Section title={t('Accounts')} actions={…} divider>   {/* NOT a box: spacing + optional hairline */}
  <Panel p={0}><DataTable … /></Panel>                {/* the ONE bordered surface */}
</Section>
<Panel title={t('Details')} actions={…}>…</Panel>
```
- `PageHeader`: one per page, at the top. Page title 28px/600.
- `Section`: 20px/600 title, `divider` adds a hairline on top. Use for each logical group.
- `Panel`: 1px hairline, surface background, 8px corners (content is clipped to them), no shadow. Accepts Box props (`p`, `mb`, …), plus optional `title`/`actions` header row.
- `StatTile`: flat tinted tile (tint of any Mantine colour, readable in both themes). Put a row of them in a `SimpleGrid`.
- Buttons: primary = `variant="filled"` (default); secondary = `variant="default"`; low emphasis = `variant="subtle"`. Destructive = `color="red"`.

## Icons: Fluent UI System Icons (`@fluentui/react-icons`)
Named imports of `<Name>20Regular`, or `<Name>20Filled` for selected/emphasis states. They render at 20px; to resize use `style={{ width: 16, height: 16 }}` (or a CSS class), not `size`/`stroke` props. Notification `icon:` takes `<Dismiss20Regular />` directly. Replace Tabler imports in each file you touch:

| Tabler | Fluent | Tabler | Fluent | Tabler | Fluent |
|---|---|---|---|---|---|
| IconX | Dismiss | IconCheck | Checkmark | IconAlertTriangle | Warning |
| IconTrash | Delete | IconDownload | ArrowDownload | IconAlertCircle | ErrorCircle |
| IconRefresh | ArrowClockwise | IconPlus | Add | IconCircleMinus | SubtractCircle |
| IconCircleCheck | CheckmarkCircle | IconInfoCircle | Info | IconQrcode | QrCode |
| IconCertificate | Certificate | IconCopy | Copy | IconPlugConnected | PlugConnected |
| IconPlugConnectedX | PlugDisconnected | IconPlayerPlay | Play | IconChevronDown | ChevronDown |
| IconChevronRight | ChevronRight | IconFiles | DocumentMultiple | IconDeviceFloppy | Save |
| IconUsers | People | IconUsersGroup | PeopleTeam | IconUser | Person |
| IconUserPlus | PersonAdd | IconUserCog | PersonSettings | IconUsersMinus | PersonDelete |
| IconDeviceMobile | Phone | IconUpload | ArrowUpload | IconSearch | Search |
| IconPuzzle | PuzzlePiece | IconKey | Key | IconEdit / IconPencil | Edit |
| IconClock | Clock | IconCirclePlus | AddCircle | IconCircleX | DismissCircle |
| IconArrowLeft | ArrowLeft | IconArrowRight | ArrowRight | IconArrowBackUp | ArrowUndo |
| IconArrowUpRight | ArrowUpRight | IconArrowDownLeft | ArrowDownLeft | IconArrowsExchange | ArrowSwap |
| IconLink | Link | IconDatabase | Database | IconTool | Wrench |
| IconPackage | Box | IconLock | LockClosed | IconEye | Eye |
| IconAffiliate | Organization | IconAdjustments | Options | IconSettings | Settings |
| IconShieldX | ShieldDismiss | IconShieldLock | ShieldLock | IconCalendarEvent | CalendarLtr |
| IconZoomIn | ZoomIn | IconZoomOut | ZoomOut | IconZoomReset | ArrowReset |
| IconWorld | Globe | IconNetwork | NetworkCheck | IconServer2 / IconServerBolt | Server |
| IconMapPin | Location | IconFolder | Folder | IconFileZip | FolderZip |
| IconFileImport | ArrowImport | IconExternalLink | Open | IconDots | MoreHorizontal |
| IconMap | Map | IconFlag | Flag | IconMovie | VideoClip |
| IconBroadcast | Live | IconFileText | DocumentText | IconLogout | SignOut |

Not listed? Search the exports: `node -e 'console.log(Object.keys(require("@fluentui/react-icons")).filter(k=>/^Shield.*20Regular$/.test(k)))'`.

## Shell (already done; for reference)
Sidebar sections GENERAL / ADMIN (account functions are admin-only: truststore and iTAK QR under Users → Server connection, ATAK QR under Users → Enroll device), Raven UI selected item (fill plus 3px accent bar), dark mode switch and signed-in user at the bottom. The header has the burger, logo, **2FA** (to `/tfa_setup`) and **Log Out**. The colour scheme is stored in localStorage `raven-color-scheme` (default dark) and survives logout (`clearSession()` in `components/ui`).
