import { useCallback } from "react";
import { useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { Banner } from "@/features/ui/components/banner";

/**
 * Ask the user to confirm opening an external link from a message,
 * revealing its real target URL to protect against link masking phishing
 * attempts (e.g. <a href="http://malicious.com">http://safe.fr</a>).
 *
 * Uses the same confirmation modal layout as the file preview when
 * clicking a link inside a PDF.
 *
 * @returns an async function resolving to true when the user confirms
 */
export const useLinkConfirmation = () => {
    const { t } = useTranslation();
    const modals = useModals();

    return useCallback(async (url: string, isMasked: boolean = false): Promise<boolean> => {
        const decision = await modals.confirmationModal({
            title: t("External link"),
            children: (
                <div className="link-preview__content">
                    {isMasked && (
                        <Banner type="warning">
                            {t("The text of this link does not match its real target, it may be unsafe.")}
                        </Banner>
                    )}
                    <p>{t("You are about to leave this page and be redirected to:")}</p>
                    <pre className="link-preview__url">{url}</pre>
                    <p>{t("Do you want to continue?")}</p>
                </div>
            ),
        });
        return decision === "yes";
    }, [modals, t]);
};
