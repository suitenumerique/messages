import { DropdownMenu, DropdownMenuItem, Icon, IconType } from "@gouvfr-lasuite/ui-kit"
import { Button, ButtonProps, Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { useTranslation } from "react-i18next"
import { useAuth } from "@/features/auth";
import { useConfig } from "@/features/providers/config";
import { useState } from "react";
import { WidgetHelper } from "@/features/utils/widget-helper";
import { formatVersionReport, useAppVersion } from "@/features/hooks/use-app-version";
import { addToast, ToasterItem } from "../toaster";
import { handle } from "@/features/utils/errors";

type SurveyButtonProps = ButtonProps & {
  /** Display only icon without label */
  iconOnly?: boolean;
}

/**
 * A button opening a menu with the help center, the feedback widget and the
 * running app version. The support entries depend on what the instance
 * configures; the version entry is always there, which is why the button
 * renders even on an instance with no support channel at all.
 */
export const SurveyButton = ({ iconOnly = false, ...props }: SurveyButtonProps) => {
  const { t } = useTranslation()
  const { user } = useAuth();
  const { FEEDBACK_WIDGET, HELP_CENTER_URL } = useConfig();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const version = useAppVersion();

  const { api_url: apiUrl, path: widgetPath, channel } = FEEDBACK_WIDGET;
  const helpCenterUrl = HELP_CENTER_URL;

  const hasWidget = !!(channel && apiUrl && widgetPath);
  const hasHelpCenter = !!helpCenterUrl;

  const title: string = t("Do you have any feedback?");
  const placeholder: string = t("Share your feedback here...");
  const emailPlaceholder: string = t("Your email...");
  const submitText: string = t("Send Feedback");
  const successText: string = t("Thank you for your feedback!");
  const successText2: string = t("In case of questions, we'll get back to you soon.");
  const closeLabel: string = t("Close the feedback widget");

  const showWidget = () => {
    WidgetHelper.pushCommand([
      "feedback",
      "init",
      {
        title,
        api: apiUrl,
        channel,
        placeholder,
        emailPlaceholder,
        submitText,
        successText,
        successText2,
        closeLabel,
        // Add email parameter if user is logged in
        ...(user?.email && { email: user.email }),
      },
    ]);

    WidgetHelper.loadScript(`${widgetPath}feedback.js`);
  }

  const openHelpCenter = () => {
    if (helpCenterUrl) {
      window.open(helpCenterUrl, '_blank', 'noopener,noreferrer');
    }
  }

  // Everything identifying the running app in one line, so a user reporting a
  // problem can paste it instead of reading numbers out.
  const copyVersionReport = async () => {
    try {
      await navigator.clipboard.writeText(formatVersionReport(version));
      addToast(<ToasterItem><p>{t('Version details copied to clipboard')}</p></ToasterItem>);
    } catch (error) {
      handle(new Error('Failed to copy the version details.'), { extra: { error } });
    }
  }

  // Determine button label and icon based on configuration
  const getButtonLabel = () => {
    if (hasHelpCenter && hasWidget) return t("Help center & Support");
    if (hasHelpCenter) return t("Visit the Help center");
    if (hasWidget) return t("Contact the Support team");
    return t("About this app");
  }

  const getButtonIcon = () => {
    if (hasWidget && !hasHelpCenter) return "feedback";
    return "help";
  }

  const supportOptions: DropdownMenuItem[] = [
    ...(hasHelpCenter ? [{
      label: t("Visit the Help center"),
      icon: <Icon name="help" type={IconType.FILLED} />,
      callback: openHelpCenter,
      subText: t("Tutorials and training"),
    }] : []),
    ...(hasWidget ? [{
      label: t("Contact the Support team"),
      icon: <Icon name="feedback" type={IconType.FILLED} />,
      callback: showWidget,
      subText: t("I have an issue or a feature request"),
    }] : []),
  ];

  const dropdownOptions: DropdownMenuItem[] = [
    ...supportOptions,
    ...(supportOptions.length ? [{ type: "separator" } as const] : []),
    {
      // Native builds carry two numbers that move independently: the installed
      // app, updated through the store, and the web bundle inside it, which an
      // OTA release can move ahead on its own. The store version is the one
      // users and store listings talk about, so it leads; the bundle version
      // stays legible underneath rather than being merged into a single string.
      label: t("Version {{version}}", { version: version.native ?? version.web }),
      subText: version.native
        ? t("Web interface {{version}}", { version: version.web })
        : undefined,
      icon: <Icon name="info" type={IconType.FILLED} />,
      callback: copyVersionReport,
    },
  ];

  // Always a dropdown: the version entry means the menu is never down to a
  // single item, so there is no configuration left where opening the menu
  // would be a pointless detour around a direct action.
  return (
    <DropdownMenu
      isOpen={isDropdownOpen}
      onOpenChange={setIsDropdownOpen}
      options={dropdownOptions}
    >
      <Tooltip placement="bottom" content={getButtonLabel()}>
        <Button
          {...props}
          icon={<Icon name={getButtonIcon()} type={IconType.FILLED} />}
          color={props.color ?? "brand"}
          variant={props.variant ?? "secondary"}
          className="feedback-button"
          title={getButtonLabel()}
          aria-label={getButtonLabel()}
          onClick={() => setIsDropdownOpen(open => !open)}
        >
          {iconOnly ? null : getButtonLabel()}
        </Button>
      </Tooltip>
    </DropdownMenu>
  );
}
