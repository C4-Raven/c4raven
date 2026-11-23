import React from 'react';
import { Box, BoxProps, Group, Text, Title } from '@mantine/core';
import classes from './ui.module.css';

export interface SectionProps extends BoxProps {
    title?: React.ReactNode;
    description?: React.ReactNode;
    /** Right-aligned controls on the title row. */
    actions?: React.ReactNode;
    /** Draw a hairline above the section (use between unrelated sections). */
    divider?: boolean;
    children?: React.ReactNode;
}

/**
 * A titled block of page content. NOT a box: separation comes from spacing
 * (and optionally a hairline), never from a border or background.
 */
export function Section({ title, description, actions, divider, children, className, ...rest }: SectionProps) {
    const cls = [classes.section, divider ? classes.sectionDivider : '', className].filter(Boolean).join(' ');
    return (
        <Box component="section" className={cls} {...rest}>
            {(title || actions) && (
                <Group justify="space-between" align="flex-end" gap="md" className={classes.sectionHeader}>
                    <div>
                        {title && <Title order={2} className={classes.sectionTitle}>{title}</Title>}
                        {description && <Text c="dimmed" size="sm" mt={2}>{description}</Text>}
                    </div>
                    {actions && <Group gap="xs">{actions}</Group>}
                </Group>
            )}
            {children}
        </Box>
    );
}
