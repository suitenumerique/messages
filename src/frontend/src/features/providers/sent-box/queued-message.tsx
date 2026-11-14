import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import soundbox from "@/features/utils/soundbox";
import { Spinner } from "@gouvfr-lasuite/ui-components";
import { Icon } from "@/features/ui/components/icon";
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

/**
 * Per-recipient outcome of the send, aggregated by the backend task.
 *
 * The Celery state only says the task did not raise: a send that reached
 * nobody still lands on SUCCESS. What actually happened travels in the result.
 */
export type SendTaskResult = {
    delivery_status?: "completed" | "partial" | "pending" | "failed" | "cancelled";
};

type DeliveryToast = {
    type: "info" | "warning" | "error";
    icon: string;
    message: string;
    /** The chime means "it's gone": only a fully delivered send earns it. */
    chime: boolean;
};

/**
 * Toast for a completed task, given its aggregated delivery status.
 *
 * An unknown or absent status keeps the optimistic toast: a worker that
 * predates the field can still hand back a result after a deploy. The
 * message's own banner in the thread lists affected recipients.
 */
export const getDeliveryToast = (
    result: SendTaskResult | null,
    t: (key: string) => string,
): DeliveryToast => {
    switch (result?.delivery_status) {
        case "partial":
            return {
                type: "warning",
                icon: "warning",
                message: t("Message sent, but not to every recipient."),
                chime: false,
            };
        case "pending":
            return {
                type: "warning",
                icon: "schedule_send",
                message: t("Message sent. Delivery is still in progress."),
                chime: false,
            };
        case "failed":
            return {
                type: "error",
                icon: "error",
                message: t("The message could not be delivered to any recipient."),
                chime: false,
            };
        case "cancelled":
            return {
                type: "warning",
                icon: "cancel",
                message: t("Sending was cancelled."),
                chime: false,
            };
        default:
            return {
                type: "info",
                icon: "check_circle",
                message: t("Message sent successfully"),
                chime: true,
            };
    }
};

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
            const delivery = getDeliveryToast(
                taskQuery.data!.data.result as SendTaskResult | null,
                t,
            );
            toast.update(toastId, {
                render: (
                    <ToasterItem type={delivery.type}>
                        <Icon name={delivery.icon} />
                        <span>{delivery.message}</span>
                    </ToasterItem>
                ),
                autoClose: delivery.chime
                    ? QUEUED_MESSAGE_CLOSE_DELAY
                    : QUEUED_MESSAGE_CLOSE_DELAY * 2,
            });
            if (delivery.chime) {
                soundbox.play(0.07);
            }
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