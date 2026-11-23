import React from 'react';
import { Tooltip } from '@mantine/core';
import { t } from 'i18next';
import { isReadOnlyAdmin } from '../adminAccess';

export const READ_ONLY_LABEL = t('Read-only access: only a full administrator can change this');

/**
 * Wraps a control a read-only admin can see but not use (render it with
 * `disabled={readOnly}`): explains why it is greyed out. Disabled buttons don't
 * fire pointer events, hence the span. Renders the child untouched for everyone else.
 */
export function ReadOnlyTip({ children }: { children: React.ReactNode }) {
    if (!isReadOnlyAdmin()) { return <>{children}</>; }
    return (
        <Tooltip label={READ_ONLY_LABEL} withinPortal>
            <span style={{ display: 'inline-flex' }}>{children}</span>
        </Tooltip>
    );
}
