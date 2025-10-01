import { useCallback, useState } from "react"
import { Button, Tooltip } from "@openfun/cunningham-react"
import { openPicker, type Item, type PickerResult } from "@gouvfr-lasuite/drive-sdk";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useConfig } from "@/features/providers/config";
import { FEATURE_KEYS, useFeatureFlag } from "@/hooks/use-feature";
import { DriveIcon } from "./drive-icon";
import { Attachment } from "@/features/api/gen/models/attachment";

export type DriveFile = { url: string } & Omit<Attachment, 'sha256' | 'blobId'>;

type DriveAttachmentPickerProps = {
    onPick: (attachments: DriveFile[]) => void;
}

/**
 * DriveAttachmentPicker is a component that allows the user to pick files
 * from a Drive instance if one is configured otherwise it will return null.
 *
 * Drive Config is retrieved from the backend. Take a look at the `DRIVE_CONFIG`
 * in the `settings.py` file in the backend.
 *
 * https://github.com/suitenumerique/drive
 */
export const DriveAttachmentPicker = ({ onPick }: DriveAttachmentPickerProps) => {
    const { t } = useTranslation();
    const [isLoading, setIsLoading] = useState(false);
    const config = useConfig();
    const isDriveDisabled = !useFeatureFlag(FEATURE_KEYS.DRIVE);
    const serializeToDriveFile = (item: Item): DriveFile => ({
        id: item.id,
        name: item.title,
        url: item.url,
        type: item.type,
        size: item.size,
        created_at: new Date().toISOString(),
    });

    const pick = useCallback(async () => {
        if (isDriveDisabled) return;
        setIsLoading(true);
        let result: PickerResult | null = null;

        try {
            result = await openPicker({
                url: config.DRIVE!.sdk_url,
                apiUrl: config.DRIVE!.api_url,
            });
        } catch (error) {
            console.error(error);
        } finally {
            setIsLoading(false);
        }

        if (result?.type === "picked" && result.items) {
            onPick(result.items.map(serializeToDriveFile));
        }
    }, [isDriveDisabled]);

    if (isDriveDisabled) return null;

    return (
        <Tooltip content={t('Add attachment from Fichiers')}>
            <Button
                color="tertiary"
                icon={isLoading ? <Spinner size="sm" /> : <DriveIcon />}
                type="button"
                disabled={isLoading}
                aria-busy={isLoading}
                onClick={pick}
                className="drive-attachment-picker"
            />
        </Tooltip>
    )
}
