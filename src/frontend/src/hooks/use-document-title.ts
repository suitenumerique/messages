import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useUnreadTitlePrefix } from "@/features/providers/unread-badge";

const TITLE_SEPARATOR = " - ";

/**
 * Sets the document title to "<segments> - <app name>" for the current view.
 * Unresolved segments (a folder name still loading, no mailbox selected…) are
 * dropped so the title never shows a dangling separator.
 *
 * The title also carries the unread badge on engines whose favicon cannot (see
 * `unread-badge.ts`). It has to be applied here rather than by the badge itself:
 * this hook rewrites `document.title` on every route change, so a prefix set
 * from the outside would be wiped on the next navigation.
 *
 * @param segments - View segments, from the most generic to the most specific.
 */
export const useDocumentTitle = (
  ...segments: (string | undefined | null | false)[]
) => {
  const { t } = useTranslation();
  const prefix = useUnreadTitlePrefix();
  const title = [
    ...segments.filter((segment): segment is string => !!segment),
    t("Messaging"),
  ].join(TITLE_SEPARATOR);

  useEffect(() => {
    document.title = prefix + title;
  }, [prefix, title]);
};
