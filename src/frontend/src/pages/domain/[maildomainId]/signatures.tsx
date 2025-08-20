import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { ComposeSignatureAction } from "@/features/layouts/components/admin/signatures-view/compose-signature-action";
import { AdminSignaturesViewPageContent } from "@/features/layouts/components/admin/signatures-view/page-content";

/**
 * Admin page which list all mailboxes for a given domain and allow to manage them.
 */
export default function AdminDomainPage() {
  return (
    <AdminLayout
      currentTab="signatures"
      actions={<ComposeSignatureAction />}
    >
      <AdminSignaturesViewPageContent />
    </AdminLayout>
  );
}
