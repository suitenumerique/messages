import { createContext, PropsWithChildren, useContext, useEffect, useMemo } from "react"
import { MailDomainAdmin } from "../api/gen/models/mail_domain_admin";
import { useMaildomainsList, useMaildomainsRetrieve } from "../api/gen";
import { useRouter } from "next/router";
import { usePagination } from "@gouvfr-lasuite/cunningham-react";
import { DEFAULT_PAGE_SIZE } from "../config/constants";

type AdminMailDomainContextType = {
    selectedMailDomain: MailDomainAdmin | null;
    mailDomains: MailDomainAdmin[];
    isLoading: boolean;
    error: unknown | null;
    pagination: ReturnType<typeof usePagination>;
    refetchMailDomains: () => void;
}

const AdminMailDomainContext = createContext<AdminMailDomainContextType | undefined>(undefined)

/**
 * Context provider for the admin mail domain views.
 * It centralizes mail domain data fetching and selection.
 */
export const AdminMailDomainProvider = ({ children }: PropsWithChildren) => {
    const router = useRouter();
    const pagination = usePagination({ pageSize: DEFAULT_PAGE_SIZE });
    const maildomainsQuery = useMaildomainsList({ page: pagination.page });
    const { data: selectedMaildomainData, isLoading: isLoadingItem, error: itemError } = useMaildomainsRetrieve(
        router.query.maildomainId as string, { query: { enabled: !!router.query.maildomainId } });
    const context = useMemo(() => ({
        selectedMailDomain: selectedMaildomainData?.data || null,
        mailDomains: maildomainsQuery.data?.data.results || [],
        isLoading: maildomainsQuery.isLoading || isLoadingItem,
        error: maildomainsQuery.error || itemError,
        pagination,
        refetchMailDomains: maildomainsQuery.refetch,
    }), [selectedMaildomainData, maildomainsQuery, isLoadingItem, itemError, pagination]);

    useEffect(() => {
        if (maildomainsQuery.data?.data.count) {
            pagination.setPagesCount(Math.ceil(maildomainsQuery.data.data.count / pagination.pageSize));
        }
    }, [maildomainsQuery.data?.data.count, pagination.pageSize, pagination.setPagesCount]);

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
