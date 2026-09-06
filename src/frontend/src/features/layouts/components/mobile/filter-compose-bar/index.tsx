import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { ThreadPanelFilter } from "@/features/layouts/components/thread-panel/components/thread-panel-filter";
import { useComposeMessage } from "@/features/message/use-compose-message";
import { isNativePlatform } from "@/features/native/platform";
import { MobileBottomBar } from "../bottom-bar";
import { Icon } from "@/features/ui/components/icon";

/**
 * Native-only bottom bar for the thread-list view: the quick filter on the left,
 * a thumb-reachable compose button on the right. Search is not here — it opens
 * from the header trigger.
 */
export const MobileFilterComposeBar = () => {
  const { t } = useTranslation();
  const { canWriteMessages, goToNewMessage, selectedMailbox } = useComposeMessage();

  if (!isNativePlatform() || !selectedMailbox) return null;

  return (
    <MobileBottomBar className="mobile-filter-compose-bar">
      <ThreadPanelFilter />
      <Button
        className="mobile-filter-compose-bar__compose"
        onClick={goToNewMessage}
        href={`/mailbox/${selectedMailbox.id}/new`}
        icon={<Icon name="mail-plus" />}
        disabled={!canWriteMessages}
        aria-label={t("New message")}
        variant="tertiary"
      />
    </MobileBottomBar>
  );
};

export default MobileFilterComposeBar;
