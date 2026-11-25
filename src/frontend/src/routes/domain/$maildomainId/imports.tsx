import { useMemo } from "react";
import { createFileRoute, useNavigate, useParams, useSearch } from "@tanstack/react-router";

import { MaildomainsImportsListStatusItem as ListStatus } from "@/features/api/gen";
import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { AdminImportsViewPageContent } from "@/features/layouts/components/admin/imports-view/page-content";
import { NewImportAction } from "@/features/layouts/components/admin/imports-view/new-import-action";
import { DEFAULT_IMPORT_LIST_STATUSES, parseImportListStatuses } from "@/hooks/import-status";
import { useSearchablePagination } from "@/hooks/use-searchable-pagination";

const AdminDomainImportsPage = () => {
  const { maildomainId } = useParams({ strict: false }) as { maildomainId?: string };
  // `status` holds the listed statuses, comma-separated; absent, the ones
  // still to watch.
  const { status } = useSearch({ strict: false }) as { status?: string };
  const statuses = useMemo(() => parseImportListStatuses(status), [status]);
  const navigate = useNavigate();
  const { pagination, searchQuery, setSearchQuery } = useSearchablePagination({
    resetKey: maildomainId,
  });

  const handleStatusesChange = (next: ListStatus[]) => {
    pagination.setPage(1);
    pagination.setPagesCount(undefined);
    const isDefault =
      next.length === DEFAULT_IMPORT_LIST_STATUSES.length &&
      DEFAULT_IMPORT_LIST_STATUSES.every((listed) => next.includes(listed));
    navigate({ to: ".", search: isDefault ? {} : { status: next.join(",") }, replace: true });
  };

  return (
    <AdminLayout currentTab="imports" actions={<NewImportAction />}>
      <AdminImportsViewPageContent
        pagination={pagination}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        statuses={statuses}
        onStatusesChange={handleStatusesChange}
      />
    </AdminLayout>
  );
};

export const Route = createFileRoute("/domain/$maildomainId/imports")({
  component: AdminDomainImportsPage,
});
