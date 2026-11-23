import { Alert } from '@mantine/core';
import { Eye20Regular } from '@fluentui/react-icons';
import { t } from 'i18next';
import { isReadOnlyAdmin } from '../adminAccess';

/** Shown at the top of an admin page to a read-only admin; renders nothing for anyone else. */
export function ReadOnlyNotice() {
    if (!isReadOnlyAdmin()) {return null;}
    return (
        <Alert variant="light" color="gray" icon={<Eye20Regular />} mb="md">
            {t('You have read-only access. Changes can only be made by a full administrator, and IP addresses are hidden.')}
        </Alert>
    );
}
