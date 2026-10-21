import type { ClassKey } from "keycloakify/login";

export const classes = {
  kcFormGroupClass: "fr-input-group",
  kcLabelClass: "fr-label",
  kcInputClass: "fr-input",
  kcInputGroup: "fr-input-wrap",
  kcInputErrorMessageClass: "fr-error-text",
  kcInputHelperTextAfterClass: "fr-hint-text",
  kcButtonClass: "fr-btn",
  kcButtonDefaultClass: "fr-btn--secondary",
  kcAlertClass: "fr-alert fr-alert--sm",
  kcAlertTitleClass: "fr-alert__title",
  kcInputClassRadioLabel: "fr-label",
  kcSelectAuthListItemClass: "fr-tile fr-tile--sm fr-enlarge-button",
  kcSelectAuthListItemTitle: "fr-tile__title",
  kcSelectAuthListItemDescriptionClass: "fr-tile__desc",
  kcSrOnlyClass: "fr-sr-only",
  kcFormButtonsClass: "fr-btns-group fr-btns-group--inline",
  kcFormPasswordVisibilityButtonClass: "fr-btn fr-btn--tertiary-no-outline",
} satisfies { [key in ClassKey]?: string };
