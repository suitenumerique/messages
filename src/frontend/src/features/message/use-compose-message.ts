import { useNavigate } from "@tanstack/react-router";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useLayoutContext } from "@/features/layouts/components/layout-context";
import useAbility, { Abilities } from "@/hooks/use-ability";

/**
 * Shared entry point to start composing a new message. Centralises the
 * write-permission check and the navigation to the compose route so the
 * sidebar action and the mobile bottom bar stay in sync.
 */
export const useComposeMessage = () => {
  const navigate = useNavigate();
  const { selectedMailbox } = useMailboxContext();
  const { closeLeftPanel } = useLayoutContext();
  const canWriteMessages = useAbility(Abilities.CAN_WRITE_MESSAGES, selectedMailbox);

  const goToNewMessage = (
    event?: React.MouseEvent<HTMLButtonElement | HTMLAnchorElement>,
  ) => {
    event?.preventDefault();
    if (!canWriteMessages || !selectedMailbox) return;
    closeLeftPanel();
    navigate({ to: "/mailbox/$mailboxId/new", params: { mailboxId: selectedMailbox.id } });
  };

  return { canWriteMessages, goToNewMessage, selectedMailbox };
};

export default useComposeMessage;
