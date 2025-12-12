import { ToasterItem } from "@/features/ui/components/toaster";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

type UndoSendToastProps = {
    delay: number;
    onUndo: () => void;
    onComplete: () => void;
};

export const UndoSendToast = ({ delay, onUndo, onComplete }: UndoSendToastProps) => {
    const { t } = useTranslation();
    const [countdown, setCountdown] = useState(delay);
    const [isCancelled, setIsCancelled] = useState(false);

    useEffect(() => {
        if (countdown <= 0 || isCancelled) {
            return;
        }

        const timer = setInterval(() => {
            setCountdown((prev) => {
                const next = prev - 1;
                if (next <= 0) {
                    clearInterval(timer);
                    onComplete();
                }
                return next;
            });
        }, 1000);

        return () => clearInterval(timer);
    }, [countdown, onComplete, isCancelled]);

    const handleUndo = () => {
        setIsCancelled(true);
        onUndo();
    };

    return (
        <ToasterItem
            type="info"
            actions={[{ label: t("Undo"), onClick: handleUndo }]}
        >
            <span>{t("Sending in {{seconds}} seconds...", { seconds: countdown })}</span>
        </ToasterItem>
    );
};
