import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { ChevronLeft } from "@gouvfr-lasuite/ui-kit/icons";
import { Icon } from "@/features/ui/components/icon";
import { useMailboxContext } from "@/features/providers/mailbox";
import { AssigneesWidget } from "@/features/layouts/components/thread-view/components/assignees-widget";

type MobileThreadHeaderProps = {
  /** Opens the thread accesses modal (read-only assignees path). */
  onOpenAccesses: () => void;
};

/**
 * Native-only replacement for the desktop ThreadActionBar inside the thread
 * view's sticky row: a back button on the left (the conversation fills the
 * whole screen, so "back" replaces the desktop "close") and the assignment
 * widget on the right. Every other thread action lives in the bottom toolbar
 * and its more-options drawer.
 */
export const MobileThreadHeader = ({ onOpenAccesses }: MobileThreadHeaderProps) => {
  const { t } = useTranslation();
  const { unselectThread } = useMailboxContext();

  return (
    <div className="mobile-thread-header">
      <Button
        className="mobile-thread-header__back"
        color="neutral"
        variant="tertiary"
        icon={<Icon icon={ChevronLeft} />}
        onClick={unselectThread}
        aria-label={t("Back")}
      />
      <AssigneesWidget onClick={onOpenAccesses} />
    </div>
  );
};

export default MobileThreadHeader;
