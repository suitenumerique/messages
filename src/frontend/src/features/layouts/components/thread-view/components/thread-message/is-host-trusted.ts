/**
 * Match a URL host against the backend-configured trusted-domain allowlist
 * (the `MESSAGE_TRUSTED_LINK_DOMAINS` config entry). Hosts that match skip the
 * external-link confirmation modal.
 *
 * A lone `*` pattern trusts every host. A pattern may otherwise use a single
 * leading `*.` wildcard to also cover subdomains (`*.gouv.fr` matches
 * `gouv.fr` and `impots.gouv.fr`); any other pattern matches the host exactly.
 * Matching is case-insensitive.
 */
export function isHostTrusted(host: string, patterns: readonly string[]): boolean {
    if (!host || patterns.length === 0) {
        return false;
    }
    const normalizedHost = host.toLowerCase();
    return patterns.some((pattern) => {
        const normalized = pattern.trim().toLowerCase();
        if (!normalized) {
            return false;
        }
        if (normalized === "*") {
            return true;
        }
        if (normalized.startsWith("*.")) {
            const base = normalized.slice(2);
            return normalizedHost === base || normalizedHost.endsWith(`.${base}`);
        }
        return normalizedHost === normalized;
    });
}
