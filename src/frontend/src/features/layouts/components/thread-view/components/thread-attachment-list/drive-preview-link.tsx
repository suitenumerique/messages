import { useConfig } from "@/features/providers/config";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";

type DrivePreviewLinkProps = {
    fileId: string;
}

/**
 * DrivePreviewLink
 * A component which renders a link to open the Drive preview of a file.
 * https://drive.instance/explorer/items/files/:itemId
 */
export const DrivePreviewLink = ({ fileId }: DrivePreviewLinkProps) => {
    const { DRIVE } = useConfig();
    const isDriveDisabled = !useFeatureFlag(FEATURE_KEYS.DRIVE);
    const { t } = useTranslation();

    if (isDriveDisabled) return null;

    return (
        <Button
            aria-label={t("Open {{driveAppName}} preview", { driveAppName: DRIVE.app_name })}
            title={t("Open {{driveAppName}} preview", { driveAppName: DRIVE.app_name })}
            href={`${DRIVE.file_url}/${fileId}`}
            target="_blank"
            size="medium"
            variant="tertiary"
            icon={<Icon name="remove_red_eye" />}
        />
    )
}
