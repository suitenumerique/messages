import clsx from "clsx";
import { useTranslation } from "react-i18next";

type DropZoneProps = {
    isHidden: boolean;
}

export const DropZone = ({ isHidden }: DropZoneProps) => {
    const { t } = useTranslation();

    return (
        <div className={clsx("attachment-uploader__dropzone", { "attachment-uploader__dropzone--hidden": isHidden })} aria-hidden={isHidden}>
            <span className="material-icons">file_download</span>
            {t("message_form.attachments_uploader.drop_attachments_here")}
        </div>
    );
};
