import { createFileRoute, useNavigate, useParams, useSearch } from "@tanstack/react-router";

import { AdminLayout } from "@/features/layouts/components/admin/admin-layout";
import { AdminImportsViewPageContent } from "@/features/layouts/components/admin/imports-view/page-content";
import { NewImportAction } from "@/features/layouts/components/admin/imports-view/new-import-action";
import { useSearchablePagination } from "@/hooks/use-searchable-pagination";

const AdminDomainImportsPage = () => {
  const { maildomainId } = useParams({ strict: false }) as { maildomainId?: string };
  // `all=true` lists the completed and paused imports too, not only those to
  // watch. The router decodes it to a boolean.
  const { all } = useSearch({ strict: false }) as { all?: boolean };
  const navigate = useNavigate();
  const { pagination, searchQuery, setSearchQuery } = useSearchablePagination({
    resetKey: maildomainId,
  });

  const handleShowAllChange = (showAll: boolean) => {
    pagination.setPage(1);
    pagination.setPagesCount(undefined);
    navigate({ to: ".", search: showAll ? { all: true } : {}, replace: true });
  };

  return (
    <AdminLayout currentTab="imports" actions={<NewImportAction />}>
      <AdminImportsViewPageContent
        pagination={pagination}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        showAll={all === true}
        onShowAllChange={handleShowAllChange}
      />
    </AdminLayout>
  );
};

export const Route = createFileRoute("/domain/$maildomainId/imports")({
  component: AdminDomainImportsPage,
});
