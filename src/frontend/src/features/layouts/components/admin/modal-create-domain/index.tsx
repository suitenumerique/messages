import { Button, Modal, ModalSize } from '@openfun/cunningham-react';
import React, { useState } from 'react'
import { useTranslation } from 'react-i18next';
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { FieldErrors, FormProvider, useForm } from 'react-hook-form';
import { MailDomainAdminWrite, useMaildomainsCreate } from '@/features/api/gen';
import { Banner } from '@/features/ui/components/banner';
import { RhfInput } from '@/features/forms/components/react-hook-form';
import { RhfCheckbox } from '@/features/forms/components/react-hook-form/rhf-checkbox';
import { useConfig } from '@/features/providers/config';
import { convertJsonSchemaToZod } from '@/features/forms/components/zod-json-schema-serializer';
import { JSONSchema } from 'zod/v4/core';
import { ItemJsonSchema } from '@/features/forms/components/zod-json-schema-serializer';
import { RhfJsonSchemaField } from '@/features/forms/components/react-hook-form/rhf-json-schema-field';

type ModalCreateDomainProps = {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (createdDomain: MailDomainAdminWrite) => void;
}

export const ModalCreateDomain = ({ isOpen, onClose, onCreate }: ModalCreateDomainProps) => {

  const { t } = useTranslation();
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN } = useConfig();
  const { mutateAsync: createDomain } = useMaildomainsCreate();

  const createDomainSchema = z.object({
      name: z.string()
      .min(1, { error: "create_domain_modal.form.errors.name_required" })
      .regex(/^[a-z0-9][a-z0-9.-]*[a-z0-9]$/, { message: "create_domain_modal.form.errors.name_invalid" }),
      oidc_autojoin: z.boolean(),
      identity_sync: z.boolean(),
      ...convertJsonSchemaToZod(SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN as JSONSchema.Schema)
  })

  type CreateDomainFormData = z.infer<typeof createDomainSchema>;

  const customAttributes = SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN?.properties ?? {};
  const form = useForm<CreateDomainFormData>({
    resolver: zodResolver(createDomainSchema),
    defaultValues: {
      name: '',
      oidc_autojoin: false,
      identity_sync: false,
      ...Object.fromEntries(Object.entries(customAttributes).map(([name, schema]) => ([name, schema.default ?? '']))),
    },
  });

  const { handleSubmit } = form;


  const handleClose = () => {
    form.reset();
    setError(null);
    onClose();
  };

  const getFieldError = (fieldName: keyof CreateDomainFormData) => {
      const errors = form.formState.errors as FieldErrors<CreateDomainFormData>;
      const error = errors?.[fieldName as keyof typeof errors];
      return error?.message ? t(error.message as string) : undefined;
  }

  const onSubmit = async (data: CreateDomainFormData) => {
    setError(null);
    setIsSubmitting(true);
    try {
      const customAttributeKeys = Object.keys(SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN?.properties ?? {});
      const response = await createDomain({data: {
        name: data.name,
        oidc_autojoin: data.oidc_autojoin,
        identity_sync: data.identity_sync,
        custom_attributes: Object.fromEntries(
          Object.entries(data).filter(([key]) => customAttributeKeys.includes(key))
        )
      }});
      onCreate(response.data);
      handleClose();

    } catch {
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
              <RhfInput
                name="name"
                label={t('create_domain_modal.form.labels.name')}
                text={getFieldError('name')}
                fullWidth
              />
            </div>
            {
              Object.entries(SCHEMA_CUSTOM_ATTRIBUTES_MAILDOMAIN?.properties ?? {}).map(([name, schema]: [string, ItemJsonSchema]) => (
                <div className="form-field-row" key={`json-schema-field-${name}`}>
                  <RhfJsonSchemaField
                    schema={schema}
                    text={getFieldError(name as keyof CreateDomainFormData)}
                    name={name}
                    fullWidth
                  />
                </div>
              ))
            }
            <div className="form-field-row">
              <RhfCheckbox
                name="oidc_autojoin"
                label={t('create_domain_modal.form.labels.oidc_autojoin')}
                type="checkbox"
              />
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

