import { notifications } from '@mantine/notifications';
import { Checkmark20Regular, Dismiss20Regular } from '@fluentui/react-icons';

/** Rows-per-page choices offered by every paginated table, and the size a table opens with. */
export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];
export const DEFAULT_PAGE_SIZE = PAGE_SIZE_OPTIONS[0];

/** Interpolated strings are rendered by React, which escapes them already. */
export const NO_ESCAPE = { interpolation: { escapeValue: false } } as const;

/** Table colours for a flat table sitting flush inside a Panel. */
export const PANEL_TABLE = {
    backgroundColor: 'var(--raven-surface)',
    borderColor: 'var(--raven-border)',
    rowBorderColor: 'var(--raven-border)',
} as const;

/** `.error` from an axios error response, if there is one. */
export function errorText(err: any): string | undefined {
    return err?.response?.data?.error;
}

/**
 * WTForms-style `{field: [messages]}` errors joined with ", ", or the plain `.error`
 * when the response has no such object.
 */
export function flattenedErrors(err: any): string | undefined {
    const data = err?.response?.data;
    const errors = data?.errors;
    if (errors && typeof errors === 'object' && !Array.isArray(errors)) {
        const messages: string[] = [];
        Object.values(errors).forEach((value) => {
            if (Array.isArray(value)) {
                value.forEach((m) => messages.push(String(m)));
            } else if (value !== null && value !== undefined) {
                messages.push(String(value));
            }
        });
        return messages.join(', ');
    }
    return data?.error;
}

export function notifySuccess(message: string, title?: string) {
    notifications.show({ title, message, color: 'green', icon: <Checkmark20Regular /> });
}

export function notifyFailure(title: string, message?: string, autoClose: boolean | number = true) {
    notifications.show({ title, message, color: 'red', icon: <Dismiss20Regular />, autoClose });
}
