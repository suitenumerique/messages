import { Mailbox, TreeLabel, useLabelsList, useLabelsPartialUpdate } from "@/features/api/gen";
import { Icon, IconSize, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, useModal, Tooltip } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";
import { LabelModal, SubLabelCreation } from "./components/label-form-modal";
import { LabelItem, LabelTransferData } from "./components/label-item";
import { useEffect, useState } from "react";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { FoldProvider, useFold } from "@/features/providers/fold";
import { useQueryClient } from "@tanstack/react-query";
import { handle } from "@/features/utils/errors";

type MailboxLabelsProps = {
  mailbox: Mailbox;
}

export const MailboxLabelsBase = ({ mailbox }: MailboxLabelsProps) => {
  const { t } = useTranslation();
  const { isOpen, onClose, open } = useModal();
  const [labelToEdit, setLabelToEdit] = useState<TreeLabel | SubLabelCreation | undefined>(undefined);
  const labelsQuery = useLabelsList({ mailbox_id: mailbox.id })
  const canManageLabels = useAbility(Abilities.CAN_MANAGE_MAILBOX_LABELS, mailbox);
  const { areAllFolded, toggleAll } = useFold();
  const [defaultFoldState, setDefaultFoldState] = useState<false | undefined>(undefined);
  const [isDragOver, setIsDragOver] = useState(false);
  const queryClient = useQueryClient();
  const moveLabelMutation = useLabelsPartialUpdate();

  const editLabel = (label: TreeLabel | SubLabelCreation) => {
    setLabelToEdit(label)
    open()
  }

  const handleClose = () => {
    setLabelToEdit(undefined)
    onClose()
  }

  const toggleFolding = () => {
    setDefaultFoldState(areAllFolded ? false : undefined);
    toggleAll();
  }

  useEffect(() => {
    if (defaultFoldState === false) {
      setDefaultFoldState(undefined);
    }
  }, [defaultFoldState]);

  const handleDragOver = (e: React.DragEvent<HTMLElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'link';
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDropLabel = (transferData: LabelTransferData) => {
    // If this is a root label do nothing.
    if (transferData.label.name === transferData.label.display_name) return;
    moveLabelMutation.mutate({
      id: transferData.label.id,
      data: {
        name: transferData.label.display_name,
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

      if (data.type === 'label') handleDropLabel(data);
    } catch (error) {
      handle(new Error('Error parsing drag data.'), { extra: { error } });
    }
  };

  return (
      <section
        className="mailbox-labels"
        onDragOver={canManageLabels ? handleDragOver : undefined}
        onDragLeave={canManageLabels ? handleDragLeave : undefined}
        onDrop={canManageLabels ? handleDrop : undefined}
      >
        <header className="mailbox-labels__header">
          <p className="mailbox-labels__title">{t('Labels')}</p>
          <div className="mailbox-labels__actions">
            {areAllFolded !== undefined && (
            <Tooltip content={areAllFolded ? t('Expand all') : t('Collapse all')} placement="bottom">
              <Button
                icon={<Icon type={IconType.FILLED} name={areAllFolded ? "unfold_more" : "unfold_less"} size={IconSize.LARGE} />}
                color="tertiary-text"
                size="small"
                onClick={toggleFolding}
                className="mailbox-labels__fold-button"
                aria-label={areAllFolded ? t('Expand all') : t('Collapse all')}
              />
            </Tooltip>
            )}
            {labelsQuery.isLoading ? <Spinner /> : (
              canManageLabels && (
                <Tooltip content={t('Create a Label')} placement="bottom">
                  <Button
                    icon={<Icon type={IconType.FILLED} name="add" />}
                    color="primary"
                    size="small"
                    onClick={open}
                    className="mailbox-labels__create-button"
                    aria-label={t('Create a Label')}
                  />
                </Tooltip>
              )
            )}
          </div>
        </header>
        <div className="label-list">
          <div>
            {
              labelsQuery.data?.data.map((label) => (
                <LabelItem key={label.id} {...label} onEdit={editLabel} canManage={canManageLabels} defaultFoldState={defaultFoldState} />
              ))
            }
            {isDragOver && (
              <div className="mailbox-labels__drag-over-indicator" />
            )}
          </div>
        </div>
        <LabelModal isOpen={isOpen} onClose={handleClose} label={labelToEdit} />
      </section>
  )
}

/**
 * Just a wrapper to provide the FoldProvider to the MailboxLabelsBase component.
 */
export const MailboxLabels = (props: MailboxLabelsProps) => {
  return (
    <FoldProvider>
      <MailboxLabelsBase {...props} />
    </FoldProvider>
  )
}
