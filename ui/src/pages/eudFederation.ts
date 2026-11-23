/**
 * What the EUDs page calls the server a federated device came from.
 *
 * `federated_from` is a Federation Hub provenance id ("<server id>-<suffix>", e.g.
 * "622713fa68fa49d8a240dc3fe9d65545-ODmwo4kG"); the server resolves it to the partner's name
 * on the Federation Hub page as `federated_from_name` when it can.
 */
const SHORT_ID_LENGTH = 8;

export function shortFederationId(id: string): string {
    const trimmed = id.trim();
    return trimmed.length > SHORT_ID_LENGTH + 2 ? `${trimmed.slice(0, SHORT_ID_LENGTH)}…` : trimmed;
}

export function federatedFromLabel(id: string, name?: string | null): string {
    const friendly = name?.trim();
    return friendly ? friendly : shortFederationId(id);
}
