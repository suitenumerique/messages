import { Button } from "@gouvfr-lasuite/ui-components";
import { useTranslation } from "react-i18next";
import { Mailbox } from "@/features/api/gen";
import { Badge } from "@/features/ui/components/badge";
import { Icon } from "@/features/ui/components/icon";
import {
    DEFAULT_CALENDAR_COLOR,
    useMailboxCalendars,
} from "@/features/layouts/components/thread-view/components/calendar-invite/calendars";

/**
 * The calendar linked to this mailbox, which invitations are saved in and
 * checked against. Read-only: a calendar is linked to a mailbox in Calendars.
 */
export const MailboxSettingsCalendarTab = ({ mailbox }: { mailbox: Mailbox }) => {
    const { t } = useTranslation();
    const { data } = useMailboxCalendars(mailbox.id);
    const webUrl = data?.data.web_url;
    const linked = (data?.data.calendars ?? []).filter((calendar) => calendar.linked);

    return (
        <div className="mailbox-settings__tab mailbox-settings__calendar-tab">
            <section className="mailbox-settings__section">
                <h3 className="mailbox-settings__section-title">{t("Linked calendar")}</h3>
                {linked.map((calendar) => (
                    <span key={calendar.id} className="mailbox-settings__calendar">
                        <span
                            className="mailbox-settings__calendar-color"
                            style={{ backgroundColor: calendar.color || DEFAULT_CALENDAR_COLOR }}
                        />
                        {calendar.name}
                        {linked.length > 1 && calendar.id === data?.data.default_calendar_id && (
                            <Badge color="neutral" variant="tertiary" compact>
                                {t("Default")}
                            </Badge>
                        )}
                    </span>
                ))}
                <p className="mailbox-settings__section-description">
                    {linked.length > 0
                        ? `${t("Shared with everyone who has access to this mailbox.")} ${t(
                              "Invitations are saved here and checked for conflicts. You can check more calendars on the invitation itself.",
                          )}`
                        : t("No calendar is linked to this mailbox.")}
                </p>
                {webUrl && (
                    <Button
                        className="mailbox-settings__calendar-link"
                        size="small"
                        variant="secondary"
                        icon={<Icon name="open_in_new" />}
                        href={webUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        {linked.length > 0 ? t("Open calendar") : t("Link a calendar in Calendars")}
                    </Button>
                )}
            </section>
        </div>
    );
};
