import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";

import { TaskMetadata } from "./step-loader";

type StepCompletedProps = {
    onClose: () => void;
    metadata?: TaskMetadata;
}

export const StepCompleted = ({ onClose, metadata }: StepCompletedProps) => {
    const { t } = useTranslation();

    const hasPartialFailures = metadata?.failure_count && metadata.failure_count > 0;

    return (
        <div className="importer-completed">
            <div className="importer-completed__description">
                <span className={`material-icons ${hasPartialFailures ? 'importer-completed__icon--partial' : ''}`}>
                    mark_email_read
                </span>
                {hasPartialFailures ? (
                    <>
                        <p>{t('Import completed with warnings')}</p>
                        <div style={{ marginTop: 'var(--c--globals--spacings--2xl)', textAlign: 'center' }}>
                            <p style={{ fontSize: 'var(--c--globals--font--sizes--base)', fontWeight: 'normal', color: 'var(--c--contextuals--content--semantic--warning--primary)' }}>
                                {t('{{success}} messages imported successfully', { success: metadata.success_count || 0 })}
                            </p>
                            <p style={{ fontSize: 'var(--c--globals--font--sizes--base)', fontWeight: 'normal', color: 'var(--c--contextuals--content--semantic--error--primary)' }}>
                                {t('{{failed}} messages failed to import', { failed: metadata.failure_count })}
                            </p>
                        </div>
                    </>
                ) : (
                    <p>{t('Your messages have been imported successfully!')}</p>
                )}
            </div>
            <Button onClick={onClose}>{t('Close')}</Button>
        </div>
    );
};
