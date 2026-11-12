import Alert from "@codegouvfr/react-dsfr/Alert";
import type { PendingAction } from "../auth";
import { t, type MessageKey } from "../i18n";

function actionGroup(
  action: string,
): "password" | "totp" | "key" | "delete" | "other" {
  if (action.startsWith("UPDATE_PASSWORD")) return "password";
  if (action.startsWith("CONFIGURE_TOTP")) return "totp";
  if (action.startsWith("webauthn-register")) return "key";
  if (action.startsWith("delete_credential")) return "delete";
  return "other";
}

const severities = {
  success: "success",
  cancelled: "info",
  error: "error",
} as const;

export function ActionAlert({ pending }: { pending: PendingAction }) {
  const key =
    `action.${actionGroup(pending.action)}.${pending.status}` as MessageKey;
  return (
    <Alert
      className="fr-mb-3w"
      severity={severities[pending.status]}
      title={t(key)}
      closable
    />
  );
}
