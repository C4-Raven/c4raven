/** Fields of the Data Sync create/edit form. */
export interface MissionForm {
    name: string;
    description: string;
    tool: string;
    default_role: string;
    password: string;
    creator_uid: string | null;
    groups: string[];
    hashtags: string[] | string | null;
}

/**
 * Body for POST /api/missions. The server never returns a feed's password, so an edit
 * starts with a blank password field; sending that blank would clear the feed's password
 * protection. When editing, the password is only sent if a new one was typed.
 */
export function missionRequestBody(form: MissionForm, editing: boolean): Record<string, unknown> {
    const body: Record<string, unknown> = {
        name: form.name,
        description: form.description,
        tool: form.tool,
        default_role: form.default_role,
        creator_uid: form.creator_uid,
        groups: form.groups,
    };
    if (!editing || form.password !== '') {
        body.password = form.password;
    }
    return body;
}
