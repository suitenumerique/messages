import { Badge } from "@/features/ui/components/badge"
import { ColorHelper } from "@/features/utils/color-helper"
import { ThreadLabel, useLabelsAddThreadsCreate, useLabelsRemoveThreadsCreate } from "@/features/api/gen"
import { useMailboxContext } from "@/features/providers/mailbox";
import { useTranslation } from "react-i18next";
import { Icon, IconSize, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Tooltip } from "@openfun/cunningham-react";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useMemo } from "react";
import { addToast, ToasterItem } from "../toaster";
import { toast } from "react-toastify";
import useAbility, { Abilities } from "@/hooks/use-ability";
import clsx from "clsx";

type LabelBadgeProps = {
    label: ThreadLabel;
    linkable?: boolean;
    removable?: boolean;
    compact?: boolean;
}

export const LabelBadge = ({ label, removable = false, linkable = false, compact = false }: LabelBadgeProps) => {
    const { t } = useTranslation();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const link = useMemo(() => {
        const params = new URLSearchParams({ label_slug: label.slug });
        return `${pathname}?${params.toString()}`;
    }, [label, pathname]);
    const isActive = searchParams.get('label_slug') === label.slug;
    const { invalidateThreadMessages, selectedThread, selectedMailbox } = useMailboxContext();
    const canManageLabels = useAbility(Abilities.CAN_MANAGE_MAILBOX_LABELS, selectedMailbox);
    const badgeColor = ColorHelper.getContrastColor(label.color!);
    const { mutate: deleteLabelMutation, isPending: isDeletingLabel } = useLabelsRemoveThreadsCreate({
        mutation: {
            onSuccess: (_, variables) => {
                invalidateThreadMessages();
                addToast(
                    <ToasterItem
                        type="info"
                        actions={[{
                            label: t('Undo'),
                            onClick: () => addLabelMutation(variables)
                        }]}
                    >
                        <span className="material-icons">label_off</span>
                        <span>{t('Label "{{label}}" removed from this conversation.', { label: label.name })}</span>
                    </ToasterItem>,
                    {
                        toastId: JSON.stringify(variables),
                    }
                )
            }
        }
    });
    const { mutate: addLabelMutation, } = useLabelsAddThreadsCreate({
        mutation: {
            onSuccess: (_, variables) => {
                invalidateThreadMessages();
                toast.dismiss(JSON.stringify(variables));
            }
        }
    });
    const showLink = linkable && !isActive;

    return (
        <Badge title={label.name} className={clsx("label-badge", {"label-badge--compact": compact })} style={{ backgroundColor: label.color, color: badgeColor }}>
            {showLink ? <Link className="label-badge__label" href={link}>{label.name}</Link> : <span className="label-badge__label">{label.name}</span>}
            {canManageLabels && selectedThread?.id && removable && (
                <Tooltip content={t('Delete')} placement="right">
                    <button
                        className="label-badge__remove-cta"
                        onClick={() => deleteLabelMutation({ id: label.id, data: { thread_ids: [selectedThread.id] } })}
                        disabled={isDeletingLabel}
                        aria-busy={isDeletingLabel}
                    >
                        {isDeletingLabel ? <Spinner size="sm" /> : <Icon name="close" size={IconSize.SMALL} type={IconType.OUTLINED} />}
                        <span className="c__offscreen">{t('Delete')}</span>
                    </button>
                </Tooltip>
            )}
        </Badge>
    )
}
