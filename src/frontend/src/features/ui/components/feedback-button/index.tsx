import { DropdownMenu, Icon, IconType } from "@gouvfr-lasuite/ui-kit"
import { Button, ButtonProps, Tooltip } from "@gouvfr-lasuite/cunningham-react"
import { useTranslation } from "react-i18next"
import { useAuth } from "@/features/auth";
import { useState } from "react";

type SurveyButtonProps = ButtonProps & {
  /** Display only icon without label */
  iconOnly?: boolean;
}

/**
 * A button that opens the help center, feedback widget, or a dropdown with both options
 */
export const SurveyButton = ({ iconOnly = false, ...props }: SurveyButtonProps) => {
  const { t } = useTranslation()
  const { user } = useAuth();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);

  const apiUrl = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_API_URL;
  const widgetPath = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_PATH;
  const channel = process.env.NEXT_PUBLIC_FEEDBACK_WIDGET_CHANNEL;
  const helpCenterUrl = process.env.NEXT_PUBLIC_HELP_CENTER_URL;

  const hasWidget = !!(channel && apiUrl && widgetPath);
  const hasHelpCenter = !!helpCenterUrl;

  // If neither is configured, don't render the button
  if (!hasHelpCenter && !hasWidget) return null;

  const title: string = t("Do you have any feedback?");
  const placeholder: string = t("Share your feedback here...");
  const emailPlaceholder: string = t("Your email...");
  const submitText: string = t("Send Feedback");
  const successText: string = t("Thank you for your feedback!");
  const successText2: string = t("In case of questions, we'll get back to you soon.");
  const closeLabel: string = t("Close the feedback widget");

  const showWidget = () => {
    // Initialize the widget array if it doesn't exist
    if (typeof window !== "undefined" && widgetPath) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any)._stmsg_widget = (window as any)._stmsg_widget || [];

      // Construct script URLs from the base path
      const feedbackScript = `${widgetPath}feedback.js`;

      // Push the widget configuration
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as any)._stmsg_widget.push([
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

      // Load the loader script if not already loaded
      if (!document.querySelector(`script[src="${feedbackScript}"]`)) {
        const script = document.createElement("script");
        script.async = true;
        script.src = feedbackScript;
        const firstScript = document.getElementsByTagName("script")[0];
        if (firstScript && firstScript.parentNode) {
          firstScript.parentNode.insertBefore(script, firstScript);
        }
      }
    }
  }

  const openHelpCenter = () => {
    if (helpCenterUrl) {
      window.open(helpCenterUrl, '_blank', 'noopener,noreferrer');
    }
  }

  const handleClick = () => {
    if (hasHelpCenter && hasWidget) {
      // Both configured: open dropdown
      setIsDropdownOpen(open => !open);
    } else if (hasHelpCenter) {
      // Only help center: open directly
      openHelpCenter();
    } else {
      // Only widget: show widget directly
      showWidget();
    }
  }

  // Determine button label and icon based on configuration
  const getButtonLabel = () => {
    if (hasHelpCenter && hasWidget) return t("Help center & Support");
    if (hasHelpCenter) return t("Visit the Help center");
    return t("Contact the Support team");
  }

  const getButtonIcon = () => {
    if (hasWidget && !hasHelpCenter) return "feedback";
    return "help";
  }

  const dropdownOptions = [
    {
      label: t("Visit the Help center"),
      icon: <Icon name="help" type={IconType.FILLED} />,
      callback: openHelpCenter,
      showSeparator: true,
      subText: t("Tutorials and training"),
    },
    {
      label: t("Contact the Support team"),
      icon: <Icon name="feedback" type={IconType.FILLED} />,
      callback: showWidget,
      subText: t("I have an issue or a feature request"),
    },
  ];

  const button = (
    <Tooltip placement="bottom" content={getButtonLabel()}>
      <Button
        {...props}
        icon={<Icon name={getButtonIcon()} type={IconType.FILLED} />}
        color={props.color ?? "brand"}
        variant={props.variant ?? "secondary"}
        className="feedback-button"
        title={getButtonLabel()}
        aria-label={getButtonLabel()}
        onClick={handleClick}
      >
        {iconOnly ? null : getButtonLabel()}
      </Button>
    </Tooltip>
  );

  // If both are configured, wrap in dropdown
  if (hasHelpCenter && hasWidget) {
    return (
      <DropdownMenu
        isOpen={isDropdownOpen}
        onOpenChange={setIsDropdownOpen}
        options={dropdownOptions}
      >
        {button}
      </DropdownMenu>
    );
  }

  return button;
}
