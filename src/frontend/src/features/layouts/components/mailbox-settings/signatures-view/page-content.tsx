import { useMailboxContext } from "@/features/providers/mailbox";
import { SignatureDataGrid } from "./signature-data-grid";

export const ManageSignaturesViewPageContent = () => {
  const { selectedMailbox } = useMailboxContext();

  if (!selectedMailbox) {
    return null;
  }

  return (
    <div className="admin-page__content">
      <SignatureDataGrid mailbox={selectedMailbox} />
    </div>
  );
};
