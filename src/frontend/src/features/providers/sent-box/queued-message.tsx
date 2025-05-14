import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import soundbox from "@/features/utils/soundbox";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Id, toast } from "react-toastify";

type QueueMessageProps = {
    taskId: string;
    onSettled?: () => void;
}

const QUEUED_MESSAGE_POLL_INTERVAL = 1000;
const QUEUED_MESSAGE_CLOSE_DELAY = 2000;
const QUEUED_MESSAGE_TIMEOUT = 30000;

export const QueueMessage = ({ taskId, onSettled }: QueueMessageProps) => {
    const { t } = useTranslation();
    const [retryCount, setRetryCount] = useState(0);
    const hasTimedOut = useMemo(() => retryCount * QUEUED_MESSAGE_POLL_INTERVAL > QUEUED_MESSAGE_TIMEOUT, [retryCount]);
    const [toastId, setToastId] = useState<Id>('');
    const taskQuery = useTasksRetrieve(taskId, {
        query: {
            refetchInterval: QUEUED_MESSAGE_POLL_INTERVAL,
            enabled: !hasTimedOut,
            meta: {
                noGlobalError: true,
            }
        }
    });

    useEffect(() => {
        soundbox.load("/sounds/mail-sent.ogg");
        setToastId(addToast(
            <ToasterItem type="info">
                <Spinner size="sm" />
                <span>{t('queued_message.sending')}</span>
            </ToasterItem>,
            {
                autoClose: false,
                onClose: onSettled
            }
        ));
    }, []);

    useEffect(() => {
        const status_code = taskQuery?.data?.status;
        
        if (!status_code) return;

        setRetryCount(retryCount => retryCount + 1);
        
        const status = taskQuery.data!.data.status;

        if (status === StatusEnum.SUCCESS) {
            toast.update(toastId, {
                render: (
                    <ToasterItem type="info">
                        <span className="material-icons">check_circle</span>
                        <span>{t('queued_message.sent')}</span>
                    </ToasterItem>
                ),
                autoClose: QUEUED_MESSAGE_CLOSE_DELAY,
            });
            soundbox.play(0.07);
            onSettled?.();
        } else if (status === StatusEnum.FAILURE) {
            toast.update(toastId, {
                render: (
                    <ToasterItem type="error">
                        <span className="material-icons">error</span>
                        <span>{t('queued_message.error')}</span>
                    </ToasterItem>
                ),
                autoClose: QUEUED_MESSAGE_CLOSE_DELAY * 2,
            });
            onSettled?.();
        }
    }, [taskQuery.error, taskQuery.data]);

    useEffect(() => {
        if (hasTimedOut) {
            toast.update(toastId, {
                render: <ToasterItem type="error"><span>{t('queued_message.sent_too_long')}</span></ToasterItem>,
                autoClose: QUEUED_MESSAGE_CLOSE_DELAY * 2,
            });
            onSettled?.();
            return;
        }
    }, [hasTimedOut]);

    return null;
}