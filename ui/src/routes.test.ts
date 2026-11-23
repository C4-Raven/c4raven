import { describe, expect, it } from 'vitest';
import { ADMIN_AREAS, adminAreaForPath } from './adminAccess';
import { allRoutes, isRedirect, routeSections } from './routes';

describe('route table', () => {
  it('has no duplicate paths', () => {
    const paths = allRoutes.map((r) => r.path);
    expect(new Set(paths).size).toBe(paths.length);
  });

  it('serves every page the layout used to serve', () => {
    const paths = allRoutes.map((r) => r.path).sort();
    expect(paths).toEqual(
      [
        '/',
        '/dashboard',
        '/map',
        '/euds',
        '/casevac',
        '/missions',
        '/missions/:name',
        '/data_packages',
        '/video_streams',
        '/video_recordings',
        '/tfa_setup',
        '/users',
        '/groups',
        '/server-requests',
        '/jobs',
        '/plugin_updates',
        '/device_profiles',
        '/link_account',
        '/federation_hub',
        '/supporting_documents',
        '/meshtastic',
      ].sort()
    );
  });

  it('gates exactly the admin pages, each with the area adminAccess knows it by', () => {
    for (const entry of allRoutes) {
      if (isRedirect(entry)) {
        continue;
      }
      expect(entry.adminArea ?? null, entry.path).toBe(adminAreaForPath(entry.path));
    }
    const gated = allRoutes.flatMap((r) => (!isRedirect(r) && r.adminArea ? [r.adminArea] : []));
    expect(gated.sort()).toEqual(ADMIN_AREAS.map((a) => a.area).sort());
  });

  it('keeps admin pages in the administration section', () => {
    for (const { section, entries } of routeSections) {
      for (const entry of entries) {
        const gated = !isRedirect(entry) && !!entry.adminArea;
        expect(gated, entry.path).toBe(section === 'administration');
      }
    }
  });

  it('redirects the old scheduler address to Server Requests', () => {
    const jobs = allRoutes.find((r) => r.path === '/jobs');
    expect(jobs && isRedirect(jobs) ? jobs.redirectTo : null).toBe('/server-requests');
  });

  it('points redirects at a page that exists', () => {
    const pages = new Set(allRoutes.filter((r) => !isRedirect(r)).map((r) => r.path));
    for (const entry of allRoutes) {
      if (isRedirect(entry)) {
        expect(pages.has(entry.redirectTo), entry.path).toBe(true);
      }
    }
  });
});
