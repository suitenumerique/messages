import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Attachment } from "@/features/api/gen/models";
import usePrevious from "@/hooks/use-previous";
import { Spinner, Icon } from "@gouvfr-lasuite/ui-kit";
import { Tooltip, Button } from "@gouvfr-lasuite/cunningham-react";
import clsx from "clsx";
import { DrivePreviewLink } from "./drive-preview-link";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { useConfig } from "@/features/providers/config";
import { useDriveUpload } from "./use-drive-upload";


type DriveUploadButtonProps = {
    attachment: Attachment;
}

/**
 * DriveUploadButton
 * Button to save an attachment to the user's Drive workspace.
 * Uses a get_or_create pattern: the backend checks if the file already exists
 * before uploading, returning 200 (existing) or 201 (created).
 */
export const DriveUploadButton = ({ attachment }: DriveUploadButtonProps) => {
    const { t } = useTranslation();
    const { DRIVE } = useConfig();
    const isDriveDisabled = !useFeatureFlag(FEATURE_KEYS.DRIVE);
    const { state, driveFileId, upload } = useDriveUpload(attachment.blobId);
    const prevState = usePrevious(state);
    const showUploadTooltip = useMemo(() => ['success', 'error'].includes(state), [state]);

    const StateIcon = useMemo(() => {
        if (state === 'uploading') return <Spinner size="sm" />;
        if (state === 'success') return <Icon name="check_circle" />;
        if (state === 'error') return <Icon name="error" />;
        return <Icon name="drive_folder_upload" />;
    }, [state]);

    if (isDriveDisabled) return null;

    return (
        <div className="attachment-item-drive-upload-button-container">
            {(driveFileId && state === 'idle') ? <DrivePreviewLink fileId={driveFileId} /> : (
                <Tooltip content={t("Save into your {{driveAppName}}'s workspace", { driveAppName: DRIVE.app_name })}>
                    <Button
                        aria-label={t("Save into your {{driveAppName}}'s workspace", { driveAppName: DRIVE.app_name })}
                        size="medium"
                        icon={StateIcon}
                        disabled={state === 'uploading' || state !== 'idle'}
                        aria-busy={state === 'uploading'}
                        color={state === 'error' ? 'error' : 'brand'}
                        variant="tertiary"
                        onClick={upload}
                        data-state={state}
                        className="attachment-item-drive-upload-button"
                    />
                </Tooltip>
            )}
            <div
                className={clsx(
                    "attachment-item--drive-upload-tooltip",
                    {
                        "attachment-item--drive-upload-tooltip--visible": showUploadTooltip,
                        "attachment-item--drive-upload-tooltip--error": state === 'error',
                    })}
                aria-live="polite"
                aria-hidden={!showUploadTooltip}
            >
                {(state === 'success' || prevState === 'success') && t("Attachment saved into your {{driveAppName}}'s workspace.", { driveAppName: DRIVE.app_name })}
                {(state === 'error' || prevState === 'error') && t("Attachment failed to be saved into your {{driveAppName}}'s workspace.", { driveAppName: DRIVE.app_name })}
            </div>
        </div>
    )
}
