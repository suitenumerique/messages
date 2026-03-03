import { useTranslation } from "react-i18next"
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit"

export const StarredMarker = () => {
    const { t } = useTranslation()

    return (
        <div className="starred-marker">
            <span className="starred-marker__label">
                <Icon name="star" type={IconType.FILLED} className="starred-marker__icon" />
                {t("This thread has been starred.")}
            </span>
        </div>
    )
}
