import { createFileRoute, useParams } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";

import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { CreateMailboxAction } from "@/features/layouts/components/admin/mailboxes-view/create-mailbox-action";
import { AdminDomainPageContent } from "@/features/layouts/components/admin/mailboxes-view/page-content";
import { useSearchablePagination } from "@/hooks/use-searchable-pagination";

const AdminDomainMailboxesPage = () => {
  const { maildomainId } = useParams({ strict: false }) as { maildomainId?: string };
  const { pagination, searchQuery, setSearchQuery } = useSearchablePagination({
    resetKey: maildomainId,
  });
  const queryClient = useQueryClient();

  const handleCreateMailbox = async () => {
    if (pagination.page === 1) {
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
  };

  return (
    <AdminLayout
      currentTab="addresses"
      actions={<CreateMailboxAction onCreate={handleCreateMailbox} />}
    >
      <AdminDomainPageContent
        pagination={pagination}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
      />
    </AdminLayout>
  );
};

export const Route = createFileRoute("/domain/$maildomainId/")({
  component: AdminDomainMailboxesPage,
});
