import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { ImportTaskRecap } from "@/hooks/use-task-status";
import clsx from "clsx";

type StepCompletedProps = {
    onClose: () => void;
    recap: ImportTaskRecap | null;
}

const FAILURE_WARNING_THRESHOLD = 0.5;

export const StepCompleted = ({ onClose, recap }: StepCompletedProps) => {
    const { t } = useTranslation();

    const total = recap?.totalMessages ?? 0;
    const success = recap?.successCount ?? 0;
    const failure = recap?.failureCount ?? 0;
    const hasRecap = recap !== null && total > 0;
    const successPercent = hasRecap ? Math.round((success / total) * 100) : 100;
    const showFailureWarning = hasRecap && failure / total > FAILURE_WARNING_THRESHOLD;

    return (
        <div className="importer-completed">
            <span className={clsx(
                'importer-completed__badge',
                { 'importer-completed__badge--warning': showFailureWarning }
            )}>
                <span className="material-icons">
                    {showFailureWarning ? 'report_problem' : 'check'}
                </span>
            </span>
            <div className="importer-completed__heading">
                <p className="importer-completed__title">{t('Import complete')}</p>
                {hasRecap && (
                    <p className="importer-completed__percent">
                        {t('{{progress}}% imported', { progress: successPercent })}
                    </p>
                )}
            </div>
            {hasRecap && (
                <ul className="importer-completed__stats">
                    <li>
                        <span className="importer-completed__stat-dot importer-completed__stat-dot--success" />
                        <span>
                            {t('Imported: {{count}} of {{total}} messages', {
                                count: success,
                                total,
                            })}
                        </span>
                    </li>
                    {failure > 0 && (
                        <li>
                            <span className="importer-completed__stat-dot importer-completed__stat-dot--failure" />
                            <span>
                                {t('Failed: {{count}} messages', { count: failure })}
                            </span>
                            {showFailureWarning && (
                                <span
                                    className="material-icons importer-completed__percent-warning"
                                    aria-label={t('High failure rate')}
                                    title={t('High failure rate')}
                                >
                                    report_problem
                                </span>
                            )}
                        </li>
                    )}
                </ul>
            )}
            <Button onClick={onClose}>{t('Close')}</Button>
        </div>
    );
};
