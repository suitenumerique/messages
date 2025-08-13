import { Mailbox, TreeLabel, useLabelsList } from "@/features/api/gen";
import { Icon, IconSize, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, useModal, Tooltip } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";
import { LabelModal, SubLabelCreation } from "./components/label-form-modal";
import { LabelItem } from "./components/label-item";
import { useEffect, useState } from "react";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { FoldProvider, useFold } from "@/features/providers/fold";

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

  return (
      <section className="mailbox-labels">
        <header className="mailbox-labels__header">
          <p className="mailbox-labels__title">{t('labels.title')}</p>
          <div className="mailbox-labels__actions">
            {areAllFolded !== undefined && (
            <Tooltip content={areAllFolded ? t('labels.expand_all') : t('labels.collapse_all')} placement="bottom">
              <Button
                icon={<Icon type={IconType.FILLED} name={areAllFolded ? "unfold_more" : "unfold_less"} size={IconSize.LARGE} />}
                color="tertiary-text"
                size="small"
                onClick={toggleFolding}
                className="mailbox-labels__fold-button"
                aria-label={areAllFolded ? t('labels.expand_all') : t('labels.collapse_all')}
              />
            </Tooltip>
            )}
            {labelsQuery.isLoading ? <Spinner /> : (
              canManageLabels && (
                <Tooltip content={t('labels.create')} placement="bottom">
                  <Button
                    icon={<Icon type={IconType.FILLED} name="add" />}
                    color="primary"
                    size="small"
                    onClick={open}
                    className="mailbox-labels__create-button"
                    aria-label={t('labels.create')}
                  />
                </Tooltip>
              )
            )}
          </div>
        </header>
        <div className="label-list">
          {
            labelsQuery.data?.data.map((label) => (
              <LabelItem key={label.id} {...label} onEdit={editLabel} canManage={canManageLabels} defaultFoldState={defaultFoldState} />
            ))
          }
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
