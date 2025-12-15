import { Button, Input, Modal, ModalSize } from '@gouvfr-lasuite/cunningham-react';
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next';
import { useForm } from 'react-hook-form';
import { MailDomainAdmin } from '@/features/api/gen';
import { Banner } from '@/features/ui/components/banner';
import { useConfig } from '@/features/providers/config';
import { fetchAPI } from '@/features/api/fetch-api';
import { useQueryClient } from '@tanstack/react-query';
import { addToast, ToasterItem } from '@/features/ui/components/toaster';
import { Icon } from '@gouvfr-lasuite/ui-kit';
import { APIError, errorToString } from '@/features/api/api-error';

type ModalUpdateDomainProps = {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  domain: MailDomainAdmin;
}

type UpdateDomainFormData = {
  max_recipients_per_message: string;
};

export const ModalUpdateDomain = ({ isOpen, onClose, onSuccess, domain }: ModalUpdateDomainProps) => {

  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const queryClient = useQueryClient();

  const { MAX_RECIPIENTS_PER_MESSAGE } = useConfig();

  const form = useForm<UpdateDomainFormData>({
    defaultValues: {
      max_recipients_per_message: domain.custom_settings?.max_recipients_per_message?.toString() ?? '',
    },
  });

  const { handleSubmit, register, formState: { errors }, watch } = form;
  const maxRecipientsValue = watch('max_recipients_per_message');

  const handleClose = () => {
    form.reset();
    setError(null);
    onClose();
  };

  const onSubmit = async (data: UpdateDomainFormData) => {
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

    setIsSubmitting(true);
    try {
      const payload = {
        custom_settings: {
          max_recipients_per_message: value === '' ? null : Number(value),
        },
      };

      await fetchAPI(`/api/v1.0/maildomains/${domain.id}/`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
      });

      // Invalidate queries - use predicate to match all maildomains queries
      await queryClient.invalidateQueries({
        predicate: (query) => {
          const key = query.queryKey[0];
          return typeof key === 'string' && key.includes('/api/v1.0/maildomains');
        }
      });

      onSuccess();
      addToast(
        <ToasterItem type="info">
          <Icon name="check" />
          <span>{t('The domain settings have been updated!')}</span>
        </ToasterItem>, {
          toastId: "toast_edit_domain_modal_success",
        }
      );
      handleClose();

    } catch (err: unknown) {
      if (err instanceof APIError && err.data?.custom_settings) {
        setError(err.data.custom_settings[0]);
      } else {
        setError(errorToString(err));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const helperText = errors.max_recipients_per_message?.message
    ? errors.max_recipients_per_message.message
    : t('Leave empty to use the global default. Maximum: {{value}}', { value: MAX_RECIPIENTS_PER_MESSAGE });

  return (
    <Modal
          isOpen={isOpen}
          title={t('Domain settings - {{domain}}', { domain: domain.name })}
          size={ModalSize.MEDIUM}
          onClose={handleClose}
    >
      <div className="modal-maildomain-manage-settings">
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
