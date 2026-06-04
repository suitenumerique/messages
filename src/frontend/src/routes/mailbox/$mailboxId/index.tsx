import { createFileRoute } from "@tanstack/react-router";
import { useResponsive } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Panel, Group, Separator, useDefaultLayout } from "react-resizable-panels";

import { ThreadPanel } from "@/features/layouts/components/thread-panel";
import { ThreadSelectionPlaceholder } from "@/features/layouts/components/thread-selection-placeholder";
import { useThreadSelection } from "@/features/providers/thread-selection";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import useAbility, { Abilities } from "@/hooks/use-ability";
import ViewHelper from "@/features/utils/view-helper";

const Mailbox = () => {
  const { t } = useTranslation();
  const { selectedMailbox, threads } = useMailboxContext();
  const canImportMessages = useAbility(Abilities.CAN_IMPORT_MESSAGES, selectedMailbox);
  const { selectedThreadIds } = useThreadSelection();
  const searchParams = useUrlSearchParams();
  const { isMobile } = useResponsive();
  const showThreadView = !isMobile;
  const emptyMailbox = (selectedMailbox?.count_threads || 0) === 0
    && (threads?.results.length ?? 0) === 0;
  const { defaultLayout, onLayoutChange } = useDefaultLayout({
    groupId: showThreadView ? "threads" : "threads-single",
    storage: localStorage,
  });

  const showImportButton = useMemo(() => {
    if (!canImportMessages || !emptyMailbox) return false;
    if (ViewHelper.isInboxView() || ViewHelper.isAllMessagesView()) return true;
    return false;
  }, [canImportMessages, emptyMailbox, searchParams]);

  if (emptyMailbox) {
    return (
      <div className="thread-view thread-view--empty" style={{ top: 0 }}>
        <div>
          <img src="/images/svg/read-mail.svg" alt="" width={60} height={60} />
          <p>{t('No threads')}</p>
          {showImportButton && (
            <Button href="#modal-message-importer">{t('Import messages')}</Button>
          )}
        </div>
      </div>
    );
  }

  return (
    <Group defaultLayout={defaultLayout} onLayoutChange={onLayoutChange} orientation="horizontal" className="threads__container">
      <Panel id={showThreadView ? "panel-thread-list" : "panel-thread-list-single"} className="thread-list-panel" defaultSize="35%" minSize="20%" maxSize="50%">
        <ThreadPanel />
      </Panel>
      {showThreadView && (
        <>
          <Separator className="panel__resize-handle" />
          <Panel id="panel-thread-view" className="thread-view-panel">
            {selectedThreadIds.size > 0 ? (
              <ThreadSelectionPlaceholder />
            ) : (
              <div className="thread-view thread-view--empty">
                <div>
                  <img src="/images/svg/read-mail.svg" alt="" width={60} height={60} />
                  <p>{t('Select a thread')}</p>
                </div>
              </div>
            )}
          </Panel>
        </>
      )}
    </Group>
  );
};

export const Route = createFileRoute("/mailbox/$mailboxId/")({
  component: Mailbox,
});
