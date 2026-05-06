import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";

type StepCompletedProps = {
    onClose: () => void;
}

export const StepCompleted = ({ onClose }: StepCompletedProps) => {
    const { t } = useTranslation();

    return (
        <div className="importer-completed">
            <div className="importer-completed__description">
                <span className="material-icons">drafts</span>
                <p>{t('Your messages have been imported successfully!')}</p>
            </div>
            <Button onClick={onClose}>{t('Close')}</Button>
        </div>
    );
};
