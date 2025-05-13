import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import soundbox from "@/features/utils/soundbox";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Id, toast } from "react-toastify";

type QueueMessageProps = {
    taskId: string;
    onSettled?: () => void;
}

const QUEUED_MESSAGE_POLL_INTERVAL = 1000;
const QUEUED_MESSAGE_CLOSE_DELAY = 2000;

export const QueueMessage = ({ taskId, onSettled }: QueueMessageProps) => {
    const { t } = useTranslation();
    const [toastId, setToastId] = useState<Id>('');
    const taskQuery = useTasksRetrieve(taskId, {
        query: {
            refetchInterval: QUEUED_MESSAGE_POLL_INTERVAL,
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
        const status_code = taskQuery?.data?.status ?? taskQuery.error?.code;
        
        if (!status_code) return;
        if (status_code === 404) {
            toast.update(toastId, {
                render: <ToasterItem type="error"><span>{t('queued_message.not_found')}</span></ToasterItem>,
                autoClose: QUEUED_MESSAGE_CLOSE_DELAY * 2,
            });
            onSettled?.();
            return;
        }
        
        const status = taskQuery.data!.data.status!;

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

    return null;
}