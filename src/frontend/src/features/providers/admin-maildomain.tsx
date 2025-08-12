import { createContext, PropsWithChildren, useContext, useEffect, useMemo } from "react"
import { MailDomainAdmin } from "../api/gen/models/mail_domain_admin";
import { useMaildomainsList, useMaildomainsRetrieve } from "../api/gen";
import { useRouter } from "next/router";
import { usePagination } from "@openfun/cunningham-react";

type AdminMailDomainContextType = {
    selectedMailDomain: MailDomainAdmin | null;
    mailDomains: MailDomainAdmin[];
    isLoading: boolean;
    error: unknown | null;
    pagination: ReturnType<typeof usePagination>;
}

const AdminMailDomainContext = createContext<AdminMailDomainContextType | undefined>(undefined)

/**
 * Context provider for the admin mail domain views.
 * It centralizes mail domain data fetching and selection.
 */
export const AdminMailDomainProvider = ({ children }: PropsWithChildren) => {
    const router = useRouter();
    const pagination = usePagination({ pageSize: 20 });
    const { data: maildomainsData, isLoading: isLoadingList, error: listError } = useMaildomainsList({ page: pagination.page });
    const { data: selectedMaildomainData, isLoading: isLoadingItem, error: itemError } = useMaildomainsRetrieve(
        router.query.maildomainId as string, { query: { enabled: !!router.query.maildomainId } });
    const context = useMemo(() => ({
        selectedMailDomain: selectedMaildomainData?.data || null,
        mailDomains: maildomainsData?.data.results || [],
        isLoading: isLoadingList || isLoadingItem,
        error: listError || itemError,
        pagination
    }), [selectedMaildomainData, maildomainsData, isLoadingList, isLoadingItem, listError, itemError, pagination.page]);

    useEffect(() => {
        if (maildomainsData?.data.count) {
            pagination.setPagesCount(Math.ceil(maildomainsData.data.count / pagination.pageSize));
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
