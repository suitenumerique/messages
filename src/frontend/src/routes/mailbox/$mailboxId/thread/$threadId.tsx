import { createFileRoute } from "@tanstack/react-router";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { Panel, Group, Separator, useDefaultLayout } from "react-resizable-panels";

import { ThreadPanel } from "@/features/layouts/components/thread-panel";
import { ThreadSelectionPlaceholder } from "@/features/layouts/components/thread-selection-placeholder";
import { ThreadView } from "@/features/layouts/components/thread-view";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useThreadSelection } from "@/features/providers/thread-selection";
import { useCurrentFolderName } from "@/hooks/use-current-folder-name";
import { useDocumentTitle } from "@/hooks/use-document-title";

const Mailbox = () => {
  const { selectedThreadIds } = useThreadSelection();
  const { selectedMailbox } = useMailboxContext();
  const folderName = useCurrentFolderName();
  const { isMobile } = useResponsive();
  const { defaultLayout, onLayoutChange } = useDefaultLayout({
    groupId: "threads",
    storage: localStorage,
  });

  useDocumentTitle(selectedMailbox?.name ?? selectedMailbox?.email, folderName);
  // The placeholder only stands in for the thread view in the desktop split
  // layout, where the list stays visible next to it. On mobile it would take
  // over the whole screen and hide the thread being read as soon as a
  // selection exists, so the thread view stays put there.
  const content = !isMobile && selectedThreadIds.size > 0 ? (
    <ThreadSelectionPlaceholder />
  ) : (
    <ThreadView />
  );

  // On mobile the thread view takes over the whole content area in normal
  // flow (no side-by-side list, no fixed overlay).
  if (isMobile) {
    return content;
  }

  return (
    <Group defaultLayout={defaultLayout} onLayoutChange={onLayoutChange} orientation="horizontal" className="threads__container">
      <Panel id="panel-thread-list" className="thread-list-panel" defaultSize="30%" minSize="250px" maxSize="50%">
        <ThreadPanel />
      </Panel>
      <Separator className="panel__resize-handle" />
      <Panel id="panel-thread-view" className="thread-view-panel">
        {content}
      </Panel>
    </Group>
  );
};

export const Route = createFileRoute("/mailbox/$mailboxId/thread/$threadId")({
  component: Mailbox,
});
