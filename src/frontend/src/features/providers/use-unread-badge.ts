import { useEffect, useRef } from "react";

import { useMailboxContext } from "@/features/providers/mailbox";
import {
  setUnreadBadge,
  trackUnreadTotal,
} from "@/features/providers/unread-badge";
import { listenForPushReceived } from "@/features/push/shared";

/**
 * Badge the tab when mail arrives while it sits in the background, and clear
 * the badge as soon as the user looks at the tab again. The badge renders as a
 * favicon dot, or as a title marker on engines whose favicon can't be updated
 * after load — see `unread-badge.ts`.
 *
 * The badge answers "something arrived while you were away", not "you have
 * unread mail": badging on `count_unread_threads > 0` leaves it lit forever for
 * anyone who keeps old unread threads around, which makes it worthless as a
 * signal. So arrivals are read as a rise of the unread total above a baseline:
 * the total the last time the tab was visible, lowered to follow the total
 * while the tab is hidden — mail read on another device must not absorb the
 * next arrival. It counts every mailbox the user can access — the badge
 * belongs to the tab, not to the selected mailbox.
 *
 * Two signals raise it. The mailbox poll works for every user but lags by up to
 * its interval; a Web Push raises it at once, and only where the user opted in
 * and a service worker runs. Neither ever lowers it: only the user coming back
 * to the tab does.
 */
export const useUnreadBadge = () => {
  const { mailboxes } = useMailboxContext();
  const unreadTotal = mailboxes?.reduce(
    (total, mailbox) => total + mailbox.count_unread_threads,
    0,
  );
  /** Unread total the last time the tab was visible. Stays `undefined` until
   * the first mailbox load, so a first fetch landing on an already-hidden tab
   * reads as a starting point rather than as an arrival. */
  const baseline = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (unreadTotal === undefined) return;
    const next = trackUnreadTotal(
      baseline.current,
      unreadTotal,
      document.hidden,
    );
    baseline.current = next.baseline;
    if (next.badge !== undefined) setUnreadBadge(next.badge);
  }, [unreadTotal]);

  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.hidden) return;
      baseline.current = unreadTotal;
      setUnreadBadge(false);
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", onVisibilityChange);
  }, [unreadTotal]);

  useEffect(
    () =>
      listenForPushReceived(() => {
        if (document.hidden) setUnreadBadge(true);
      }),
    [],
  );

  useEffect(() => () => setUnreadBadge(false), []);
};
