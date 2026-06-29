import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, Spinner } from "@gouvfr-lasuite/ui-kit";
import type { Attachment } from "@/features/api/gen/models";
import { useConfig } from "@/features/providers/config";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { useDriveUpload } from "../thread-attachment-list/use-drive-upload";

type SidebarDriveActionProps = {
    attachment: Attachment;
};

/**
 * Unified Drive action for the preview sidebar of a blob attachment:
 * "Save in Drive" while the file isn't there yet, then "Open in Drive"
 * once the upload (or a cached previous one) yields a Drive file id.
 * Drive PJ pickers don't need this — they already live in Drive.
 */
export const SidebarDriveAction = ({ attachment }: SidebarDriveActionProps) => {
    const { t } = useTranslation();
    const { DRIVE } = useConfig();
    const isDriveDisabled = !useFeatureFlag(FEATURE_KEYS.DRIVE);
    const { state, driveFileId, upload } = useDriveUpload(attachment.blobId);

    if (isDriveDisabled) return null;

    if (driveFileId) {
        return (
            <Button
                size="small"
                variant="secondary"
                fullWidth
                href={`${DRIVE.file_url}/${driveFileId}`}
                target="_blank"
                rel="noopener noreferrer"
                icon={<Icon name="open_in_new" />}
            >
                {t("Open in {{driveAppName}}", { driveAppName: DRIVE.app_name })}
            </Button>
        );
    }

    const label =
        state === "uploading"
            ? t("Saving...")
            : state === "error"
                ? t("Save failed — retry")
                : t("Save in {{driveAppName}}", { driveAppName: DRIVE.app_name });

    return (
        <Button
            size="small"
            variant="secondary"
            fullWidth
            onClick={upload}
            disabled={state === "uploading"}
            aria-busy={state === "uploading"}
            color={state === "error" ? "error" : "brand"}
            icon={
                state === "uploading"
                    ? <Spinner size="sm" />
                    : <Icon name="drive_folder_upload" />
            }
        >
            {label}
        </Button>
    );
};
