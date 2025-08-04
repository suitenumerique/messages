import { TreeLabel, ThreadsStatsRetrieveStatsFields, useLabelsDestroy, useLabelsList, useThreadsStatsRetrieve, ThreadsStatsRetrieve200, useLabelsAddThreadsCreate, useLabelsRemoveThreadsCreate, useLabelsPartialUpdate } from "@/features/api/gen";
import { useMailboxContext } from "@/features/providers/mailbox";
import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button, useModals } from "@openfun/cunningham-react";
import clsx from "clsx";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useLayoutContext } from "@/features/layouts/components/main";
import router from "next/router";
import { MAILBOX_FOLDERS } from "../../../mailbox-list";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { toast } from "react-toastify";
import { useFold } from "@/features/providers/fold";
import { SubLabelCreation } from "../label-form-modal";

export type LabelTransferData = {
  type: 'label';
  label: Pick<TreeLabel, 'id' | 'display_name' | 'name'>;
}

type LabelItemProps = TreeLabel & {
  level?: number;
  onEdit: (label: TreeLabel | SubLabelCreation) => void;
  canManage: boolean;
  defaultFoldState?: false | undefined;
}

export const LabelItem = ({ level = 0, onEdit, canManage, defaultFoldState, ...label }: LabelItemProps) => {
  const { selectedMailbox, invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();
  const modals = useModals();
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
  const foldKey = useMemo(() => `label-item-${label.display_name}${label.children.length > 0 ? `-with-children` : ''}`, [label.display_name, label.children.length]);
  const { isFolded, toggle } = useFold(foldKey, isFoldedByDefault);
  const foldTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const goToDefaultFolder = () => {
    const defaultFolder = MAILBOX_FOLDERS[0];
    router.push(pathname + `?${new URLSearchParams(defaultFolder.filter).toString()}`);
  }
  const moveLabelMutation = useLabelsPartialUpdate();

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
  const labelsQuery = useLabelsList({ mailbox_id: selectedMailbox!.id }, { query: { enabled: false } })
  const hasChildren = label.children && label.children.length > 0;
  const handleClick = (e: React.MouseEvent<HTMLButtonElement>) => {
    e.preventDefault();
    toggle();
  }

  const deleteThreadMutation = useLabelsRemoveThreadsCreate({
    mutation: {
      onSuccess: (_, variables) => {
        invalidateThreadMessages();
        toast.dismiss(JSON.stringify(variables));
      },
    },
  });

  const addThreadMutation = useLabelsAddThreadsCreate({
    mutation: {
      onSuccess: (_, variables) => {
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

  const handleDragStart = (e: React.DragEvent<HTMLAnchorElement>) => {
    e.dataTransfer.setData('application/json', JSON.stringify({
      type: 'label',
      label: {
        id: label.id,
        display_name: label.display_name,
        name: label.name
      }
    } as LabelTransferData));
    e.dataTransfer.effectAllowed = 'link'
  }

  const handleDragOver = (e: React.DragEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = 'link';
    setIsDragOver(true);
    if (!foldTimeoutRef.current) {
      foldTimeoutRef.current = setTimeout(() => {
        if (isFolded === true) toggle();
      }, 750);
    }
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
    if (foldTimeoutRef.current) {
      clearTimeout(foldTimeoutRef.current);
      foldTimeoutRef.current = null;
    }
  };

  const handleDropThread = (transferData: { threadId: string, labels: string[] }) => {
    const canBeAssigned = !transferData.labels.includes(label.id);
    if (transferData.threadId && canBeAssigned) {
      addThreadMutation.mutate({
        id: label.id,
        data: {
          thread_ids: [transferData.threadId],
        },
      });
    }
  }

  const handleDropLabel = (transferData: LabelTransferData) => {
    // If label is dropped on itself do nothing.
    if (transferData.label.id === label.id) return;
    // If label is dropped on a child do nothing.
    if (label.name.startsWith(`${transferData.label.name}/`)) return;
    // If label is dropped on a direct parent do nothing.
    if (transferData.label.name === `${label.name}/${transferData.label.display_name}`) return;

    moveLabelMutation.mutate({
      id: transferData.label.id,
      data: {
        name: [label.name, transferData.label.display_name].join('/'),
      }
    }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: labelsQuery.queryKey });
      },
    });
  }

  const handleDrop = (e: React.DragEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
    const rawData = e.dataTransfer.getData('application/json');
    if (!rawData) return;

    try {
      const data = JSON.parse(rawData);

      if (data.type === 'thread') handleDropThread(data);
      else if (data.type === 'label') handleDropLabel(data);
    } catch (error) {
      console.error('Error parsing drag data:', error);
    }
  };

  const handleDelete = async () => {
    const decision = await modals.deleteConfirmationModal({
      title: <span className="c__modal__text--centered">{t('labels.delete_modal.title', { label: label.display_name })}</span>,
      children: t('labels.delete_modal.message'),
    });

    if (decision === 'delete') {
      deleteMutation.mutate({ id: label.id }, {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: labelsQuery.queryKey });
        },
      })
    }
  }

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
        onDragStart={handleDragStart}
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
            <Icon className="label-item__icon" icon="label" name="label" style={{ 'color': label.color, '--strokeColor': `${label.color}AF` }} />
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
                    label: t('labels.add_sub_label'),
                    icon: <span className="material-icons">add</span>,
                    callback: () => onEdit({ name: `${label.name}/`, color: label.color }),
                    showSeparator: true,
                  },
                  {
                    label: t('actions.delete'),
                    icon: <span className="material-icons">delete</span>,
                    callback: handleDelete,
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
          {unreadCount > 0 && <span className="mailbox__item-counter">{unreadCount}</span>}
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
