import { useTranslation } from "react-i18next";
import { Badge } from "@gouvfr-lasuite/ui-components";
import { useMaildomainsImportsStatsRetrieve } from "@/features/api/gen";
import { Icon } from "@/features/ui/components/icon";
import { IMPORTS_POLL_INTERVAL } from "@/hooks/import-status";

type ImportsTabLabelProps = {
    maildomainId: string;
};

/** The Imports tab name, with how many imports are in progress and, as an
 * alert, how many failed. */
export const ImportsTabLabel = ({ maildomainId }: ImportsTabLabelProps) => {
    const { t } = useTranslation();
    const { data } = useMaildomainsImportsStatsRetrieve(maildomainId, {
        query: {
            // Imports in progress end or fail without the admin doing anything.
            refetchInterval: (query) =>
                (query.state.data?.data.in_progress ?? 0) > 0 ? IMPORTS_POLL_INTERVAL : false,
        },
        // Background status poll: let foreground requests win the wire.
        request: { priority: "low" },
    });
    const inProgress = data?.data.in_progress ?? 0;
    const failed = data?.data.failed ?? 0;
    const inProgressLabel = t("{{count}} imports in progress", { count: inProgress });
    const failedLabel = t("{{count}} failed imports", { count: failed });

    return (
        <span className="admin-imports__tab-label">
            {t("Imports")}
            {inProgress > 0 && (
                <Badge type="neutral" title={inProgressLabel}>
                    <span aria-hidden="true">{inProgress}</span>
                    <span className="c__offscreen">{inProgressLabel}</span>
                </Badge>
            )}
            {failed > 0 && (
                <Badge type="danger" className="admin-imports__failed-badge" title={failedLabel}>
                    <Icon name="error" size={12} />
                    <span aria-hidden="true">{failed}</span>
                    <span className="c__offscreen">{failedLabel}</span>
                </Badge>
            )}
        </span>
    );
};
