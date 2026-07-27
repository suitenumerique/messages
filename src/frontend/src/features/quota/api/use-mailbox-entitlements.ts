import { useMailboxesEntitlementsRetrieve } from "@/features/api/gen/mailboxes/mailboxes";

/**
 * Fetch storage entitlements (usage + limits) for a mailbox.
 *
 * Quotas live on the mailbox, not the user, so the widget always resolves
 * entitlements through the mailbox currently being viewed. The query is
 * disabled until a mailbox id is known.
 */
export const useMailboxEntitlements = (mailboxId: string | undefined) => {
  return useMailboxesEntitlementsRetrieve(mailboxId as string, {
    query: {
      enabled: !!mailboxId,
      // Usage changes slowly relative to a browsing session; avoid refetching
      // the (potentially DeployCenter-backed) endpoint on every focus.
      staleTime: 5 * 60 * 1000,
    },
  });
};
