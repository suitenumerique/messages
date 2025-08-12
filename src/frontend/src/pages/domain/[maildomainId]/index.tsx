import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { CreateMailboxAction } from "@/features/layouts/components/admin/mailboxes-view/create-mailbox-action";
import { AdminDomainPageContent } from "@/features/layouts/components/admin/mailboxes-view/page-content";
import { usePagination } from "@openfun/cunningham-react";

/**
 * Admin page which list all mailboxes for a given domain and allow to manage them.
 */
export default function AdminDomainMailboxesPage() {
  const pagination = usePagination({ pageSize: 20 });

  const handleCreateMailbox = () => {
    pagination.setPage(1);
    pagination.setPagesCount(undefined)
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
