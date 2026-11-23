import React from 'react';
import { Box, BoxProps, Group, Text } from '@mantine/core';
import classes from './ui.module.css';

export interface PanelProps extends BoxProps {
    /** Optional small heading row, separated from the body by a hairline. */
    title?: React.ReactNode;
    actions?: React.ReactNode;
    children?: React.ReactNode;
}

/**
 * The ONE bordered surface: 1px hairline, surface background, 8px corners, no
 * shadow. Never put a Panel (or Paper/Card) inside another Panel.
 */
export function Panel({ title, actions, children, className, p = 'md', ...rest }: PanelProps) {
    return (
        <Box className={[classes.panel, className].filter(Boolean).join(' ')} p={p} {...rest}>
            {(title || actions) && (
                <Group justify="space-between" gap="sm" wrap="nowrap" className={classes.panelHeader}>
                    {title && <Text fw={600}>{title}</Text>}
                    {actions && <Group gap="xs">{actions}</Group>}
                </Group>
            )}
            {children}
        </Box>
    );
}
