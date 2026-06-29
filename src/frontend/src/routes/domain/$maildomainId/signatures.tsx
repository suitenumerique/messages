import { createFileRoute } from "@tanstack/react-router";

import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { ComposeSignatureAction } from "@/features/layouts/components/admin/signatures-view/compose-signature-action";
import { AdminSignaturesViewPageContent } from "@/features/layouts/components/admin/signatures-view/page-content";

const AdminDomainSignaturesPage = () => (
  <AdminLayout
    currentTab="signatures"
    actions={<ComposeSignatureAction />}
  >
    <AdminSignaturesViewPageContent />
  </AdminLayout>
);

export const Route = createFileRoute("/domain/$maildomainId/signatures")({
  component: AdminDomainSignaturesPage,
});
