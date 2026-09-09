import { Spinner } from "@gouvfr-lasuite/ui-components";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { DateHelper } from "@/features/utils/date-helper";

/** How long the announcement stays in the live region before it is cleared. */
const ANNOUNCEMENT_MS = 3000;

type DraftSaveStatusProps = {
    /** The draft's last save time, from `draft.updated_at`. */
    savedAt: string | undefined;
    isSaving: boolean;
    /** The form's ticking clock, so the relative time keeps aging. */
    now: Date;
    className?: string;
};

/**
 * The draft's save state: a spinner while a save is in flight and the
 * "last saved" relative time once one landed.
 *
 * Screen readers get a dedicated live region instead of the visible label:
 * that label is re-rendered by the clock every few seconds ("15 seconds
 * ago", "30 seconds ago"…), and making it live would turn every tick into
 * an announcement. The region only speaks when a save lands, then empties
 * so the next one is announced too. The very first save is left to the
 * "Draft saved" toast the form raises on draft creation, which screen
 * readers already announce.
 */
export const DraftSaveStatus = ({ savedAt, isSaving, now, className }: DraftSaveStatusProps) => {
    const { t } = useTranslation();
    // State-from-previous-render rather than an effect, so a save is caught
    // in the same render it shows up in.
    const [prevSavedAt, setPrevSavedAt] = useState(savedAt);
    const [announcedSaveAt, setAnnouncedSaveAt] = useState<string | undefined>();
    if (prevSavedAt !== savedAt) {
        setPrevSavedAt(savedAt);
        if (prevSavedAt !== undefined && savedAt !== undefined) setAnnouncedSaveAt(savedAt);
    }

    useEffect(() => {
        if (!announcedSaveAt) return;
        const timeoutId = window.setTimeout(() => setAnnouncedSaveAt(undefined), ANNOUNCEMENT_MS);
        return () => window.clearTimeout(timeoutId);
    }, [announcedSaveAt]);

    return (
        <div className={className}>
            {isSaving && (
                <span role="img" aria-label={t("Saving...")}>
                    <Spinner size="sm" />
                </span>
            )}
            {savedAt && t("Last saved {{relativeTime}}", { relativeTime: DateHelper.formatRelativeTime(savedAt, now) })}
            <span role="status" aria-live="polite" className="c__offscreen">
                {announcedSaveAt ? t("Draft saved") : ""}
            </span>
        </div>
    );
};
