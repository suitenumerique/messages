import { ModalSize, Button, Modal } from "@openfun/cunningham-react";
import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { FieldErrors, FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import z from "zod";
import { useRouter } from "next/router";
import { useMaildomainsMailboxesCreate, useMaildomainsMailboxesList } from "@/features/api/gen/maildomains/maildomains";
import { RhfInput } from "@/features/forms/components/react-hook-form";
import { RhfCheckbox } from "@/features/forms/components/react-hook-form/rhf-checkbox";
import { Banner } from "@/features/ui/components/banner";
import { APIError } from "@/features/api/api-error";
import { MailboxAdminCreate, MailboxAdminCreatePayloadRequest } from "@/features/api/gen";
import { MailboxCreationSuccess } from "./mailbox-creation-success";
import clsx from "clsx";

export const MODAL_CREATE_ADDRESS_ID = "modal-create-address";

type MailboxType = "personal" | "shared" | "redirect";

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

// Form schema with conditional validation
const createAddressSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("personal"),
    first_name: z.string().min(1, "create_address_modal.form.errors.first_name_required"),
    last_name: z.string().min(1, "create_address_modal.form.errors.last_name_required"),
    prefix: z.string()
      .min(1, "create_address_modal.form.errors.prefix_required")
      .regex(/^[a-zA-Z0-9_.-]+$/, "create_address_modal.form.errors.prefix_invalid"),
    confirmation_accepted: z.boolean().refine(val => val === true, "create_address_modal.form.errors.confirmation_required"),
  }),
  z.object({
    type: z.literal("shared"),
    name: z.string().min(1, "create_address_modal.form.errors.name_required"),
    prefix: z.string()
      .min(1, "create_address_modal.form.errors.prefix_required")
      .regex(/^[a-zA-Z0-9_.-]+$/, "create_address_modal.form.errors.prefix_invalid"),
  }),
  z.object({
    type: z.literal("redirect"),
    prefix: z.string()
      .min(1, "create_address_modal.form.errors.prefix_required")
      .regex(/^[a-zA-Z0-9_.-]+$/, "create_address_modal.form.errors.prefix_invalid"),
    target_email: z.string().email("create_address_modal.form.errors.target_email_invalid"),
  }),
]);

type CreateAddressFormData = z.infer<typeof createAddressSchema>;

type ModalCreateAddressProps = {
  isOpen: boolean;
  onClose: () => void;
}

