import React from 'react';
import { Box, BoxProps, MantineColor } from '@mantine/core';
import classes from './ui.module.css';

export interface StatTileProps extends BoxProps {
    /** A Fluent icon element, e.g. <People20Regular />. Rendered at 24px. */
    icon?: React.ReactNode;
    value: React.ReactNode;
    label: React.ReactNode;
    /** Any Mantine theme colour name; the tile is a muted tint of it. */
    color?: MantineColor;
}

/** Flat, square, tinted quick-stat tile (readable in light and dark). */
export function StatTile({ icon, value, label, color = 'ravenSteel', className, style, ...rest }: StatTileProps) {
    return (
        <Box
          className={[classes.statTile, className].filter(Boolean).join(' ')}
          style={{ '--tile-color': `var(--mantine-color-${color}-filled)`, ...(style as React.CSSProperties) } as React.CSSProperties}
          {...rest}
        >
            {icon && <div className={classes.statIcon} aria-hidden>{icon}</div>}
            <div className={classes.statValue}>{value}</div>
            <div className={classes.statLabel}>{label}</div>
        </Box>
    );
}
