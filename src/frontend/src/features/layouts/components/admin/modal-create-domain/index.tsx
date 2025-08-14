import { Button, Modal, ModalSize } from '@openfun/cunningham-react';
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next';
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { FormProvider, useForm } from 'react-hook-form';
import { MailDomainAdminWriteRequest, useMaildomainsCreate } from '@/features/api/gen';
import { Banner } from '@/features/ui/components/banner';
import { RhfInput } from '@/features/forms/components/react-hook-form';
import { RhfCheckbox } from '@/features/forms/components/react-hook-form/rhf-checkbox';


type ModalCreateAddressProps = {
  isOpen: boolean;
  onClose: () => void;
  onCreate: () => void;
}

export const ModalCreateDomain = ({ isOpen, onClose, onCreate }: ModalCreateAddressProps) => {

  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const { mutateAsync: createDomain } = useMaildomainsCreate();

  const createDomainSchema = z.object({
      name: z.string().min(1, { error: "create_domain_modal.form.errors.name_required" }),
      oidc_autojoin: z.boolean(),
      identity_sync: z.boolean(),
  })

  type CreateDomainFormData = z.infer<typeof createDomainSchema>;


  const form = useForm<CreateDomainFormData>({
    resolver: zodResolver(createDomainSchema),
    defaultValues: {
      name: '',
      oidc_autojoin: false,
      identity_sync: false,
    },
  });

  const { handleSubmit } = form;


  const handleClose = () => {
    form.reset();
    setError(null);
    onClose();
  };

  const onSubmit = async (data: CreateDomainFormData) => {
    setError(null);
    setIsSubmitting(true);
    try {
      const payload: MailDomainAdminWriteRequest = data;
      await createDomain({data: payload});
      onCreate();
      handleClose();

    } catch (_) {
      setError("create_domain_modal.api_errors.default");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
          isOpen={isOpen}
          title={t('create_domain_modal.title')}
          size={ModalSize.LARGE}
          onClose={handleClose}
        >
      <div className="modal-create-domain">
        <FormProvider {...form}>
          <form onSubmit={handleSubmit(onSubmit)} noValidate>
            {error && (
              <Banner type="error">
                {t(error)}
              </Banner>
            )}
            <div className="form-field-row">
              <RhfInput name="name" label={t('create_domain_modal.form.labels.name')} required/>
            </div>
            <div className="form-field-row">
              <RhfCheckbox name="oidc_autojoin" label={t('create_domain_modal.form.labels.oidc_autojoin')} type="checkbox"/>
            </div>
            <div className="form-field-row">
              <RhfCheckbox
                name="identity_sync"
                label={t('create_domain_modal.form.labels.identity_sync')}
                type="checkbox"
              />
            </div>
            <div className="form-actions">
              <Button
                type="submit"
                disabled={isSubmitting}
                fullWidth
              >
                {isSubmitting ? t('actions.creating') : t('actions.create')}
              </Button>
            </div>
          </form>
        </FormProvider>
      </div>
    </Modal>
  )
}

