import { useLocation, useNavigate } from "@tanstack/react-router";
import { useCallback } from "react";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import { useLayoutContext } from "@/features/layouts/components/layout-context";
import { MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";

/**
 * The search state lives in the `search` URL param. Every surface that reads
 * or writes it (header field, native bottom bar) goes through this hook so
 * they cannot drift apart.
 */
export const useSearchQuery = () => {
    const navigate = useNavigate();
    const pathname = useLocation({ select: (l) => l.pathname });
    const { closeLeftPanel } = useLayoutContext();
    const searchParams = useUrlSearchParams();
    const query = searchParams.get("search") ?? "";
    const isSearching = searchParams.has("search");

    const submit = useCallback((nextQuery: string) => {
        // The folder the user was browsing is dropped when a search replaces
        // the URL, so leaving search mode lands on the default folder.
        const params = nextQuery
            ? new URLSearchParams({ search: nextQuery })
            : new URLSearchParams(MAILBOX_FOLDERS()[0].filter);
        closeLeftPanel();
        navigate({ to: pathname, search: Object.fromEntries(params), replace: true });
    }, [closeLeftPanel, navigate, pathname]);

    const reset = useCallback(() => submit(""), [submit]);

    return { query, isSearching, submit, reset };
};
