import { QueryClient, QueryKey } from "@tanstack/react-query";

import {
    getMaildomainsMessageTemplatesListUrl,
    getMailboxesMessageTemplatesListUrl,
} from "@/features/api/gen";

// Generated query keys start with the request URL, so a mailbox's template
// queries all share the prefix of its templates list: the settings lists,
// a template details, but also what the composers consume — the available
// templates (toolbar selects, `available/`) and the rendered signatures
// (`{id}/render/`). A partial key match on the list key would miss those
// nested routes, a URL prefix match covers them all.
const queryUrl = ({ queryKey }: { queryKey: QueryKey }) =>
    typeof queryKey[0] === "string" ? queryKey[0] : "";

const ANY_MAILBOX_MESSAGE_TEMPLATES = /^\/api\/v1\.0\/mailboxes\/[^/]+\/message-templates\//;

/**
 * Invalidates every message template query of a mailbox, including the ones
 * feeding the composers open in the session.
 */
export const invalidateMailboxMessageTemplates = (queryClient: QueryClient, mailboxId: string) => {
    const prefix = getMailboxesMessageTemplatesListUrl(mailboxId);
    return queryClient.invalidateQueries({
        predicate: (query) => queryUrl(query).startsWith(prefix),
    });
};

/**
 * Invalidates every message template query of a mail domain. Domain templates
 * are listed among the available templates of the domain's mailboxes, so the
 * template queries of every mailbox in the session are refreshed too.
 */
export const invalidateMaildomainMessageTemplates = (queryClient: QueryClient, maildomainId: string | undefined) => {
    const prefix = maildomainId ? getMaildomainsMessageTemplatesListUrl(maildomainId) : undefined;
    return queryClient.invalidateQueries({
        predicate: (query) => {
            const url = queryUrl(query);
            return (!!prefix && url.startsWith(prefix)) || ANY_MAILBOX_MESSAGE_TEMPLATES.test(url);
        },
    });
};
