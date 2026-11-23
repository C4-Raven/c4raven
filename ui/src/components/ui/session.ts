const COLOR_SCHEME_KEY = 'raven-color-scheme';

/**
 * Clears the stored session on logout (same as the previous
 * `localStorage.clear()`), but keeps the user's light/dark choice, which is a
 * per-browser preference rather than session state.
 */
export function clearSession() {
    const scheme = localStorage.getItem(COLOR_SCHEME_KEY);
    localStorage.clear();
    if (scheme !== null) {
        localStorage.setItem(COLOR_SCHEME_KEY, scheme);
    }
}
