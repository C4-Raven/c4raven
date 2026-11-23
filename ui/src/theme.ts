import { createTheme, CSSVariablesResolver, MantineColorsTuple } from '@mantine/core';

// Raven steel: a muted, slightly cool blue used as the primary colour. Each
// step's luminance is tuned so text-on-fill contrast stays in the same range
// as before (shade 6 on white is ~4.7:1, shade 7 ~5.9:1).
const ravenSteel: MantineColorsTuple = [
    '#eef3fb',
    '#dae4f2',
    '#b5c8e1',
    '#8caace',
    '#6990bd',
    '#5180b1',
    '#4977aa',
    '#3c6794',
    '#345a86',
    '#2c4d77',
];

// Near-black dark palette. dark.7 = page background, dark.8 = chrome
// (header/sidebar), dark.6 = panel surface, dark.4 = hairline border.
const dark: MantineColorsTuple = [
    '#C1C2C5',
    '#A6A7AB',
    '#909296',
    '#5C5F66',
    '#373A40',
    '#2C2E33',
    '#17181b',
    '#0e0f11',
    '#1c1d21',
    '#000000',
];

const FONT_TEXT =
    '"Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif';
const FONT_DISPLAY =
    '"Segoe UI Variable Display", "Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", Arial, sans-serif';

// One small, subtle shadow for overlays (modals, menus, popovers). Cards and
// panels never use a shadow.
const OVERLAY_SHADOW = '0 4px 16px rgba(0, 0, 0, 0.18)';

// Raven UI corner radius (modelled on Microsoft's published desktop geometry
// guidance, with slightly softer controls): 6px for in-page controls (buttons, inputs, checkboxes, badges,
// tooltips), 8px for containers that float or stand alone (dialogs, menus,
// popovers, cards/panels), and 0 where straight edges meet other edges (the
// app shell's sidebar and header, nav items, tables and tab strips).
const SHARP = { radius: 0 };
const CONTROL = { radius: 'sm' };
const CONTAINER = { radius: 'md' };

export const theme = createTheme({
    primaryColor: 'ravenSteel',
    primaryShade: { light: 6, dark: 7 },
    colors: { ravenSteel, dark },
    fontFamily: FONT_TEXT,
    fontFamilyMonospace: '"Cascadia Mono", Consolas, ui-monospace, monospace',
    headings: { fontFamily: FONT_DISPLAY, fontWeight: '600' },
    defaultRadius: 'sm',
    radius: { xs: '2px', sm: '6px', md: '8px', lg: '8px', xl: '12px' },
    shadows: {
        xs: OVERLAY_SHADOW,
        sm: OVERLAY_SHADOW,
        md: OVERLAY_SHADOW,
        lg: OVERLAY_SHADOW,
        xl: OVERLAY_SHADOW,
    },
    focusRing: 'auto',
    components: {
        // Plain objects (not Component.extend) so the theme does not pull
        // every component into the entry chunk. Components whose built-in
        // default is a pill (62.5rem) rather than the theme radius need an
        // explicit value. Switches are pill-shaped toggles; the sidebar's
        // avatar stays square to match the shell.
        Avatar: { defaultProps: SHARP },
        Switch: { defaultProps: { radius: 'xl' } },
        Badge: { defaultProps: CONTROL },
        Slider: { defaultProps: CONTROL },
        Button: { defaultProps: CONTROL },
        ActionIcon: { defaultProps: CONTROL },
        Tooltip: { defaultProps: CONTROL },
        Paper: { defaultProps: { ...CONTAINER, shadow: undefined } },
        Card: { defaultProps: CONTAINER },
        Modal: { defaultProps: { ...CONTAINER, shadow: 'md' } },
        Menu: { defaultProps: { ...CONTAINER, shadow: 'md' } },
        Popover: { defaultProps: { ...CONTAINER, shadow: 'md' } },
        HoverCard: { defaultProps: { ...CONTAINER, shadow: 'md' } },
        Notification: { defaultProps: CONTAINER },
    },
});

// Raven UI light: #f3f3f3 page, #fff surfaces, #e5e5e5 hairlines, #1b1b1b
// text. Dark keeps the near-black palette above.
export const cssVariablesResolver: CSSVariablesResolver = () => ({
    variables: {},
    light: {
        '--mantine-color-body': '#f3f3f3',
        '--mantine-color-text': '#1b1b1b',
        '--mantine-color-default-border': '#e5e5e5',
    },
    dark: {},
});
