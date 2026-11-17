import { Checkbox, Select } from "@gouvfr-lasuite/ui-components";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Mailbox } from "@/features/api/gen";
import { fetchAPI } from "@/features/api/fetch-api";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";

type SettingsCalendar = {
  id: string;
  name: string;
  color: string | null;
  writable: boolean;
  linked: boolean;
  available: boolean;
  check_conflicts: boolean;
};

type CalendarSettingsApiResponse = {
  data: { calendars: SettingsCalendar[]; default_calendar_id: string | null };
  status: number;
};

type CalendarSettingsPatch = {
  conflict_calendar_ids?: string[];
  available_calendar_ids?: string[];
  default_calendar_id?: string;
};

const settingsUrl = (mailboxId: string) =>
  `/api/v1.0/mailboxes/${mailboxId}/calendar/settings/`;

export const useCalendarSettings = (mailboxId?: string) =>
  useQuery<CalendarSettingsApiResponse>({
    queryKey: ["calendar-settings", mailboxId],
    queryFn: () => fetchAPI<CalendarSettingsApiResponse>(settingsUrl(mailboxId!)),
    enabled: !!mailboxId,
    meta: { noGlobalError: true },
  });

export const MailboxSettingsCalendarTab = ({ mailbox }: { mailbox: Mailbox }) => {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data, isFetching } = useCalendarSettings(mailbox.id);

  const { mutate, isPending } = useMutation({
    mutationFn: (patch: CalendarSettingsPatch) =>
      fetchAPI(settingsUrl(mailbox.id), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }),
    onSuccess: () => {
      for (const key of ["calendar-settings", "calendar-calendars", "calendar-conflicts"]) {
        queryClient.invalidateQueries({ queryKey: [key, mailbox.id] });
      }
    },
    onError: () => {
      addToast(
        <ToasterItem type="error">
          <span>{t("An error occurred while saving the calendar settings.")}</span>
        </ToasterItem>,
      );
    },
    meta: { noGlobalError: true },
  });

  const calendars = [...(data?.data.calendars ?? [])].sort(
    (a, b) => Number(b.linked) - Number(a.linked),
  );
  const defaultCalendarId = data?.data.default_calendar_id ?? null;
  const busy = isPending || isFetching;

  const idsWith = (flag: "available" | "check_conflicts", id: string, checked: boolean) =>
    calendars
      .filter((calendar) => (calendar.id === id ? checked : calendar[flag]))
      .map((calendar) => calendar.id);

  return (
    <div className="mailbox-settings__tab mailbox-settings__calendar">
      <section className="mailbox-settings__section">
        <header className="mailbox-settings__section-header">
          <div>
            <h3 className="mailbox-settings__section-title">{t("Default calendar")}</h3>
            <p className="mailbox-settings__section-description">
              {t("Where events from invitations in this mailbox are saved")}
            </p>
          </div>
        </header>
        <Select
          label={t("Default calendar")}
          hideLabel
          fullWidth
          clearable={false}
          disabled={busy}
          value={defaultCalendarId ?? undefined}
          options={calendars
            .filter((calendar) => calendar.available)
            .map((calendar) => ({
              label: calendar.linked
                ? `${calendar.name} (${t("linked to this mailbox")})`
                : calendar.name,
              value: calendar.id,
            }))}
          onChange={(event) => {
            const value = event.target.value;
            if (typeof value === "string" && value !== defaultCalendarId) {
              mutate({ default_calendar_id: value });
            }
          }}
        />
      </section>
      <section className="mailbox-settings__section">
        <header className="mailbox-settings__section-header">
          <div>
            <h3 className="mailbox-settings__section-title">{t("Calendars")}</h3>
            <p className="mailbox-settings__section-description">
              {t("Choose which calendars you can save events to, and which are checked for conflicting events")}
            </p>
          </div>
        </header>
        <div className="mailbox-settings__calendars">
          <span />
          <span className="mailbox-settings__calendars-head">{t("Available")}</span>
          <span className="mailbox-settings__calendars-head">{t("Check conflicts")}</span>
          {calendars.map((calendar) => (
            <div key={calendar.id} className="mailbox-settings__calendar-row">
              <span className="mailbox-settings__calendar-name">
                <span
                  className="mailbox-settings__calendar-color"
                  style={{ backgroundColor: calendar.color ?? undefined }}
                />
                <span>{calendar.name}</span>
                {calendar.linked && (
                  <span className="mailbox-settings__calendar-linked">
                    {t("Linked to this mailbox")}
                  </span>
                )}
              </span>
              <Checkbox
                aria-label={t("Available")}
                checked={calendar.available}
                disabled={busy || !calendar.writable}
                onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
                  mutate({
                    available_calendar_ids: idsWith("available", calendar.id, event.target.checked),
                  })
                }
              />
              <Checkbox
                aria-label={t("Check conflicts")}
                checked={calendar.check_conflicts}
                disabled={busy}
                onChange={(event: React.ChangeEvent<HTMLInputElement>) =>
                  mutate({
                    conflict_calendar_ids: idsWith("check_conflicts", calendar.id, event.target.checked),
                  })
                }
              />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
};
