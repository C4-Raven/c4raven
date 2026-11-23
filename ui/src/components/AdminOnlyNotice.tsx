import { Alert } from '@mantine/core';
import { LockClosed20Regular } from '@fluentui/react-icons';
import { t } from 'i18next';

/** Stands in for a tab or panel a read-only admin can see but whose content the server keeps from them. */
export function AdminOnlyNotice() {
    return (
        <Alert variant="light" color="gray" icon={<LockClosed20Regular />}>
            {t('Only a full administrator can view this.')}
        </Alert>
    );
}
