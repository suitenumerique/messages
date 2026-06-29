import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useEffect } from "react";
import { FormProvider, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Mailbox, useMailboxesPartialUpdate } from "@/features/api/gen";
import { RhfInput } from "@/features/forms/components/react-hook-form/rhf-input";
import { useMailboxContext } from "@/features/providers/mailbox";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { Icon } from "@gouvfr-lasuite/ui-kit";
import i18n from "@/features/i18n/initI18n";

type MailboxSettingsGeneralTabProps = {
  mailbox: Mailbox;
  /** Reports the form's dirty state so the parent modal can guard tab switches
   * and closing against discarding unsaved edits. */
  onDirtyChange?: (isDirty: boolean) => void;
};

const renameSchema = z.object({
  // `.trim()` runs before `.min(1)`, so whitespace-only input fails validation
  // and the submitted value is normalized (matching the backend, which stores
  // the trimmed display name). `.max(255)` mirrors the `Contact.name` column.
  name: z
    .string()
    .trim()
    .min(1, { error: i18n.t("Name is required.") })
    .max(255, { error: i18n.t("The name must not exceed 255 characters.") }),
});
type RenameFormData = z.infer<typeof renameSchema>;

export const MailboxSettingsGeneralTab = ({ mailbox, onDirtyChange }: MailboxSettingsGeneralTabProps) => {
  const { t } = useTranslation();
  const { refetchMailboxes } = useMailboxContext();

  const form = useForm<RenameFormData>({
    resolver: zodResolver(renameSchema),
    defaultValues: { name: mailbox.name ?? "" },
  });

  const { mutateAsync: renameMailbox, isPending } = useMailboxesPartialUpdate({
    mutation: { meta: { noGlobalError: true } }
  });

  const onSubmit = async (data: RenameFormData) => {
    try {
      await renameMailbox({ id: mailbox.id, data: { name: data.name } });
      await refetchMailboxes();
      form.reset({ name: data.name });
      addToast(
        <ToasterItem type="info">
          <Icon name="check" />
          <span>{t("The mailbox name has been updated!")}</span>
        </ToasterItem>,
        { toastId: "toast_mailbox_settings_rename_success" },
      );
    } catch {
      addToast(
        <ToasterItem type="error">
          <span>{t("An error occurred while updating the mailbox name.")}</span>
        </ToasterItem>,
      );
    }
  };

  // Surface the dirty state to the parent modal, and clear it on unmount so a
  // stale "dirty" flag can't outlive this tab (Cunningham unmounts the inactive
  // tab's content, so switching away tears the form down entirely).
  useEffect(() => {
    onDirtyChange?.(form.formState.isDirty);
  }, [form.formState.isDirty, onDirtyChange]);

  useEffect(() => {
    return () => onDirtyChange?.(false);
  }, [onDirtyChange]);

  return (
    <div className="mailbox-settings__tab mailbox-settings__general">
      <section className="mailbox-settings__section">
        <header className="mailbox-settings__section-header">
          <div>
            <h3 className="mailbox-settings__section-title">{t("Name")}</h3>
            <p className="mailbox-settings__section-description">
              {t("Customize your sender name")}
            </p>
          </div>
        </header>
        <FormProvider {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} noValidate>
            <RhfInput
              label={t("Name")}
              name="name"
              hideLabel
              fullWidth
              variant="classic"
              text={form.formState.errors.name?.message}
              rightIcon={
                <Button
                  type="submit"
                  size="small"
                  disabled={isPending || !form.formState.isDirty}
                >
                  {isPending ? t("Saving...") : t("Validate")}
                </Button>
              }
            />
          </form>
        </FormProvider>
      </section>
    </div>
  );
};
