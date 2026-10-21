import type { KcContext } from "./KcContext";
import { setDsfrLang } from "../shared/dsfr";

const fr = {
  "menu.label": "Menu",
  "menu.account": "Compte et connexion",
  "menu.personalInfo": "Informations personnelles",
  "header.logout": "Se déconnecter",
  loading: "Chargement…",
  "error.load": "Impossible de charger les données du compte.",
  "error.retry": "Réessayer",
  "account.title": "Compte et connexion",
  "account.credentials": "Identifiants",
  "account.email": "Adresse email",
  "account.changePassword": "Modifier mon mot de passe",
  "account.twoFactor": "Double authentification",
  "account.noTwoFactor":
    "Vous n'avez pas encore configuré la double authentification (2FA).",
  "account.setupTwoFactor": "Configurer la double authentification",
  "account.addMethod": "Ajouter une méthode de validation supplémentaire",
  "credential.otp": "Code à usage unique",
  "credential.passkey": "Clé d'accès",
  "credential.addedOn": "Date d'ajout :",
  "credential.securityKey": "Clé de sécurité",
  "credential.secondFactorOnly": "Second facteur uniquement",
  "credential.delete": "Supprimer",
  "profile.title": "Vos informations personnelles",
  "profile.required": "Les champs notés * sont obligatoires.",
  "profile.firstName": "Prénom *",
  "profile.lastName": "Nom *",
  "profile.email": "Adresse email",
  "profile.locale": "Langue",
  "profile.submit": "Mettre à jour",
  "profile.success": "Vos informations ont été mises à jour.",
  "profile.error": "La mise à jour a échoué.",
  "choice.title": "Choisir votre méthode de connexion renforcée",
  "choice.unsupportedTitle":
    "Impossible de créer une clé d'accès sur cet appareil",
  "choice.unsupportedText":
    "Assurez-vous que le système d'exploitation de votre appareil est à jour, que le verrouillage de l'écran et le Bluetooth sont activés, et que vous utilisez un navigateur compatible.",
  "choice.legend": "Méthode",
  "choice.totp": "Code à usage unique (TOTP)",
  "choice.totpHint":
    "Générez un code via une application smartphone ou un logiciel sur votre ordinateur.",
  "choice.passkey": "Clé d'accès (passkey)",
  "choice.passkeyHint":
    "Utilisez la biométrie ou le code PIN de votre session d'ordinateur.",
  "choice.securityKey": "Clé de sécurité physique (second facteur uniquement)",
  "choice.securityKeyHint":
    "Pour une clé qui ne fonctionne pas comme clé d'accès. Elle s'utilise après le mot de passe.",
  "choice.empty": "Aucune méthode n'est disponible.",
  "choice.continue": "Continuer",
  "choice.back": "Retour",
  "action.password.success": "Mot de passe modifié.",
  "action.password.cancelled": "Modification du mot de passe annulée.",
  "action.password.error": "La modification du mot de passe a échoué.",
  "action.totp.success": "Application d'authentification ajoutée.",
  "action.totp.cancelled": "Ajout de l'application d'authentification annulé.",
  "action.totp.error": "L'ajout de l'application d'authentification a échoué.",
  "action.key.success": "Clé ajoutée.",
  "action.key.cancelled": "Ajout de la clé annulé.",
  "action.key.error": "L'ajout de la clé a échoué.",
  "action.delete.success": "Méthode supprimée.",
  "action.delete.cancelled": "Suppression de la méthode annulée.",
  "action.delete.error": "La suppression de la méthode a échoué.",
  "action.other.success": "Action terminée.",
  "action.other.cancelled": "Action annulée.",
  "action.other.error": "L'action a échoué.",
};

export type MessageKey = keyof typeof fr;

const en: Record<MessageKey, string> = {
  "menu.label": "Menu",
  "menu.account": "Account and sign-in",
  "menu.personalInfo": "Personal information",
  "header.logout": "Sign out",
  loading: "Loading…",
  "error.load": "Unable to load the account data.",
  "error.retry": "Try again",
  "account.title": "Account and sign-in",
  "account.credentials": "Credentials",
  "account.email": "Email address",
  "account.changePassword": "Change my password",
  "account.twoFactor": "Two-factor authentication",
  "account.noTwoFactor":
    "You have not set up two-factor authentication (2FA) yet.",
  "account.setupTwoFactor": "Set up two-factor authentication",
  "account.addMethod": "Add another verification method",
  "credential.otp": "One-time code",
  "credential.passkey": "Passkey",
  "credential.addedOn": "Date added:",
  "credential.securityKey": "Security key",
  "credential.secondFactorOnly": "Second factor only",
  "credential.delete": "Delete",
  "profile.title": "Your personal information",
  "profile.required": "Fields marked with * are required.",
  "profile.firstName": "First name *",
  "profile.lastName": "Last name *",
  "profile.email": "Email address",
  "profile.locale": "Language",
  "profile.submit": "Update",
  "profile.success": "Your information has been updated.",
  "profile.error": "The update failed.",
  "choice.title": "Choose your strong sign-in method",
  "choice.unsupportedTitle": "Unable to create a passkey on this device",
  "choice.unsupportedText":
    "Make sure the operating system of your device is up to date, that the screen lock and Bluetooth are on, and that you use a compatible browser.",
  "choice.legend": "Method",
  "choice.totp": "One-time code (TOTP)",
  "choice.totpHint":
    "Generate a code with a smartphone application or with software on your computer.",
  "choice.passkey": "Passkey",
  "choice.passkeyHint": "Use biometrics or the PIN of your computer session.",
  "choice.securityKey": "Physical security key (second factor only)",
  "choice.securityKeyHint":
    "For a key that does not work as a passkey. You use it after the password.",
  "choice.empty": "No method is available.",
  "choice.continue": "Continue",
  "choice.back": "Back",
  "action.password.success": "Password changed.",
  "action.password.cancelled": "Password change cancelled.",
  "action.password.error": "The password change failed.",
  "action.totp.success": "Authenticator application added.",
  "action.totp.cancelled":
    "Adding the authenticator application was cancelled.",
  "action.totp.error": "Adding the authenticator application failed.",
  "action.key.success": "Key added.",
  "action.key.cancelled": "Adding the key was cancelled.",
  "action.key.error": "Adding the key failed.",
  "action.delete.success": "Method deleted.",
  "action.delete.cancelled": "Deleting the method was cancelled.",
  "action.delete.error": "Deleting the method failed.",
  "action.other.success": "Action completed.",
  "action.other.cancelled": "Action cancelled.",
  "action.other.error": "The action failed.",
};

let lang: "fr" | "en" = "fr";
let kcMessages: Record<string, string> = {};

export function initI18n(kcContext: KcContext) {
  lang = kcContext.locale.split("-")[0] === "en" ? "en" : "fr";
  document.documentElement.lang = lang;
  setDsfrLang(lang);
  try {
    kcMessages = JSON.parse(kcContext.msgJSON ?? "{}") as Record<
      string,
      string
    >;
  } catch {
    kcMessages = {};
  }
}

export function getLang() {
  return lang;
}

export function t(key: MessageKey): string {
  return (lang === "en" ? en : fr)[key];
}

export function kcMsg(key: string, params: string[] = []): string {
  return (kcMessages[key] ?? key)
    .replace(
      /\{(\d+)\}/g,
      (match, index: string) => params[Number(index)] ?? match,
    )
    .replace(/''/g, "'");
}
