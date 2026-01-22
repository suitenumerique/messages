import { useMailboxContext } from "@/features/providers/mailbox";
import { IntegrationsDataGrid } from "./integrations-data-grid";

export const IntegrationsPageContent = () => {
  const { selectedMailbox } = useMailboxContext();

  if (!selectedMailbox) {
    return null;
  }

  return (
    <div className="admin-page__content">
      <IntegrationsDataGrid mailbox={selectedMailbox} />
    </div>
  );
};