export const ModalCreateAddress = ({ isOpen, onClose }: ModalCreateAddressProps) => {
  const { t } = useTranslation();
  const router = useRouter();
  const domainId = router.query.maildomainId as string;
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<MailboxType>("personal");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [prefixManuallyChanged, setPrefixManuallyChanged] = useState(false);
  const [prefixHasFocus, setPrefixHasFocus] = useState(false);

  // Get existing mailboxes and domain info
  const { data: mailboxesData, refetch: refetchMailboxes } = useMaildomainsMailboxesList(domainId);
  const mailboxes = mailboxesData?.data.results || [];
  const domainName = mailboxes[0]?.domain_name || "";
  const { mutateAsync: createMailbox } = useMaildomainsMailboxesCreate();
  const [createdMailbox, setCreatedMailbox] = useState<MailboxAdminCreate | null>(null);

  const form = useForm<CreateAddressFormData>({
    resolver: zodResolver(createAddressSchema),
    defaultValues: {
      type: "personal",
      first_name: "",
      last_name: "",
      prefix: "",
      confirmation_accepted: false,
    },
  });

  const { handleSubmit, reset, setValue, watch } = form;

  // Watch form values for auto-sync
  const watchedValues = watch();

  // Auto-sync prefix based on name fields
  useEffect(() => {
    if (prefixManuallyChanged || prefixHasFocus) return;

    if (activeTab === "personal" && watchedValues.type === "personal") {
      const personalData = watchedValues as Extract<CreateAddressFormData, { type: "personal" }>;
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

        if (autoPrefix && autoPrefix !== watchedValues.prefix) {
          setValue("prefix", autoPrefix, { shouldValidate: false });
        }
      }
    } else if (activeTab === "shared" && watchedValues.type === "shared") {
      const sharedData = watchedValues as Extract<CreateAddressFormData, { type: "shared" }>;
      const name = sharedData.name?.trim();
      if (name) {
        const autoPrefix = slugify(name);
        if (autoPrefix !== watchedValues.prefix) {
          setValue("prefix", autoPrefix, { shouldValidate: false });
        }
      }
    }
  }, [watchedValues, activeTab, prefixManuallyChanged, setValue]);

  // Reset form when switching tabs
  const handleTabChange = (tab: "personal" | "shared" | "redirect") => {
    setActiveTab(tab);
    setPrefixManuallyChanged(false);

    if (tab === "personal") {
      reset({
        type: "personal",
        first_name: "",
        last_name: "",
        prefix: "",
        confirmation_accepted: false,
      });
    } else if (tab === "shared") {
      reset({
        type: "shared",
        name: "",
        prefix: "",
      });
    } else {
      reset({
        type: "redirect",
        prefix: "",
        target_email: "",
      });
    }
  };

  const onSubmit = async (data: CreateAddressFormData) => {
    setError(null);
    setIsSubmitting(true);
    try {
      const payload: MailboxAdminCreatePayloadRequest = {
        local_part: data.prefix,
        metadata: {
          type: data.type,
        },
      };

      // Add type-specific data
      if (data.type === "personal") {
        payload.metadata.first_name = data.first_name;
        payload.metadata.last_name = data.last_name;
      } else if (data.type === "redirect") {
        // Find target mailbox for alias creation
        const targetMailbox = mailboxes.find(mb =>
          `${mb.local_part}@${mb.domain_name}` === data.target_email
        );
        payload.alias_of = targetMailbox?.id;
      }

      const response = await createMailbox({ maildomainPk: domainId, data: payload }, );
      setCreatedMailbox(response.data);
    } catch (error: unknown) {
      if (error instanceof APIError && error.data.local_part) {
        setError("create_address_modal.api_errors.prefix_exists");
      } else {
        setError("create_address_modal.api_errors.default");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    form.reset();
    setError(null);
    setActiveTab("personal");
    setCreatedMailbox(null);
    onClose();
  };

  const handleCloseSuccess = () => {
    handleClose();
    refetchMailboxes();
  };

  const getFieldError = <Type extends "personal" | "shared" | "redirect", Errors extends FieldErrors<Extract<CreateAddressFormData, { type: Type }>> = FieldErrors<Extract<CreateAddressFormData, { type: Type }>>>(fieldName: keyof Errors) => {
      const errors = form.formState.errors as Errors;
      const error = errors?.[fieldName];
      return error?.message ? t(error.message as string) : undefined;
  }

  return (
    <Modal
      isOpen={isOpen}
      title={t('create_address_modal.title', { domain: domainName })}
      size={ModalSize.LARGE}
      onClose={handleClose}
    >
      {createdMailbox ? (
        <MailboxCreationSuccess type={activeTab} mailbox={createdMailbox} onClose={handleCloseSuccess} />
      ) : (
      <div className="modal-create-address">
        {/* Tab Navigation */}
        <div className="modal-tabs">
          <button
            type="button"
            className={clsx('modal-tab', {'modal-tab--active': activeTab === "personal"})}
            onClick={() => handleTabChange("personal")}
          >
            {t('create_address_modal.tabs.personal')}
          </button>
          <button
            type="button"
            className={clsx('modal-tab', {'modal-tab--active': activeTab === "shared"})}
            onClick={() => handleTabChange("shared")}
          >
            {t('create_address_modal.tabs.shared')}
          </button>
          <button
            type="button"
            className={clsx('modal-tab', {'modal-tab--active': activeTab === "redirect"})}
            onClick={() => handleTabChange("redirect")}
          >
            {t('create_address_modal.tabs.redirect')}
          </button>
        </div>

        <FormProvider {...form}>
          <form onSubmit={handleSubmit(onSubmit)} noValidate>
            {error && (
              <Banner type="error">
                {t(error)}
              </Banner>
            )}

            {/* Personal Mailbox Form */}
            {activeTab === "personal" && (
              <>
                <div className="form-field-row name-row">
                  <RhfInput
                    label={t('create_address_modal.form.labels.first_name')}
                    text={getFieldError<"personal">('first_name')}
                    name="first_name"
                    className="name-input"
                  />
                  <RhfInput
                    label={t('create_address_modal.form.labels.last_name')}
                    text={getFieldError<"personal">('last_name')}
                    name="last_name"
                    className="name-input"
                  />
                </div>

                <div className="form-field-row address-row">
                  <RhfInput
                    label={t('create_address_modal.form.labels.address')}
                    text={getFieldError<"personal">('prefix')}
                    name="prefix"
                    fullWidth
                    className="address-input"
                    onFocus={() => setPrefixHasFocus(true)}
                    onBlur={() => setPrefixHasFocus(false)}
                    onInput={() => {
                      setPrefixManuallyChanged(true);
                    }}
                  />
                  <span className="domain-suffix">@{domainName}</span>
                </div>

                <div className="form-field-row">
                  <RhfCheckbox
                    label={t('create_address_modal.form.labels.confirmation_accepted')}
                    state={getFieldError<"personal">('confirmation_accepted') ? "error" : "default"}
                    text={getFieldError<"personal">('confirmation_accepted')}
                    name="confirmation_accepted"
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
                    label={t('create_address_modal.form.labels.name')}
                    text={getFieldError<"shared">('name')}
                    name="name"
                    fullWidth
                  />
                </div>

                <div className="form-field-row address-row">
                  <RhfInput
                    label={t('create_address_modal.form.labels.address')}
                    text={getFieldError<"shared">('prefix')}
                    name="prefix"
                    fullWidth
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
                    label={t('create_address_modal.form.labels.address')}
                    name="prefix"
                    text={getFieldError<"redirect">('prefix')}
                    fullWidth
                    className="address-input"
                    onFocus={() => setPrefixHasFocus(true)}
                    onBlur={() => setPrefixHasFocus(false)}
                    onInput={() => {
                      setPrefixManuallyChanged(true);
                    }}
                  />
                  <span className="domain-suffix">@{domainName}</span>
                </div>

                <div className="form-field-row">
                  <RhfInput
                    label={t('create_address_modal.form.labels.target_email')}
                    text={getFieldError<"redirect">('target_email')}
                    name="target_email"
                    type="email"
                    fullWidth
                  />
                </div>
              </>
            )}

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
      )}
    </Modal>
  );
};
