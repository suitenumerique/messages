import { createFileRoute, Outlet } from "@tanstack/react-router";

import { MainLayout } from "@/features/layouts/components/main";
import { ThreadSelectionProvider } from "@/features/providers/thread-selection";

const MailboxLayoutRoute = () => (
  <MainLayout>
    <ThreadSelectionProvider>
      <Outlet />
    </ThreadSelectionProvider>
  </MainLayout>
);

export const Route = createFileRoute("/mailbox/$mailboxId")({
  component: MailboxLayoutRoute,
});
