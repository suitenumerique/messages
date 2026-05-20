import { usePagination } from "@gouvfr-lasuite/cunningham-react";
import { useEffect, useState } from "react";
import { DEFAULT_PAGE_SIZE } from "@/features/config/constants";
import usePrevious from "./use-previous";

type UseSearchablePaginationOptions = {
    pageSize?: number;
    /**
     * When this value changes, both the search query and pagination reset.
     * Pages-router pages don't unmount on dynamic-param change, so pass the
     * route param (e.g. `maildomainId`) to keep state isolated per resource.
     */
    resetKey?: unknown;
};

/**
 * Cunningham `usePagination` paired with a search-query state.
 *
 * Changing the search query resets the pagination back to page 1 and clears
 * `pagesCount` so the next list response reseeds it — without this the user
 * could land on an out-of-range page after typing a query.
 */
export const useSearchablePagination = (
    options: UseSearchablePaginationOptions = {},
) => {
    const { pageSize = DEFAULT_PAGE_SIZE, resetKey } = options;
    const pagination = usePagination({ pageSize });
    const [searchQuery, setSearchQueryState] = useState<string>("");
    const previousResetKey = usePrevious(resetKey);

    const setSearchQuery = (query: string) => {
        setSearchQueryState(query);
        pagination.setPage(1);
        pagination.setPagesCount(undefined);
    };

    useEffect(() => {
        if (previousResetKey === resetKey) return;
        if (resetKey === undefined) return;
        setSearchQueryState("");
        pagination.setPage(1);
        pagination.setPagesCount(undefined);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [resetKey]);

    return { pagination, searchQuery, setSearchQuery };
};
