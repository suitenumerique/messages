import { TreeLabel, ThreadsStatsRetrieveStatsFields, useLabelsDestroy, useLabelsList, useThreadsStatsRetrieve, ThreadsStatsRetrieve200, useLabelsAddThreadsCreate, useLabelsRemoveThreadsCreate } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import clsx from "clsx";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/features/ui/components/badge";
import { useLayoutContext } from "@/features/layouts/components/main";
import router from "next/router";
import { MAILBOX_FOLDERS } from "../../../mailbox-list";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { toast } from "react-toastify";
import { useFold } from "@/features/providers/fold";
import { SubLabelCreation } from "../label-form-modal";

type LabelItemProps = TreeLabel & {
    level?: number;
    onEdit: (label: TreeLabel | SubLabelCreation) => void;
    canManage: boolean;
    defaultFoldState?: false | undefined;
  }

export  const LabelItem = ({ level = 0, onEdit, canManage, defaultFoldState, ...label }: LabelItemProps) => {
    const { selectedMailbox, invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
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
    const hasActiveChild = Boolean(searchParams.get('label_slug')?.startsWith(`${label.slug}-`));
    const isFoldedByDefault = label.children.length === 0 ? null : (defaultFoldState ?? !hasActiveChild);
    const { isFolded, toggle } = useFold(`label-item-${label.display_name}`, isFoldedByDefault);
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
      toggle();
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
      const rawData = e.dataTransfer.getData('application/json');
      if (!rawData) return;

      try {
        const data = JSON.parse(rawData);
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

    const getPaddingLeftItem = (level: number) => {
      let offset = 0;
      if (level === 1 && !hasChildren) offset = 3.3;
      else if (!hasChildren) offset = 2.25;
      else offset = 1.15;

      return `${offset * level}rem`;
    }

    return (
      <>
        <Link
          href={`${pathname}?${queryParams}`}
          onClick={closeLeftPanel}
          className={clsx("label-item", isActive && "label-item--active", isDragOver && "label-item--drag-over")}
          style={level > 0 ? { paddingLeft: getPaddingLeftItem(level) } : {}}
          data-focus-within={isDropdownOpen}
          title={label.display_name}
          onDragOver={canManage ? handleDragOver : undefined}
          onDragLeave={canManage ? handleDragLeave : undefined}
          onDrop={canManage ? handleDrop : undefined}
        >
          <div className="label-item__column">
            {hasChildren && (
              <button
                onClick={handleClick}
                className='label-item__toggle'
                aria-expanded={isFolded}
                title={isFolded ? t('labels.collapse') : t('labels.expand')}
              >
                <Icon type={IconType.OUTLINED} name={isFolded ? "chevron_right" : "expand_more"} />
                <span className="c__offscreen">{isFolded ? t('labels.expand') : t('labels.collapse')}</span>
              </button>
            )}
            <div className="label-item__name">
              <Icon className="label-item__icon" icon="label" name="label" style={{ 'color': label.color, '--strokeColor': `${label.color}AF`}} />
              <span className="label-name label-name--truncated">{label.display_name}</span>
            </div>
          </div>
          <div className="label-item__column">
            {canManage && (
              <div className="label-item__dropdown-actions">
                <DropdownMenu
                  isOpen={isDropdownOpen}
                  onOpenChange={setIsDropdownOpen}
                  options={[
                    {
                      label: t('actions.edit'),
                      icon: <span className="material-icons">edit</span>,
                      callback: () => onEdit(label),
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
                    {
                      label: t('labels.add_sub_label'),
                      icon: <span className="material-icons">add</span>,
                      callback: () => onEdit({ name: `${label.name}/`, color: label.color }),
                    },
                  ]}
                >
                  <Button
                    onClick={() => setIsDropdownOpen(true)}
                    icon={<Icon name="more_horiz" />}
                    color="primary-text"
                    aria-label={t('tooltips.more_options')}
                    size="small"
                  />
                  </DropdownMenu>
              </div>
            )}
            {unreadCount > 0 && <Badge>{unreadCount}</Badge>}
          </div>
        </Link>

        {hasChildren && isFolded === false && (
          <div className="label-children">
            {label.children.map((child) => (
              <LabelItem key={child.id} {...child} level={level + 1} onEdit={onEdit} canManage={canManage} defaultFoldState={defaultFoldState} />
            ))}
          </div>
        )}
      </>
    );
  }
