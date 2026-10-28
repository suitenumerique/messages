import { i18nBuilder } from "keycloakify/login";
import type { ThemeName } from "../kc.gen";

// Keycloakify reads this object statically, so it must stay one literal.
// The two deleteCredential keys are missing from the Keycloakify FR set.
const { useI18n, ofTypeI18n } = i18nBuilder
  .withThemeName<ThemeName>()
  .withCustomTranslations({
    fr: {
      lstLoginTitle: "Accéder au compte",
      lstPasswordLabel: "Renseignez votre mot de passe",
      lstPasskeyButton: "Se connecter avec une clé d'accès",
      lstOtherMethods: "Autres méthodes de connexion",
      lstPasskeyLabelField: "Nom de la clé",
      lstPasskeyLabelDefault: "Clé d'accès",
      lstPasskeyLabelHint:
        "Ce nom vous aide à reconnaître la clé dans votre compte.",
      lstPasskeyRegisterTitle: "Enregistrer une clé",
      lstPasskeyRegisterButton: "Enregistrer la clé",
      lstPasskeyUnsupportedTitle:
        "Impossible de créer une clé d’accès sur cet appareil",
      lstPasskeyUnsupportedText:
        "Assurez-vous que le système d’exploitation de votre appareil est à jour, que le verrouillage de l’écran et le Bluetooth sont activés, et que vous utilisez un navigateur compatible.",
      lstBack: "Retour",
      deleteCredentialTitle: "Supprimer {0}",
      deleteCredentialMessage: "Voulez-vous supprimer {0} ?",
    },
    en: {
      lstLoginTitle: "Access your account",
      lstPasswordLabel: "Enter your password",
      lstPasskeyButton: "Sign in with a passkey",
      lstOtherMethods: "Other sign-in methods",
      lstPasskeyLabelField: "Key name",
      lstPasskeyLabelDefault: "Passkey",
      lstPasskeyLabelHint:
        "This name helps you recognise the key in your account.",
      lstPasskeyRegisterTitle: "Register a key",
      lstPasskeyRegisterButton: "Register the key",
      lstPasskeyUnsupportedTitle: "Unable to create a passkey on this device",
      lstPasskeyUnsupportedText:
        "Make sure the operating system of your device is up to date, that the screen lock and Bluetooth are on, and that you use a compatible browser.",
      lstBack: "Back",
      deleteCredentialTitle: "Delete {0}",
      deleteCredentialMessage: "Do you want to delete {0}?",
    },
  })
  .build();

type I18n = typeof ofTypeI18n;

export { useI18n, ofTypeI18n, type I18n };
