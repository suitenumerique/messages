import { DEFAULT_PAGE_SIZE } from "@/features/config/constants";
import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { CreateMailboxAction } from "@/features/layouts/components/admin/mailboxes-view/create-mailbox-action";
import { AdminDomainPageContent } from "@/features/layouts/components/admin/mailboxes-view/page-content";
import { usePagination } from "@openfun/cunningham-react";
import { useQueryClient } from "@tanstack/react-query";

/**
 * Admin page which list all mailboxes for a given domain and allow to manage them.
 */
export default function AdminDomainMailboxesPage() {
  const pagination = usePagination({ pageSize: DEFAULT_PAGE_SIZE });
  const queryClient = useQueryClient();

  const handleCreateMailbox = async () => {
    if (pagination.page === 1) {
      // Invalidate the mailboxes list query for the current domain of page 1
      await queryClient.invalidateQueries({
        predicate: (query) => {
          const isMailboxesMailDomainQuery = typeof query.queryKey[0] === 'string' && /maildomains\/[a-f0-9-]*\/mailboxes\/?/.test(query.queryKey[0]);
          const isFirstPageQuery = !!query.queryKey[1] && typeof query.queryKey[1] === 'object' && 'page' in query.queryKey[1] && query.queryKey[1].page === 1;
          return isMailboxesMailDomainQuery && isFirstPageQuery;
        }
      });
    } else {
      pagination.setPage(1);
    }
    pagination.setPagesCount(undefined);
  }

  return (
    <AdminLayout
      currentTab="addresses"
      actions={<CreateMailboxAction onCreate={handleCreateMailbox} />}
    >
      <AdminDomainPageContent pagination={pagination} />
    </AdminLayout>
  );
}
