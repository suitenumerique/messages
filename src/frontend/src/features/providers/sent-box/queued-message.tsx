import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import soundbox from "@/features/utils/soundbox";
import { Icon, Spinner } from "@gouvfr-lasuite/ui-kit";
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
                <span>{t('Sending message...')}</span>
            </ToasterItem>,
            {
                autoClose: false,
                onClose: onSettled
            }
        ));
    }, []);

    useEffect(() => {
        if (taskQuery.isError) {
            toast.update(toastId, {
                render: (
                    <ToasterItem type="error">
                        <Icon name="error" />
                        <span>{t('The message could not be sent.')}</span>
                    </ToasterItem>
                ),
                autoClose: QUEUED_MESSAGE_CLOSE_DELAY * 2,
            });
            onSettled?.();
            return;
        }

        const status_code = taskQuery?.data?.status;

        if (!status_code) return;

        setRetryCount(retryCount => retryCount + 1);

        const status = taskQuery.data!.data.status;

        if (status === StatusEnum.SUCCESS) {
            toast.update(toastId, {
                render: (
                    <ToasterItem type="info">
                        <Icon name="check_circle" />
                        <span>{t('Message sent successfully')}</span>
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
                        <Icon name="error" />
                        <span>{t('The message could not be sent.')}</span>
                    </ToasterItem>
                ),
                autoClose: QUEUED_MESSAGE_CLOSE_DELAY * 2,
            });
            onSettled?.();
        }
    }, [taskQuery.error, taskQuery.data]);

    useEffect(() => {
        if (hasTimedOut) {
            // The send didn't fail: the backend already un-drafted the message and
            // the SMTP task is still running. Reassure the user and point them to
            // the Outbox rather than showing a misleading error. onSettled refreshes
            // the stats so the Outbox folder reflects the pending message.
            toast.update(toastId, {
                render: (
                    <ToasterItem type="warning">
                        <Icon name="schedule_send" />
                        <span>{t('Sending is taking longer than expected. You can track your message in the Outbox.')}</span>
                    </ToasterItem>
                ),
                autoClose: QUEUED_MESSAGE_CLOSE_DELAY * 2,
            });
            onSettled?.();
            return;
        }
    }, [hasTimedOut]);

    return null;
}