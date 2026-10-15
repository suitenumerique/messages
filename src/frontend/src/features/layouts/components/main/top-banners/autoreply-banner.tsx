import { useTranslation } from "react-i18next";
import { HeaderBanner } from "@gouvfr-lasuite/ui-components";
import { GearRounded } from "@gouvfr-lasuite/ui-components/icons";
import { MessageTemplateTypeChoices, useMailboxesMessageTemplatesList } from "@/features/api/gen";
import { MODAL_MAILBOX_SETTINGS_ID } from "@/features/layouts/components/mailbox-settings/modal-mailbox-settings";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useModalStore } from "@/features/providers/modal-store";
import { Icon } from "@/features/ui/components/icon";
import { DateHelper } from "@/features/utils/date-helper";
import { useBannerDismissal } from "./use-banner-dismissal";

/**
 * Warns the user that an auto-reply is active for the selected mailbox.
 * Whether it is active is computed by the backend at fetch time, so a
 * scheduled auto-reply starting or ending is only reflected on page load.
 * Closing it hides it for the selected mailbox only.
 */
export const AutoreplyBanner = () => {
  const { selectedMailbox } = useMailboxContext();
  const { openModal } = useModalStore();
  const { t, i18n } = useTranslation();
  const { isDismissed, dismiss } = useBannerDismissal(
    selectedMailbox ? `autoreply:${selectedMailbox.id}` : undefined,
  );

  const { data } = useMailboxesMessageTemplatesList(
    selectedMailbox?.id ?? "",
    { type: [MessageTemplateTypeChoices.autoreply] },
    {
      query: {
        enabled: !!selectedMailbox?.id,
        staleTime: Infinity,
      },
    },
  );
  const activeAutoreply = data?.data?.find((template) => template.is_active_autoreply);

  if (!selectedMailbox || !activeAutoreply || isDismissed) {
    return null;
  }

  const metadata = activeAutoreply.metadata;
  const endAt = metadata?.schedule_type === "date_range" && typeof metadata.end_at === "string"
    ? DateHelper.formatDateTime(metadata.end_at, i18n.language)
    : undefined;
  // end_at is a wall-clock time in the auto-reply time zone: name that zone
  // when it is not the user's one.
  const timeZone = typeof metadata?.timezone === "string" ? metadata.timezone : undefined;
  const isForeignTimeZone = !!timeZone && timeZone !== Intl.DateTimeFormat().resolvedOptions().timeZone;
  const label = endAt
    ? t("An auto-reply is active for this mailbox until {{date}}.", {
        date: isForeignTimeZone ? `${endAt} (${timeZone})` : endAt,
      })
    : t("An auto-reply is active for this mailbox.");

  return (
    <HeaderBanner
      label={label}
      color="brand"
      ctaProps={{
        label: t("Manage"),
        icon: <Icon icon={GearRounded} />,
        onClick: () => openModal(MODAL_MAILBOX_SETTINGS_ID, { initialTab: "autoreplies" }),
      }}
      onClose={dismiss}
    />
  );
};
