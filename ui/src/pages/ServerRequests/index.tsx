import React, { useState } from 'react';
import { Tabs } from '@mantine/core';
import {
    Bug20Regular,
    CalendarClock20Regular,
    ClipboardTaskListLtr20Regular,
    DatabaseSettings20Regular,
    ShieldLock20Regular,
} from '@fluentui/react-icons';
import { useSearchParams } from 'react-router';
import { useTranslation } from 'react-i18next';
import ScheduledJobsTab from './ScheduledJobsTab';
import SecurityAuditTab from './SecurityAuditTab';
import AdminActionsTab from './AdminActionsTab';
import ErrorLogTab from './ErrorLogTab';
import DataRetentionTab from './DataRetentionTab';
import { PageHeader } from '@/components/ui';
import classes from './ServerRequests.module.css';
import { ReadOnlyNotice } from '../../components/ReadOnlyNotice';
import { isReadOnlyAdmin } from '../../adminAccess';
import { AdminOnlyNotice } from '../../components/AdminOnlyNotice';

const TABS = ['jobs', 'audit', 'admin', 'errors', 'retention'] as const;
type TabValue = (typeof TABS)[number];

function isTab(value: string | null): value is TabValue {
    return value !== null && (TABS as readonly string[]).includes(value);
}

export default function ServerRequests() {
    const { t } = useTranslation();
    const [searchParams, setSearchParams] = useSearchParams();
    const requested = searchParams.get('tab');
    // The security posture report names admins still on the default password: full admins only
    const readOnly = isReadOnlyAdmin();
    const tab: TabValue = isTab(requested) ? requested : 'jobs';
    // Mount each tab the first time it is opened, then keep it mounted so
    // filters, loaded data and the error-log buffer survive tab switches.
    const [visited, setVisited] = useState<Set<TabValue>>(() => new Set([tab]));

    function changeTab(value: string | null) {
        if (!isTab(value)) { return; }
        setVisited((prev) => (prev.has(value) ? prev : new Set(prev).add(value)));
        setSearchParams(value === 'jobs' ? {} : { tab: value }, { replace: true });
    }

    return (
        <>
            <PageHeader
                title={t('Server Requests')}
                description={t('Background jobs, sign-in and enrollment audit, admin actions, the server error log and data retention')}
            />
            <ReadOnlyNotice />

            <Tabs value={tab} onChange={changeTab} classNames={{ list: classes.tabList, tab: classes.tab }}>
                <Tabs.List mb="lg">
                    <Tabs.Tab value="jobs" leftSection={<CalendarClock20Regular />}>{t('Scheduled Jobs')}</Tabs.Tab>
                    <Tabs.Tab value="audit" leftSection={<ShieldLock20Regular />}>{t('Security Audit')}</Tabs.Tab>
                    <Tabs.Tab value="admin" leftSection={<ClipboardTaskListLtr20Regular />}>{t('Admin Actions')}</Tabs.Tab>
                    <Tabs.Tab value="errors" leftSection={<Bug20Regular />}>{t('Error Log')}</Tabs.Tab>
                    <Tabs.Tab value="retention" leftSection={<DatabaseSettings20Regular />}>{t('Data Retention')}</Tabs.Tab>
                </Tabs.List>

                <Tabs.Panel value="jobs">{visited.has('jobs') && <ScheduledJobsTab />}</Tabs.Panel>
                <Tabs.Panel value="audit">
                    {/* Never loaded for read-only admins: the report names admins still on the default password */}
                    {readOnly ? <AdminOnlyNotice /> : visited.has('audit') && <SecurityAuditTab />}
                </Tabs.Panel>
                <Tabs.Panel value="admin">{visited.has('admin') && <AdminActionsTab />}</Tabs.Panel>
                <Tabs.Panel value="errors">{visited.has('errors') && <ErrorLogTab active={tab === 'errors'} />}</Tabs.Panel>
                <Tabs.Panel value="retention">{visited.has('retention') && <DataRetentionTab />}</Tabs.Panel>
            </Tabs>
        </>
    );
}
