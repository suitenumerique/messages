import { createFileRoute, Outlet } from "@tanstack/react-router";

import { MainLayout } from "@/features/layouts/components/main";
import { ThreadListboxFocusProvider } from "@/features/providers/thread-listbox-focus";
import { ThreadSelectionProvider } from "@/features/providers/thread-selection";

const MailboxLayoutRoute = () => (
  <MainLayout>
    <ThreadSelectionProvider>
      <ThreadListboxFocusProvider>
        <Outlet />
      </ThreadListboxFocusProvider>
    </ThreadSelectionProvider>
  </MainLayout>
);

export const Route = createFileRoute("/mailbox/$mailboxId")({
  component: MailboxLayoutRoute,
});
