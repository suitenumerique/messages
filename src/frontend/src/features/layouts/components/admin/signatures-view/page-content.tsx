import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import { SignatureDataGrid } from "./signature-data-grid";
import { AdminPageContent } from "../page-content";

export const AdminSignaturesViewPageContent = () => {
  const { selectedMailDomain } = useAdminMailDomain();

  return (
    <AdminPageContent>
      <SignatureDataGrid domain={selectedMailDomain!} />
    </AdminPageContent>
  );
};
