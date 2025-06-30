import { TreeLabel, ThreadsStatsRetrieveStatsFields, useLabelsDestroy, useLabelsList, useThreadsStatsRetrieve, ThreadsStatsRetrieve200, useLabelsAddThreadsCreate, useLabelsRemoveThreadsCreate } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button, useModal } from "@openfun/cunningham-react";
import clsx from "clsx";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/features/ui/components/badge";
import { LabelModal } from "../label-form-modal";
import { useLayoutContext } from "@/features/layouts/components/main";
import router from "next/router";
import { MAILBOX_FOLDERS } from "../../../mailbox-list";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { toast } from "react-toastify";

type LabelItemProps = TreeLabel & {
    level?: number;
  }

export  const LabelItem = ({ level = 0, ...label }: LabelItemProps) => {
    const { selectedMailbox, invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const { isOpen, onClose, open } = useModal();
    const [isDragOver, setIsDragOver] = useState(false);
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
    const { closeLeftPanel } = useLayoutContext();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const { t } = useTranslation();
    const isActive = searchParams.get('label_slug') === label.slug;
    const hasActiveChild = searchParams.get('label_slug')?.startsWith(`${label.slug}-`);
    const [isExpanded, setIsExpanded] = useState(hasActiveChild);
    const goToDefaultFolder = () => {
      const defaultFolder = MAILBOX_FOLDERS[0];
      router.push(pathname + `?${new URLSearchParams(defaultFolder.filter).toString()}`);
  }
    const deleteMutation = useLabelsDestroy({
      mutation: {
        onSuccess: () => {
          if (searchParams.get('label_slug') === label.slug ||
              searchParams.get('label_slug')?.startsWith(`${label.slug}-`)) {
            const newSearchParams = new URLSearchParams(searchParams.toString());
            newSearchParams.delete('label_slug');
            if (newSearchParams.toString()) {
              router.push(`${pathname}?${newSearchParams.toString()}`);
            } else {
              goToDefaultFolder();
            }
          }
        },
      },
    });
    const queryClient = useQueryClient();
    const labelsQuery = useLabelsList({ mailbox_id: selectedMailbox!.id })
    const hasChildren = label.children && label.children.length > 0;
    const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
      e.preventDefault();
      setIsExpanded(!isExpanded);
    }

    const deleteThreadMutation = useLabelsRemoveThreadsCreate({
      mutation: {
        onSuccess: ( _, variables) => {
          invalidateThreadMessages();
          toast.dismiss(JSON.stringify(variables));
        },
      },
    });

    const addThreadMutation = useLabelsAddThreadsCreate({
      mutation: {
        onSuccess: ( _, variables) => {
          // Invalidate relevant queries to refresh the UI
          invalidateThreadMessages();
          invalidateThreadsStats();

          // Show success toast
          addToast(
            <ToasterItem
              type="info"
              actions={[{
                label: t('actions.undo'), onClick: () => deleteThreadMutation.mutate(variables)
              }]}
            >
              <span className="material-icons">label</span>
              <span>{t('labels.thread_assigned', { label: label.name })}</span>
            </ToasterItem>, {
              toastId: JSON.stringify(variables),
            }
          );
        },
      },
    });

    const handleDragOver = (e: React.DragEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'link';
      setIsDragOver(true);
    };

    const handleDragLeave = () => {
      setIsDragOver(false);
    };

    const handleDrop = (e: React.DragEvent<HTMLAnchorElement>) => {
      e.preventDefault();
      setIsDragOver(false);

      try {
        const data = JSON.parse(e.dataTransfer.getData('application/json'));
        const canBeAssigned = !data.labels.includes(label.id);
        if (data.type === 'thread' && data.threadId && canBeAssigned) {
          addThreadMutation.mutate({
            id: label.id,
            data: {
              thread_ids: [data.threadId],
            },
          });
        }
      } catch (error) {
        console.error('Error parsing drag data:', error);
      }
    };

    return (
      <>
        <Link
          href={`${pathname}?${queryParams}`}
          onClick={closeLeftPanel}
          className={clsx("label-item", isActive && "label-item--active", isDragOver && "label-item--drag-over")}
          style={{ paddingLeft: `${level * 1}rem` }}
          data-focus-within={isDropdownOpen}
          title={label.display_name}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          <div className="label-item__column">
            <button
              onClick={handleClick}
              className='label-item__toggle'
              disabled={!hasChildren}
              aria-expanded={isExpanded}
              title={isExpanded ? t('labels.collapse') : t('labels.expand')}
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
