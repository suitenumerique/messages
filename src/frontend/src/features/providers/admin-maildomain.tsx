import { createContext, PropsWithChildren, useContext, useEffect, useMemo } from "react"
import { MailDomainAdmin } from "../api/gen/models/mail_domain_admin";
import { useMaildomainsList, useMaildomainsRetrieve } from "../api/gen";
import { useParams } from "@tanstack/react-router";
import { usePagination } from "@gouvfr-lasuite/cunningham-react";
import { keepPreviousData } from "@tanstack/react-query";
import { useSearchablePagination } from "@/hooks/use-searchable-pagination";

type AdminMailDomainContextType = {
    selectedMailDomain: MailDomainAdmin | null;
    mailDomains: MailDomainAdmin[];
    isLoading: boolean;
    error: unknown | null;
    pagination: ReturnType<typeof usePagination>;
    searchQuery: string;
    setSearchQuery: (query: string) => void;
}

const AdminMailDomainContext = createContext<AdminMailDomainContextType | undefined>(undefined)

/**
 * Context provider for the admin mail domain views.
 * It centralizes mail domain data fetching and selection.
 */
export const AdminMailDomainProvider = ({ children }: PropsWithChildren) => {
    const routeParams = useParams({ strict: false }) as { maildomainId?: string };
    const { pagination, searchQuery, setSearchQuery } = useSearchablePagination();
    const trimmedQuery = searchQuery.trim();
    const { data: maildomainsData, isLoading: isLoadingList, error: listError } = useMaildomainsList({
        page: pagination.page,
        ...(trimmedQuery ? { q: trimmedQuery } : {}),
    }, {
        query: { placeholderData: keepPreviousData },
    });
    const { data: selectedMaildomainData, isLoading: isLoadingItem, error: itemError } = useMaildomainsRetrieve(
        routeParams.maildomainId ?? '', { query: { enabled: !!routeParams.maildomainId } });

    const context = useMemo(() => ({
        selectedMailDomain: selectedMaildomainData?.data || null,
        mailDomains: maildomainsData?.data.results || [],
        isLoading: isLoadingList || isLoadingItem,
        error: listError || itemError,
        pagination,
        searchQuery,
        setSearchQuery,
    }), [selectedMaildomainData, maildomainsData, isLoadingList, isLoadingItem, listError, itemError, pagination, searchQuery, setSearchQuery]);

    useEffect(() => {
        if (maildomainsData?.data.count !== undefined) {
            pagination.setPagesCount(
                Math.max(1, Math.ceil(maildomainsData.data.count / pagination.pageSize))
            );
        }
    }, [maildomainsData?.data.count, pagination.pageSize, pagination.setPagesCount]);

    return (
        <AdminMailDomainContext.Provider value={context}>{children}</AdminMailDomainContext.Provider>
    )
}

export const useAdminMailDomain = () => {
    const context = useContext(AdminMailDomainContext);
    if (context === undefined) {
        throw new Error("useAdminMailDomain must be used within an AdminMailDomainProvider");
    }
    return context;
}
