import { TreeLabel, ThreadsStatsRetrieveStatsFields, useLabelsDestroy, useLabelsList, useThreadsStatsRetrieve, ThreadsStatsRetrieve200 } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button, useModal } from "@openfun/cunningham-react";
import clsx from "clsx";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/features/ui/components/badge";
import { LabelModal } from "../label-form-modal";

type LabelItemProps = TreeLabel & {
    level?: number;
  }

export  const LabelItem = ({ level = 0, ...label }: LabelItemProps) => {
    const { selectedMailbox } = useMailboxContext();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const { isOpen, onClose, open } = useModal();
    const queryParams = useMemo(() => {
      const params = new URLSearchParams({ label_slug: label.slug });
      return params.toString();
    }, [label.slug]);
    const { data: stats } = useThreadsStatsRetrieve({
      mailbox_id: selectedMailbox?.id,
      stats_fields: ThreadsStatsRetrieveStatsFields.all_unread,
      label_slug: label.slug
    }, {
      query: {
        queryKey: ['threads', 'stats', selectedMailbox!.id, queryParams],
      }
    });
    const unreadCount = (stats?.data as ThreadsStatsRetrieve200)?.all_unread ?? 0;
    const [isExpanded, setIsExpanded] = useState(false);
    const searchParams = useSearchParams();
    const { t } = useTranslation();
    const isActive = searchParams.get('label_slug') === label.slug;
    const deleteMutation = useLabelsDestroy();
    const queryClient = useQueryClient();
    const labelsQuery = useLabelsList({ mailbox_id: selectedMailbox!.id })
    const hasChildren = label.children && label.children.length > 0;
    const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
      e.preventDefault();
      setIsExpanded(!isExpanded);
    }

    return (
      <>
        <Link
          href={`/mailbox/${selectedMailbox?.id}?${queryParams}`}
          className={clsx("label-item", isActive && "label-item--active")}
          style={{ paddingLeft: `${level * 1}rem` }}
          data-focus-within={isDropdownOpen}
          title={label.display_name}
        >
          <div className="label-item__column">
            <button
              onClick={handleClick}
              className='label-item__toggle'
              disabled={!hasChildren}
            >
              <Icon type={IconType.OUTLINED} icon={isExpanded ? "expand_more" : "chevron_right"} name={isExpanded ? "expand_more" : "chevron_right"} />
              <span className="c__offscreen">{isExpanded ? t('labels.collapse') : t('labels.expand')}</span>
            </button>
            <div className="label-item__name">
              <Icon icon="label" name="label" color={label.color} />
              <span className="label-name label-name--truncated">{label.display_name}</span>
            </div>
          </div>
          <div className="label-item__column">
            <div className="label-item__dropdown-actions">
              <DropdownMenu
                isOpen={isDropdownOpen}
                onOpenChange={setIsDropdownOpen}
                options={[
                  {
                    label: t('actions.edit'),
                    icon: <span className="material-icons">edit</span>,
                    callback: open,
                  },
                  {
                    label: t('actions.delete'),
                    icon: <span className="material-icons">delete</span>,
                    callback: () => deleteMutation.mutate({ id: label.id }, {
                      onSuccess: () => {
                        queryClient.invalidateQueries({ queryKey: labelsQuery.queryKey });
                      },
                    }),
                  },
                ]}
              >
                <Button
                  onClick={() => setIsDropdownOpen(true)}
                  icon={<span className="material-icons">more_vert</span>}
                  color="primary-text"
                  aria-label={t('tooltips.more_options')}
                  size="small"
                />
              </DropdownMenu>
            </div>
            {unreadCount > 0 && <Badge>{unreadCount}</Badge>}
          </div>
        </Link>

        {hasChildren && isExpanded && (
          <div className="label-children">
            {label.children.map((child) => (
              <LabelItem key={child.id} {...child} level={level + 1} />
            ))}
          </div>
        )}
        <LabelModal isOpen={isOpen} onClose={onClose} label={label} />
      </>
    );
  }
