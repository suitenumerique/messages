import { useMailboxContext } from "@/features/providers/mailbox";
import { AutoreplyDataGrid } from "./autoreply-data-grid";

export const ManageAutorepliesViewPageContent = () => {
  const { selectedMailbox } = useMailboxContext();

  if (!selectedMailbox) {
    return null;
  }

  return <AutoreplyDataGrid mailbox={selectedMailbox} />;
};
