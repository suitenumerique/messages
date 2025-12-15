import { Button, Input, Modal, ModalSize, Select } from '@gouvfr-lasuite/cunningham-react';
import React, { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useQueryClient } from '@tanstack/react-query';
import { MailboxAdmin, useMaildomainsMailboxesSettingsUpdate } from '@/features/api/gen';
import { Banner } from '@/features/ui/components/banner';
import { useConfig } from '@/features/providers/config';
import { addToast, ToasterItem } from '@/features/ui/components/toaster';
import { Icon } from '@gouvfr-lasuite/ui-kit';
import MailboxHelper from '@/features/utils/mailbox-helper';
import { APIError, errorToString } from '@/features/api/api-error';
import QuotaHelper, { PeriodType } from '@/features/utils/quota-helper';

type ModalMailboxManageSettingsProps = {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: () => void;
  mailbox: MailboxAdmin;
  domainId: string;
}

/**
 * Create validation schema with dynamic max values
 */
const createValidationSchema = (
  maxPerMessage: number,
  globalMaxRecipients: { limit: number; period: string } | null,
  t: (key: string, options?: Record<string, unknown>) => string
) => {
  const positiveIntegerOrEmpty = (fieldName: string, maxValue?: number) =>
    z.string().refine(
      (val) => {
        if (val.trim() === '') return true;
        const num = Number(val);
        return !isNaN(num) && Number.isInteger(num) && num >= 1;
      },
      { message: t('Please enter a valid positive integer or leave empty.') }
    ).refine(
      (val) => {
        if (val.trim() === '' || maxValue === undefined) return true;
        return Number(val) <= maxValue;
      },
      { message: t('The limit cannot exceed the global maximum of {{max}} recipients.', { max: maxValue }) }
    );

  return z.object({
    max_recipients_per_message: positiveIntegerOrEmpty('max_recipients_per_message', maxPerMessage),
    max_recipients_limit: z.string().refine(
      (val) => {
        if (val.trim() === '') return true;
        const num = Number(val);
        return !isNaN(num) && Number.isInteger(num) && num >= 1;
      },
      { message: t('Please enter a valid positive integer or leave empty.') }
    ),
    max_recipients_period: z.enum(['d', 'm', 'y']),
  }).refine(
    (data) => {
      // Cross-field validation: check quota limit against global max when same period
      if (data.max_recipients_limit.trim() === '') return true;
      if (!globalMaxRecipients) return true;
      if (data.max_recipients_period !== globalMaxRecipients.period) return true;
      return Number(data.max_recipients_limit) <= globalMaxRecipients.limit;
    },
    {
      message: t('The limit cannot exceed the global maximum of {{max}}.', { max: globalMaxRecipients?.limit }),
      path: ['max_recipients_limit'],
    }
  );
};

type UpdateMailboxFormData = z.infer<ReturnType<typeof createValidationSchema>>;

export const ModalMailboxManageSettings = ({ isOpen, onClose, onSuccess, mailbox, domainId }: ModalMailboxManageSettingsProps) => {

  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { MAX_RECIPIENTS_PER_MESSAGE, MAX_RECIPIENTS_FOR_MAILBOX } = useConfig();
  const globalMaxRecipients = QuotaHelper.parseGlobalMaxRecipients(MAX_RECIPIENTS_FOR_MAILBOX);

  const { mutateAsync: updateMailboxSettings, isPending: isSubmitting } = useMaildomainsMailboxesSettingsUpdate();

  const parsedMaxRecipients = QuotaHelper.parseMaxRecipients(mailbox.custom_settings?.max_recipients);

  const validationSchema = useMemo(
    () => createValidationSchema(MAX_RECIPIENTS_PER_MESSAGE, globalMaxRecipients, t),
    [MAX_RECIPIENTS_PER_MESSAGE, globalMaxRecipients, t]
  );

  const form = useForm<UpdateMailboxFormData>({
    resolver: zodResolver(validationSchema),
    defaultValues: {
      max_recipients_per_message: mailbox.custom_settings?.max_recipients_per_message?.toString() ?? '',
      max_recipients_limit: parsedMaxRecipients.limit,
      max_recipients_period: parsedMaxRecipients.period,
    },
  });

  const { handleSubmit, register, formState: { errors }, watch, setValue } = form;
  const maxRecipientsValue = watch('max_recipients_per_message');
  const maxRecipientsLimitValue = watch('max_recipients_limit');
  const maxRecipientsPeriodValue = watch('max_recipients_period');

  const handleClose = () => {
    form.reset();
    setError(null);
    onClose();
  };

  const onSubmit = async (data: UpdateMailboxFormData) => {
    setError(null);

    const perMessageValue = data.max_recipients_per_message.trim();
    const limitValue = data.max_recipients_limit.trim();
    const periodValue = data.max_recipients_period;

    try {
      // Build max_recipients string (e.g., "500/d") or null
      const maxRecipients = limitValue === '' ? null : `${limitValue}/${periodValue}`;

      // Use the dedicated /settings/ endpoint for custom_settings
      await updateMailboxSettings({
        maildomainPk: domainId,
        id: mailbox.id,
        data: {
          custom_settings: {
            max_recipients_per_message: perMessageValue === '' ? null : Number(perMessageValue),
            max_recipients: maxRecipients,
          },
        },
      });

      // Invalidate quota queries for this mailbox
      await queryClient.invalidateQueries({
        predicate: (query) => {
          const key = query.queryKey[0];
          return key === 'mailbox-quota' || key === 'domain-quota';
        },
        refetchType: 'all',
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

  const helperTextPerMessage = errors.max_recipients_per_message?.message
    ? errors.max_recipients_per_message.message
    : t('Leave empty to use the domain or global default. Maximum: {{value}}', { value: MAX_RECIPIENTS_PER_MESSAGE });

  const helperTextQuota = errors.max_recipients_limit?.message
    ? errors.max_recipients_limit.message
    : t('Leave empty to use the domain or global default. Maximum: {{value}}', { value: MAX_RECIPIENTS_FOR_MAILBOX });

  const periodOptions = QuotaHelper.PERIOD_OPTIONS.map(opt => ({
    ...opt,
    label: t(opt.label),
  }));

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
              text={helperTextPerMessage}
              value={maxRecipientsValue}
            />
          </div>

          <div className="form-field-row">
            <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
              <div style={{ flex: 1 }}>
                <Input
                  {...register('max_recipients_limit')}
                  label={t('Maximum recipients per period')}
                  type="number"
                  min={1}
                  fullWidth
                  state={errors.max_recipients_limit ? 'error' : 'default'}
                  text={helperTextQuota}
                  value={maxRecipientsLimitValue}
                />
              </div>
              <div style={{ width: '120px' }}>
                <Select
                  label={t('Period')}
                  options={periodOptions}
                  value={maxRecipientsPeriodValue}
                  onChange={(e) => setValue('max_recipients_period', e.target.value as PeriodType)}
                  fullWidth
                />
              </div>
            </div>
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
