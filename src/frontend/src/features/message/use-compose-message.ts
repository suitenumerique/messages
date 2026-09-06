import { useMailboxContext } from "@/features/providers/mailbox";
import { useComposeWindows } from "@/features/providers/compose-windows";
import { useLayoutContext } from "@/features/layouts/components/layout-context";
import useAbility, { Abilities } from "@/hooks/use-ability";

/**
 * Shared entry point to start composing a new message. Centralises the
 * write-permission check and the compose window opening so the sidebar
 * action and the mobile bottom bar stay in sync.
 */
export const useComposeMessage = () => {
  const { selectedMailbox } = useMailboxContext();
  const { openComposeWindow } = useComposeWindows();
  const { closeLeftPanel } = useLayoutContext();
  const canWriteMessages = useAbility(Abilities.CAN_WRITE_MESSAGES, selectedMailbox);

  const goToNewMessage = (
    event?: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>,
  ) => {
    event?.preventDefault();
    if (!canWriteMessages || !selectedMailbox) return;
    closeLeftPanel();
    openComposeWindow({ mode: "new", mailboxId: selectedMailbox.id });
  };

  return { canWriteMessages, goToNewMessage, selectedMailbox };
};

export default useComposeMessage;
