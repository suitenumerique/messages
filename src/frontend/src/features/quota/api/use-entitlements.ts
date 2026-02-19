import { useQuery } from "@tanstack/react-query";
import { fetchAPI } from "@/features/api/fetch-api";

export interface EntitlementsMailbox {
  max_storage: number | null;
  storage_used: number | null;
}

export interface EntitlementsResponse {
  status: number;
  data: {
    can_access: boolean;
    can_admin_maildomains: string[];
    operator: Record<string, unknown> | null;
    mailbox: EntitlementsMailbox | null;
  };
}

export function useEntitlements(mailboxId: string | undefined) {
  return useQuery({
    queryKey: ["entitlements", mailboxId],
    queryFn: () =>
      fetchAPI<EntitlementsResponse>(
        `/api/v1.0/entitlements/`,
        {
          params: mailboxId ? { mailbox_id: mailboxId } : undefined,
        }
      ),
    enabled: !!mailboxId,
    staleTime: 5 * 60 * 1000, // 5 minutes
    meta: {
      noGlobalError: true,
    },
  });
}
