import { createContext, PropsWithChildren, useContext, useEffect, useMemo, useState } from "react"
import { MailDomainAdmin } from "../api/gen/models/mail_domain_admin";
import { useMaildomainsList } from "../api/gen";
import { useRouter } from "next/router";

type AdminMailDomainContextType = {
    selectedMailDomain: MailDomainAdmin | null;
    mailDomains: MailDomainAdmin[];
    isLoading: boolean;
}

const AdminMailDomainContext = createContext<AdminMailDomainContextType>({
    selectedMailDomain: null,
    mailDomains: [],
    isLoading: false,
})

/**
 * Context provider for the admin mail domain views.
 * It centralizes mail domain data fetching and selection.
 */
export const AdminMailDomainProvider = ({ children }: PropsWithChildren) => {
    const { data: maildomainsData, isLoading } = useMaildomainsList();
    const router = useRouter();
    const [selectedMailDomain, setSelectedMailDomain] = useState<MailDomainAdmin | null>(null);
    const context = useMemo(() => ({
        selectedMailDomain,
        mailDomains: maildomainsData?.data.results || [],
        isLoading,
    }), [selectedMailDomain, maildomainsData, isLoading]);

    useEffect(() => {
        if (router.query.maildomainId) {
            const maildomain = maildomainsData?.data.results?.find((maildomain) => maildomain.id === router.query.maildomainId);
            if (maildomain) {
                setSelectedMailDomain(maildomain);
            }
        }
    }, [router.query.maildomainId, maildomainsData]);

    return (
        <AdminMailDomainContext.Provider value={context}>{children}</AdminMailDomainContext.Provider>
    )
}

export const useAdminMailDomain = () => {
    const context = useContext(AdminMailDomainContext);
    if (!context) {
        throw new Error("useAdminMailDomain must be used within an AdminMailDomainProvider");
    }
    return context;
}
