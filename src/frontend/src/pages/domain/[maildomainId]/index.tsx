import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { CreateMailboxAction } from "@/features/layouts/components/admin/mailboxes-view/create-mailbox-action";
import { AdminDomainPageContent } from "@/features/layouts/components/admin/mailboxes-view/page-content";

/**
 * Admin page which list all mailboxes for a given domain and allow to manage them.
 */
export default function AdminDomainPage() {
  return (
    <AdminLayout
      currentTab="addresses"
      actions={<CreateMailboxAction />}
    >
      <AdminDomainPageContent />
    </AdminLayout>
  );
}
