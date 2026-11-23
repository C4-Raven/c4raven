import React from 'react';
import { Group, Text, Title } from '@mantine/core';
import classes from './ui.module.css';

export interface PageHeaderProps {
    title: React.ReactNode;
    description?: React.ReactNode;
    /** Right-aligned buttons (primary action last). */
    actions?: React.ReactNode;
}

/** Large page title at the top of a page, sitting directly on the background. */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
    return (
        <Group justify="space-between" align="flex-end" gap="md" className={classes.pageHeader}>
            <div>
                <Title order={1} className={classes.pageTitle}>{title}</Title>
                {description && <Text c="dimmed" size="sm" mt={4}>{description}</Text>}
            </div>
            {actions && <Group gap="xs">{actions}</Group>}
        </Group>
    );
}
