import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { useComposeWindows } from "@/features/providers/compose-windows";
import { MAILBOX_FOLDERS } from "@/features/layouts/components/mailbox-panel/components/mailbox-list";

/**
 * Backward-compatibility redirect: composing now happens in a floating
 * window, so deep links to the old full-page form open a window over the
 * mailbox default folder instead.
 */
const NewMessageRedirect = () => {
  const navigate = useNavigate();
  const { mailboxId } = Route.useParams();
  const { openComposeWindow } = useComposeWindows();
  const hasRedirected = useRef(false);

  useEffect(() => {
    if (hasRedirected.current) return;
    hasRedirected.current = true;
    openComposeWindow({ mode: "new", mailboxId });
    const defaultFolder = MAILBOX_FOLDERS()[0];
    navigate({ to: "/mailbox/$mailboxId", params: { mailboxId }, search: defaultFolder.filter, replace: true });
  }, [mailboxId, navigate, openComposeWindow]);

  return null;
};

export const Route = createFileRoute("/mailbox/$mailboxId/new/")({
  component: NewMessageRedirect,
});
