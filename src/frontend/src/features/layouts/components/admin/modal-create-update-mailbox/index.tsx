import { ModalSize, Button, Modal } from "@openfun/cunningham-react";
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { FieldErrors, FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { useRouter } from "next/router";
import { useMaildomainsMailboxesCreate, useMaildomainsMailboxesList, useMaildomainsMailboxesPartialUpdate } from "@/features/api/gen/maildomains/maildomains";
import { RhfInput } from "@/features/forms/components/react-hook-form";
import { RhfCheckbox } from "@/features/forms/components/react-hook-form/rhf-checkbox";
import { Banner } from "@/features/ui/components/banner";
import { APIError } from "@/features/api/api-error";
import { MailboxAdmin, MailboxAdminCreate, MailboxAdminCreatePayloadRequest, MailboxAdminUpdateMetadataRequest } from "@/features/api/gen";
import { MailboxCreationSuccess } from "./mailbox-creation-success";
import { useAdminMailDomain } from "@/features/providers/admin-maildomain";
import clsx from "clsx";
import { RhfJsonSchemaField } from "@/features/forms/components/react-hook-form/rhf-json-schema-field";
import { convertJsonSchemaToZod, ItemJsonSchema } from "@/features/forms/components/zod-json-schema-serializer";
import { useConfig } from "@/features/providers/config";
import { JSONSchema } from "zod/v4/core";
import MailboxHelper from "@/features/utils/mailbox-helper";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import i18n from "@/features/i18n/initI18n";

export const MODAL_CREATE_ADDRESS_ID = "modal-create-address";

type MailboxType = "personal" | "shared" | "redirect";
type MailboxTypeErrors<FormData, Type extends MailboxType> = FieldErrors<Extract<FormData, { type: Type }>>

// Slugify function to transform text into URL-friendly format
const slugify = (text: string): string => {
  return text
    .toLowerCase()
    .normalize('NFD') // Decompose accented characters
    .replace(/[\u0300-\u036f]/g, '') // Remove accent marks
    .replace(/[^a-z0-9]/g, '-') // Replace non-alphanumeric with hyphens
    .replace(/-+/g, '-') // Replace multiple hyphens with single
    .replace(/^-+|-+$/g, ''); // Remove leading/trailing hyphens
};
type ModalCreateOrUpdateMailboxProps = {
  isOpen: boolean;
  mailbox?: MailboxAdmin;
  onClose: () => void;
  onSuccess?: () => void;
}

export const ModalCreateOrUpdateMailbox = ({ isOpen, mailbox, onClose, onSuccess }: ModalCreateOrUpdateMailboxProps) => {
  const { t } = useTranslation();
  const router = useRouter();
  const domainId = router.query.maildomainId as string;
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<MailboxType>(() => {
    if (!mailbox || mailbox.is_identity) return "personal";
    if (mailbox.alias_of) return "redirect";
    return "shared";
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [prefixManuallyChanged, setPrefixManuallyChanged] = useState(false);
  const [prefixHasFocus, setPrefixHasFocus] = useState(false);
  const [firstFieldRef, setFirstFieldRef] = useState<HTMLInputElement | null>(null);
  const isUpdating = Boolean(mailbox);

  // Get existing mailboxes and domain info
  const { data: mailboxesData } = useMaildomainsMailboxesList(domainId);
  const mailboxes = mailboxesData?.data.results || [];
  const { selectedMailDomain } = useAdminMailDomain();
  const domainName = selectedMailDomain?.name || "";
  const { mutateAsync: createMailbox } = useMaildomainsMailboxesCreate();
  const { mutateAsync: updateMailbox } = useMaildomainsMailboxesPartialUpdate();
  const [createdMailbox, setCreatedMailbox] = useState<MailboxAdminCreate | null>(null);
  const { SCHEMA_CUSTOM_ATTRIBUTES_USER } = useConfig();

  const createMailboxSchema = z.discriminatedUnion("type", [
    z.object({
      type: z.literal("personal"),
      first_name: z.string().min(1, { error: i18n.t("First name is required.") }),
      last_name: z.string().min(1, { error: i18n.t("Last name is required.") }),
      prefix: z.string()
        .min(1, { error: i18n.t("Prefix is required.") })
        .regex(/^[a-zA-Z0-9_.-]+$/, { error: i18n.t("Prefix can only contain letters, numbers, dots, underscores and hyphens.") }),
      confirmation_accepted: z.boolean().refine(val => val === true, { error: i18n.t("You must confirm this statement.") }),
      ...convertJsonSchemaToZod(SCHEMA_CUSTOM_ATTRIBUTES_USER as JSONSchema.Schema),
    }),
    z.object({
      type: z.literal("shared"),
      name: z.string().min(1, { error: i18n.t("Name is required.") }),
      prefix: z.string()
        .min(1, { error: i18n.t("Prefix is required.") })
        .regex(/^[a-zA-Z0-9_.-]+$/, { error: i18n.t("Prefix can only contain letters, numbers, dots, underscores and hyphens.") }),
    }),
    z.object({
      type: z.literal("redirect"),
      prefix: z.string()
        .min(1, { error: i18n.t("Prefix is required.") })
        .regex(/^[a-zA-Z0-9_.-]+$/, { error: i18n.t("Prefix can only contain letters, numbers, dots, underscores and hyphens.") }),
      target_email: z.email({ error: i18n.t("Please enter a valid email address.") }),
    }),
  ]);
  const editMailboxSchema = z.discriminatedUnion("type", [
    z.object({
      type: z.literal("personal"),
      full_name: z.string().min(1, { error: i18n.t("Full name is required.") }),
      ...convertJsonSchemaToZod(SCHEMA_CUSTOM_ATTRIBUTES_USER as JSONSchema.Schema),
    }),
    z.object({
      type: z.literal("shared"),
      name: z.string().min(1, { error: i18n.t("Name is required.") }),
    }),
  ]);
  type CreateMailboxFormData = z.infer<typeof createMailboxSchema>;
  type EditMailboxFormData = z.infer<typeof editMailboxSchema>;

  const getDefaultValues = (type: MailboxType): CreateMailboxFormData | EditMailboxFormData => {
    if (type === "personal") {
      const customAttributes = SCHEMA_CUSTOM_ATTRIBUTES_USER?.properties ?? {};
      if (isUpdating) {
        const owner_access = mailbox?.accesses.find((access) => access.user.email === MailboxHelper.toString(mailbox));
        return {
          type: "personal",
          prefix: mailbox?.local_part ?? "",
          confirmation_accepted: true,
          full_name: owner_access?.user.full_name ?? "",
          ...Object.fromEntries(Object.entries(customAttributes).map(
            ([name, schema]) => ([name, owner_access?.user.custom_attributes[name] ?? schema.default ?? ''])
          )),
        };
      }
      return {
        type: "personal",
        first_name: "",
        last_name: "",
        prefix: "",
        confirmation_accepted: false,
        ...Object.fromEntries(Object.entries(customAttributes).map(([name, schema]) => ([name, schema.default ?? '']))),
      };
    } else if (type === "shared") {
      return {
        type: "shared",
        name: mailbox?.contact.name ?? "",
        prefix: mailbox?.local_part ?? "",
      };
    } else {
      return {
        type: "redirect",
        prefix: mailbox?.local_part ?? "",
        target_email: "",
      };
    }
  }

  const form = useForm<CreateMailboxFormData | EditMailboxFormData>({
    resolver: zodResolver(isUpdating ? editMailboxSchema : createMailboxSchema),
    defaultValues: getDefaultValues(activeTab),
  });

  const { handleSubmit, reset, setValue, watch } = form;

  // Watch form values for auto-sync
  const watchedValues = watch();

  // Auto-sync prefix based on name fields
  useEffect(() => {
    if (isUpdating || prefixManuallyChanged || prefixHasFocus) return;

    if (activeTab === "personal" && watchedValues.type === "personal") {
      const personalData = watchedValues as Extract<CreateMailboxFormData, { type: "personal" }>;
      const firstName = personalData.first_name?.trim();
      const lastName = personalData.last_name?.trim();

      if (firstName || lastName) {
        let autoPrefix = '';
        if (firstName && lastName) {
          autoPrefix = `${slugify(firstName)}.${slugify(lastName)}`;
        } else if (firstName) {
          autoPrefix = slugify(firstName);
        } else if (lastName) {
          autoPrefix = slugify(lastName);
        }

        if (autoPrefix && autoPrefix !== (watchedValues as CreateMailboxFormData).prefix) {
          setValue("prefix", autoPrefix, { shouldValidate: false });
        }
      }
    } else if (activeTab === "shared" && watchedValues.type === "shared") {
      const sharedData = watchedValues as Extract<CreateMailboxFormData, { type: "shared" }>;
      const name = sharedData.name?.trim();
      if (name) {
        const autoPrefix = slugify(name);
        if (autoPrefix !== (watchedValues as CreateMailboxFormData).prefix) {
          setValue("prefix", autoPrefix, { shouldValidate: false });
        }
      }
    }
  }, [watchedValues, activeTab, prefixManuallyChanged, isUpdating, setValue]);

  // Focus first field when tab changes
  // TODO: might be better to use form.setFocus if we can make it work
  useEffect(() => {
    if (firstFieldRef && isOpen) {
      // Small delay to ensure the field is rendered
      setTimeout(() => {
        firstFieldRef.focus();
      }, 100);
    }
  }, [activeTab, isOpen, firstFieldRef]);

  // Reset form when switching tabs
  const handleTabChange = (tab: MailboxType) => {
    setActiveTab(tab);
    setPrefixManuallyChanged(false);
    const defaultValues = getDefaultValues(tab);
    reset(defaultValues);
  };

  const handleCreate = async (data: CreateMailboxFormData) => {
    try {
      const customAttributeKeys = Object.keys(SCHEMA_CUSTOM_ATTRIBUTES_USER?.properties ?? {});
      const payload: MailboxAdminCreatePayloadRequest = {
        local_part: data.prefix,
        metadata: {
          type: data.type,
          custom_attributes: {
            ...Object.fromEntries(Object.entries(data).filter(([key]) => customAttributeKeys.includes(key)).map(([key, value]) => [key, value])),
          },
        },
      };

      // Add type-specific data
      if (data.type === "personal") {
        payload.metadata.first_name = data.first_name;
        payload.metadata.last_name = data.last_name;
      } else if (data.type === "shared") {
        payload.metadata.name = data.name;
      } else if (data.type === "redirect") {
        // Find target mailbox for alias creation
        const targetMailbox = mailboxes.find(mb =>
          MailboxHelper.toString(mb) === data.target_email
        );
        payload.alias_of = targetMailbox?.id;
      }
      const response = await createMailbox({ maildomainPk: domainId, data: payload });
      setCreatedMailbox(response.data);
      onSuccess?.();
    } catch (error: unknown) {
      if (error instanceof APIError && error.data.local_part) {
        setError(t('An address with this prefix already exists in this domain.'));
      } else {
        setError(t('An error occurred while creating the address.'));
      }
    }
  }

  const handleUpdate = async (data: EditMailboxFormData) => {
    const customAttributeKeys = Object.keys(SCHEMA_CUSTOM_ATTRIBUTES_USER?.properties ?? {});
    const metadata: MailboxAdminUpdateMetadataRequest = {
      custom_attributes: {
        ...Object.fromEntries(Object.entries(data).filter(
          ([key]) => customAttributeKeys.includes(key)).map(([key, value]) => [key, value]
          )),
      },
    };

    if (data.type === "personal") metadata.full_name = data.full_name;
    else if (data.type === "shared") metadata.name = data.name;

    try {
      await updateMailbox({
        maildomainPk: domainId,
        id: mailbox!.id,
        data: { metadata },
      });
      onSuccess?.();
      addToast(
        <ToasterItem type="info">
          <Icon name="check" />
          <span>{t('The address has been updated!')}</span>
        </ToasterItem>, {
          toastId: "toast_edit_mailbox_modal_success",
        }
      )
      handleClose();
    } catch {
      setError(t('An error occurred while updating the address.'));
    }
  }

  const onSubmit = async (data: CreateMailboxFormData | EditMailboxFormData) => {
    setError(null);
    setIsSubmitting(true);

    if (isUpdating) {
      await handleUpdate(data as EditMailboxFormData);
    } else {
      await handleCreate(data as CreateMailboxFormData);
    }

    setIsSubmitting(false);
  };

  const handleClose = () => {
    setActiveTab("personal");
    reset(getDefaultValues("personal"));
    setError(null);
    setCreatedMailbox(null);
    onClose();
  };

  const getFieldError = <
    Type extends MailboxType,
    FormData extends CreateMailboxFormData | EditMailboxFormData = CreateMailboxFormData | EditMailboxFormData,
    Errors extends MailboxTypeErrors<FormData, Type> = MailboxTypeErrors<FormData, Type>>(fieldName: keyof Errors) => {
    const errors = form.formState.errors as Errors;
    const error = errors?.[fieldName];
    return error?.message ? error.message : undefined;
  }

  return (
    <Modal
      isOpen={isOpen}
      title={
        isUpdating ? t('Edit {{mailbox}} address', { mailbox: MailboxHelper.toString(mailbox!) }) :
          t('Create a new address @{{domain}}', { domain: domainName })
      }
      size={ModalSize.LARGE}
      onClose={handleClose}
    >
      {createdMailbox ? (
        <MailboxCreationSuccess type={activeTab} mailbox={createdMailbox} onClose={handleClose} />
      ) : (
        <div className="modal-create-address">
          {/* Tab Navigation */}
          <div className="modal-tabs">
            <button
              type="button"
              className={clsx('modal-tab', { 'modal-tab--active': activeTab === "personal" })}
              onClick={() => handleTabChange("personal")}
              disabled={isUpdating}
            >
              {isUpdating ? t('Personal mailbox') : t('Create a new personal mailbox')}
            </button>
            <button
              type="button"
              className={clsx('modal-tab', { 'modal-tab--active': activeTab === "shared" })}
              onClick={() => handleTabChange("shared")}
              disabled={isUpdating}
            >
              {isUpdating ? t('Shared mailbox') : t('Create a new shared mailbox')}
            </button>
            <button
              disabled={isUpdating || true}
              type="button"
              className={clsx('modal-tab', { 'modal-tab--active': activeTab === "redirect" })}
              onClick={() => handleTabChange("redirect")}
            >
              {isUpdating ? t('Simple redirect (Coming soon)') : t('Create a simple redirect (Coming soon)')}
            </button>
          </div>

          <FormProvider {...form}>
            <form onSubmit={handleSubmit(onSubmit)} noValidate>
              {/* Personal Mailbox Form */}
              {activeTab === "personal" && (
                <>
                  <div className="form-field-row name-row">
                    {isUpdating ? (
                      <RhfInput
                        label={t('Full name')}
                        text={getFieldError<"personal", EditMailboxFormData>('full_name')}
                        name="full_name"
                        className="name-input"
                      />
                    ) : (
                      <>
                        <RhfInput
                          label={t('First name')}
                          text={getFieldError<"personal", CreateMailboxFormData>('first_name')}
                          name="first_name"
                          className="name-input"
                          ref={(el) => {
                            if (activeTab === "personal") {
                              setFirstFieldRef(el);
                            }
                          }}
                        />
                        <RhfInput
                          label={t('Last name')}
                          text={getFieldError<"personal", CreateMailboxFormData>('last_name')}
                          name="last_name"
                          className="name-input"
                        />
                      </>
                    )}
                  </div>

                  <div className="form-field-row address-row">
                    <RhfInput
                      label={t('Address')}
                      text={getFieldError<"personal", CreateMailboxFormData>('prefix')}
                      name="prefix"
                      fullWidth
                      readOnly={isUpdating}
                      disabled={isUpdating}
                      className="address-input"
                      onFocus={() => setPrefixHasFocus(true)}
                      onBlur={() => setPrefixHasFocus(false)}
                      onInput={() => {
                        setPrefixManuallyChanged(true);
                      }}
                    />
                    <span className="domain-suffix">@{domainName}</span>
                  </div>

                  {
                    Object.entries(SCHEMA_CUSTOM_ATTRIBUTES_USER?.properties ?? {}).map(([name, schema]: [string, ItemJsonSchema]) => (
                      <div className="form-field-row" key={`json-schema-field-${name}`}>
                        <RhfJsonSchemaField
                          schema={schema}
                          state={getFieldError<"personal", CreateMailboxFormData>(name as keyof MailboxTypeErrors<CreateMailboxFormData, 'personal'>) ? "error" : "default"}
                          text={getFieldError<"personal", CreateMailboxFormData>(name as keyof MailboxTypeErrors<CreateMailboxFormData, 'personal'>)}
                          name={name}
                          fullWidth
                        />
                      </div>
                    ))
                  }

                  <div className="form-field-row">
                    <RhfCheckbox
                      label={t('I confirm that this address corresponds to the real identity of a colleague, and I commit to deactivating it when their position ends.')}
                      state={getFieldError<"personal", CreateMailboxFormData>('confirmation_accepted') ? "error" : "default"}
                      text={getFieldError<"personal", CreateMailboxFormData>('confirmation_accepted')}
                      name="confirmation_accepted"
                      readOnly={isUpdating}
                      disabled={isUpdating}
                      required
                    />
                  </div>
                </>
              )}

              {/* Shared Mailbox Form */}
              {activeTab === "shared" && (
                <>
                  <div className="form-field-row">
                    <RhfInput
                      label={t('Name')}
                      text={getFieldError<"shared">('name')}
                      name="name"
                      fullWidth
                      ref={(el) => {
                        if (activeTab === "shared") {
                          setFirstFieldRef(el);
                        }
                      }}
                    />
                  </div>

                  <div className="form-field-row address-row">
                    <RhfInput
                      label={t('Address')}
                      text={getFieldError<"shared", CreateMailboxFormData>('prefix')}
                      name="prefix"
                      fullWidth
                      readOnly={isUpdating}
                      disabled={isUpdating}
                      className="address-input"
                      onFocus={() => setPrefixHasFocus(true)}
                      onBlur={() => setPrefixHasFocus(false)}
                      onInput={() => {
                        setPrefixManuallyChanged(true);
                      }}
                    />
                    <span className="domain-suffix">@{domainName}</span>
                  </div>
                </>
              )}

              {/* Redirect/Alias Form */}
              {activeTab === "redirect" && (
                <>
                  <div className="form-field-row address-row">
                    <RhfInput
                      label={t('Address')}
                      name="prefix"
                      text={getFieldError<"redirect">('prefix')}
                      fullWidth
                      className="address-input"
                      onFocus={() => setPrefixHasFocus(true)}
                      onBlur={() => setPrefixHasFocus(false)}
                      onInput={() => {
                        setPrefixManuallyChanged(true);
                      }}
                      ref={(el) => {
                        if (activeTab === "redirect") {
                          setFirstFieldRef(el);
                        }
                      }}
                    />
                    <span className="domain-suffix">@{domainName}</span>
                  </div>

                  <div className="form-field-row">
                    <RhfInput
                      label={t('Target email')}
                      text={getFieldError<"redirect">('target_email')}
                      name="target_email"
                      type="email"
                      fullWidth
                    />
                  </div>
                </>
              )}

              {error && (
                <Banner type="error">
                  {t(error)}
                </Banner>
              )}


              <div className="form-actions">
                <Button
                  type="submit"
                  disabled={isSubmitting}
                  fullWidth
                >
                  {isSubmitting ? t('Saving...') : (isUpdating ? t('Save') : t('Create'))}
                </Button>
              </div>
            </form>
          </FormProvider>
        </div>
      )}
    </Modal>
  );
};
