import { useEffect, useState, type ReactNode } from 'react';
import { Box, Button, Text, Title } from '@mantine/core';
import { Home20Regular, LockClosed20Regular } from '@fluentui/react-icons';
import { Link } from 'react-router';
import { t } from 'i18next';
import { type AdminArea, canSeeArea, refreshAdminAccess } from '../adminAccess';

/**
 * Renders an admin page only for someone allowed to see it: a full administrator, or a
 * read-only admin a superadmin switched that area on for. Everyone else gets a short
 * "no access" message instead of a page whose every request the server refuses.
 */
export function AdminAreaGuard({ area, children }: { area: AdminArea; children: ReactNode }) {
    const [allowed, setAllowed] = useState(() => canSeeArea(area));

    useEffect(() => {
        const now = canSeeArea(area);
        setAllowed(now);
        if (now) {return undefined;}
        // The stored access may be stale (a superadmin just switched this area on): check once more
        let cancelled = false;
        refreshAdminAccess().then(() => {
            if (!cancelled) {setAllowed(canSeeArea(area));}
        });
        return () => { cancelled = true; };
    }, [area]);

    if (allowed) {return <>{children}</>;}
    return <NoAccess />;
}

export function NoAccess() {
    return (
        <Box py={80} px="md" ta="center" maw={520} mx="auto">
            <LockClosed20Regular style={{ width: 40, height: 40, opacity: 0.6 }} aria-hidden />
            <Title order={2} mt="md" mb="xs">{t("You don't have access to this page")}</Title>
            <Text c="dimmed" mb="lg">
                {t('Ask a full administrator if you need to see it.')}
            </Text>
            <Button component={Link} to="/dashboard" variant="default" leftSection={<Home20Regular />}>
                {t('Back to the dashboard')}
            </Button>
        </Box>
    );
}
