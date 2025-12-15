import { Button, Input, Modal, ModalSize } from '@gouvfr-lasuite/cunningham-react';
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next';
import { useForm } from 'react-hook-form';
import { MailboxAdmin, useMaildomainsMailboxesSettingsUpdate } from '@/features/api/gen';
import { Banner } from '@/features/ui/components/banner';
import { useConfig } from '@/features/providers/config';
import { addToast, ToasterItem } from '@/features/ui/components/toaster';
import { Icon } from '@gouvfr-lasuite/ui-kit';
import MailboxHelper from '@/features/utils/mailbox-helper';
import { APIError, errorToString } from '@/features/api/api-error';

type ModalMailboxManageSettingsProps = {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  mailbox: MailboxAdmin;
  domainId: string;
}

type UpdateMailboxFormData = {
  max_recipients_per_message: string;
};

export const ModalMailboxManageSettings = ({ isOpen, onClose, onSuccess, mailbox, domainId }: ModalMailboxManageSettingsProps) => {

  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);

  const { MAX_RECIPIENTS_PER_MESSAGE } = useConfig();

  const { mutateAsync: updateMailboxSettings, isPending: isSubmitting } = useMaildomainsMailboxesSettingsUpdate();

  const form = useForm<UpdateMailboxFormData>({
    defaultValues: {
      max_recipients_per_message: mailbox.custom_settings?.max_recipients_per_message?.toString() ?? '',
    },
  });

  const { handleSubmit, register, formState: { errors }, watch } = form;
  const maxRecipientsValue = watch('max_recipients_per_message');

  const handleClose = () => {
    form.reset();
    setError(null);
    onClose();
  };

  const onSubmit = async (data: UpdateMailboxFormData) => {
    setError(null);

    // Validate
    const value = data.max_recipients_per_message.trim();
    if (value !== '' && (isNaN(Number(value)) || Number(value) < 1 || !Number.isInteger(Number(value)))) {
      form.setError('max_recipients_per_message', { message: t('Please enter a valid positive integer or leave empty.') });
      return;
    }

    // Validate that the value does not exceed the global maximum
    if (value !== '' && Number(value) > MAX_RECIPIENTS_PER_MESSAGE) {
      form.setError('max_recipients_per_message', {
        message: t('The limit cannot exceed the global maximum of {{max}} recipients.', { max: MAX_RECIPIENTS_PER_MESSAGE })
      });
      return;
    }

    try {
      // Use the dedicated /settings/ endpoint for custom_settings
      await updateMailboxSettings({
        maildomainPk: domainId,
        id: mailbox.id,
        data: {
          custom_settings: {
            max_recipients_per_message: value === '' ? null : Number(value),
          },
        },
      });

      onSuccess();
      addToast(
        <ToasterItem type="info">
          <Icon name="check" />
          <span>{t('The mailbox settings have been updated!')}</span>
        </ToasterItem>, {
          toastId: "toast_edit_mailbox_settings_success",
        }
      );
      handleClose();

    } catch (err: unknown) {
      if (err instanceof APIError && err.data?.custom_settings) {
        setError(err.data.custom_settings[0]);
      } else {
        setError(errorToString(err));
      }
    }
  };

  const mailboxEmail = MailboxHelper.toString(mailbox);

  const helperText = errors.max_recipients_per_message?.message
    ? errors.max_recipients_per_message.message
    : t('Leave empty to use the domain or global default. Maximum: {{value}}', { value: MAX_RECIPIENTS_PER_MESSAGE });

  return (
    <Modal
          isOpen={isOpen}
          title={t('Mailbox settings - {{email}}', { email: mailboxEmail })}
          size={ModalSize.MEDIUM}
          onClose={handleClose}
    >
      <div className="modal-mailbox-manage-settings">
        <form onSubmit={handleSubmit(onSubmit)} noValidate>
          {error && (
            <Banner type="error">
              {t(error)}
            </Banner>
          )}

          <div className="form-field-row">
            <Input
              {...register('max_recipients_per_message')}
              label={t('Maximum recipients per message')}
              type="number"
              min={1}
              max={MAX_RECIPIENTS_PER_MESSAGE}
              fullWidth
              state={errors.max_recipients_per_message ? 'error' : 'default'}
              text={helperText}
              value={maxRecipientsValue}
            />
          </div>

          <div className="form-actions">
            <Button
              type="submit"
              disabled={isSubmitting}
              fullWidth
            >
              {isSubmitting ? t('Saving...') : t('Save')}
            </Button>
          </div>
        </form>
      </div>
    </Modal>
  )
}
