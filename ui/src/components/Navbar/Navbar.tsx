import React, { useEffect, useState } from 'react';
import {
    Anchor,
    Avatar,
    Group,
    NavLink,
    ScrollArea,
    Switch,
    Text,
    useComputedColorScheme,
    useMantineColorScheme,
} from '@mantine/core';
import { Link, useLocation } from 'react-router';
import {
    type FluentIcon,
    Board20Filled,
    Board20Regular,
    Box20Filled,
    Box20Regular,
    DocumentText20Filled,
    DocumentText20Regular,
    Flag20Filled,
    Flag20Regular,
    Link20Filled,
    Link20Regular,
    Live20Filled,
    Live20Regular,
    Map20Filled,
    Map20Regular,
    Organization20Filled,
    Organization20Regular,
    People20Filled,
    People20Regular,
    PeopleTeam20Filled,
    PeopleTeam20Regular,
    Phone20Filled,
    Phone20Regular,
    PhoneLaptop20Filled,
    PhoneLaptop20Regular,
    PuzzlePiece20Filled,
    PuzzlePiece20Regular,
    Router20Filled,
    Router20Regular,
    Server20Filled,
    Server20Regular,
    VideoClip20Filled,
    VideoClip20Regular,
    WeatherMoon20Regular,
} from '@fluentui/react-icons';
import { t } from 'i18next';
import classes from './Navbar.module.css';
import AboutModal from '../AboutModal';
import { ADMIN_AREAS, canSeeArea, isAdminViewer, isSuperAdmin, refreshAdminAccess } from '../../adminAccess';
import { useFeatures } from '../../features';

interface NavItemDef {
    link: string;
    label: string;
    icon: FluentIcon;
    activeIcon: FluentIcon;
}

const navbarLinks: NavItemDef[] = [
    { link: '/dashboard', label: t('Dashboard'), icon: Board20Regular, activeIcon: Board20Filled },
    { link: '/map', label: t('Map'), icon: Map20Regular, activeIcon: Map20Filled },
    { link: '/euds', label: t('EUDs'), icon: Phone20Regular, activeIcon: Phone20Filled },
    { link: '/data_packages', label: t('Data Packages'), icon: Box20Regular, activeIcon: Box20Filled },
    { link: '/video_streams', label: t('Video Streams'), icon: Live20Regular, activeIcon: Live20Filled },
    { link: '/video_recordings', label: t('Video Recordings'), icon: VideoClip20Regular, activeIcon: VideoClip20Filled },
    { link: '/missions', label: t('Data Sync'), icon: Flag20Regular, activeIcon: Flag20Filled },
];

const adminLinks: NavItemDef[] = [
    { link: '/users', label: t('Users'), icon: People20Regular, activeIcon: People20Filled },
    { link: '/groups', label: t('Groups'), icon: PeopleTeam20Regular, activeIcon: PeopleTeam20Filled },
    { link: '/server-requests', label: t('Server Requests'), icon: Server20Regular, activeIcon: Server20Filled },
    { link: '/plugin_updates', label: t('Plugins'), icon: PuzzlePiece20Regular, activeIcon: PuzzlePiece20Filled },
    { link: '/device_profiles', label: t('Device Profiles'), icon: PhoneLaptop20Regular, activeIcon: PhoneLaptop20Filled },
    { link: '/link_account', label: t('Link TAK.gov Account'), icon: Link20Regular, activeIcon: Link20Filled },
    { link: '/federation_hub', label: t('Federation Hub'), icon: Organization20Regular, activeIcon: Organization20Filled },
    { link: '/supporting_documents', label: t('Supporting Documents'), icon: DocumentText20Regular, activeIcon: DocumentText20Filled },
    { link: '/meshtastic', label: t('Meshtastic'), icon: Router20Regular, activeIcon: Router20Filled },
];

export interface NavbarProps {
    /** Called after a nav link is followed (closes the mobile drawer). */
    onNavigate?: () => void;
}

function SectionLabel({ children }: { children: React.ReactNode }) {
    return <div className={classes.sectionLabel}>{children}</div>;
}

export default function Navbar({ onNavigate }: NavbarProps) {
    // Bumped after /api/me is re-read, so a superadmin's changes show without signing in again
    const [, setAccessVersion] = useState(0);
    const [aboutOpen, setAboutOpen] = useState(false);
    const superAdmin = isSuperAdmin();
    const readOnlyAdmin = isAdminViewer();
    const features = useFeatures();
    const visibleAdminLinks = adminLinks.filter((item) => {
        // Meshtastic is off unless the server says it is on
        if (item.link === '/meshtastic' && features?.meshtastic !== true) {return false;}
        const area = ADMIN_AREAS.find((a) => a.link === item.link);
        return area ? canSeeArea(area.area) : superAdmin;
    });
    const showAdmin = visibleAdminLinks.length > 0;
    // Hidden only once the server has said so; the page itself still waits for the answer
    const visibleLinks = navbarLinks.filter((item) => item.link !== '/map' || features?.map !== false);
    const username = localStorage.getItem('username') || '';
    const location = useLocation();
    const { setColorScheme } = useMantineColorScheme();
    const colorScheme = useComputedColorScheme('dark');

    useEffect(() => {
        refreshAdminAccess().then(() => setAccessVersion((v) => v + 1));
    }, []);

    const renderLink = (item: NavItemDef) => {
        const active = location.pathname === item.link;
        const Icon = active ? item.activeIcon : item.icon;
        return (
            <NavLink
              className={classes.link}
              component={Link}
              key={item.link}
              active={active}
              to={item.link}
              label={item.label}
              leftSection={<Icon className={classes.linkIcon} />}
              onClick={onNavigate}
            />
        );
    };

    return (
        <div className={classes.nav}>
            <ScrollArea type="hover" scrollbarSize={6} className={classes.scroll}>
                <SectionLabel>{t('General')}</SectionLabel>
                {visibleLinks.map(renderLink)}

                {showAdmin && (
                    <>
                        <SectionLabel>{readOnlyAdmin ? t('Admin (read-only)') : t('Admin')}</SectionLabel>
                        {visibleAdminLinks.map(renderLink)}
                    </>
                )}
            </ScrollArea>

            <div className={classes.footer}>
                <Group justify="space-between" wrap="nowrap" className={classes.footerRow}>
                    <Group gap={12} wrap="nowrap">
                        <WeatherMoon20Regular className={classes.linkIcon} />
                        <Text size="sm">{t('Dark Mode')}</Text>
                    </Group>
                    <Switch
                      size="sm"
                      aria-label={t('Dark Mode')}
                      checked={colorScheme === 'dark'}
                      onChange={(e) => setColorScheme(e.currentTarget.checked ? 'dark' : 'light')}
                    />
                </Group>
                <Group gap="sm" wrap="nowrap" className={classes.user}>
                    <Avatar size={32} color="ravenSteel" variant="filled">
                        {(username || '?').slice(0, 2).toUpperCase()}
                    </Avatar>
                    <div className={classes.userText}>
                        <Text size="sm" fw={600} truncate>{username}</Text>
                        {superAdmin && <Text size="xs" c="dimmed">{t('Admin')}</Text>}
                        {readOnlyAdmin && <Text size="xs" c="dimmed">{t('Read-only admin')}</Text>}
                    </div>
                </Group>
                <Anchor component="button" type="button" size="xs" c="dimmed" className={classes.about} onClick={() => setAboutOpen(true)}>
                    {t('About & licence')}
                </Anchor>
            </div>
            <AboutModal opened={aboutOpen} onClose={() => setAboutOpen(false)} />
        </div>
    );
}
