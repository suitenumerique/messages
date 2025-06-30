import { Badge } from "@/features/ui/components/badge"
import { ColorHelper } from "@/features/utils/color-helper"
import { ThreadLabel, useLabelsAddThreadsCreate, useLabelsRemoveThreadsCreate } from "@/features/api/gen"
import { useMailboxContext } from "@/features/providers/mailbox";
import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Tooltip } from "@openfun/cunningham-react";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useMemo } from "react";
import { addToast, ToasterItem } from "../toaster";
import { toast } from "react-toastify";

type LabelBadgeProps = {
    label: ThreadLabel;
    linkable?: boolean;
    removable?: boolean;
}

export const LabelBadge = ({ label, removable = false, linkable = false }: LabelBadgeProps) => {
    const { t } = useTranslation();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const link = useMemo(() => {
        const params = new URLSearchParams({ label_slug: label.slug });
        return `${pathname}?${params.toString()}`;
    }, [label, pathname]);
    const isActive = searchParams.get('label_slug') === label.slug;
    const { invalidateThreadMessages, selectedThread } = useMailboxContext();
    const badgeColor = ColorHelper.getContrastColor(label.color!);
    const {mutate: deleteLabelMutation, isPending: isDeletingLabel} = useLabelsRemoveThreadsCreate({
        mutation: {
            onSuccess: (_, variables) => {
                invalidateThreadMessages();
                addToast(
                    <ToasterItem
                        type="info"
                        actions={[{
                            label: t('actions.undo'),
                            onClick: () => addLabelMutation(variables)
                        }]}
                    >
                        <span className="material-icons">label_off</span>
                        <span>{t('labels.thread_unassigned', { label: label.name })}</span>
                    </ToasterItem>,
                    {
                        toastId: JSON.stringify(variables),
                    }
                )
            }
        }
    });
    const {mutate: addLabelMutation, } = useLabelsAddThreadsCreate({
        mutation: {
            onSuccess: (_, variables) => {
                invalidateThreadMessages();
                toast.dismiss(JSON.stringify(variables));
            }
        }
    });
    const showLink = linkable && !isActive;

    return (
        <Badge title={label.name} className="label-badge" style={{ backgroundColor: label.color, color: badgeColor}}>
            {showLink ? <Link href={link}>{label.name}</Link> : label.name}
            {selectedThread?.id && removable && (
                <Tooltip content={t('actions.delete')}>
                    <button
                        className="label-badge__remove-cta"
                        onClick={() => deleteLabelMutation({ id: label.id, data: { thread_ids: [selectedThread.id] } })}
                        disabled={isDeletingLabel}
                        aria-busy={isDeletingLabel}
                    >
                        {isDeletingLabel ? <Spinner size="sm" /> : <span className="material-icons">close</span>}
                        <span className="c__offscreen">{t('actions.delete')}</span>
                    </button>
                </Tooltip>
            )}
        </Badge>
    )
}
