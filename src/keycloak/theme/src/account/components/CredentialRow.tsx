import Button from "@codegouvfr/react-dsfr/Button";
import type { CredentialContainer, CredentialMetadata } from "../api";
import { getLang, kcMsg, t, type MessageKey } from "../i18n";

type Props = {
  container: CredentialContainer;
  metadata: CredentialMetadata;
  onDelete: (credentialId: string) => void;
};

const TAGS: Record<string, { label: MessageKey; className: string }> = {
  otp: { label: "credential.otp", className: "fr-tag--purple-glycine" },
  "webauthn-passwordless": {
    label: "credential.passkey",
    className: "fr-tag--green-emeraude",
  },
  webauthn: {
    label: "credential.securityKey",
    className: "fr-tag--blue-cumulus",
  },
};

export function CredentialRow({ container, metadata, onDelete }: Props) {
  const { credential } = metadata;
  const label = credential.userLabel || kcMsg(container.displayName);
  const tag = TAGS[container.type] ?? TAGS.webauthn;
  const addedOn =
    credential.createdDate === undefined
      ? undefined
      : new Intl.DateTimeFormat(getLang(), { dateStyle: "long" }).format(
          credential.createdDate,
        );

  return (
    <li className="lst-credential" data-credential-type={container.type}>
      <div className="lst-credential__body">
        <h3 className="fr-text--md fr-text--bold fr-mb-1v">{label}</h3>
        <div className="lst-credential__tags">
          <p className={`fr-tag fr-tag--sm ${tag.className}`}>{t(tag.label)}</p>
          {container.type === "webauthn" && (
            <p className="fr-badge fr-badge--sm fr-badge--info">
              {t("credential.secondFactorOnly")}
            </p>
          )}
        </div>
        {addedOn && (
          <p className="fr-text--xs fr-mb-0 fr-hint-text">
            {`${t("credential.addedOn")} ${addedOn}`}
          </p>
        )}
      </div>
      {container.removeable && (
        <Button
          priority="tertiary no outline"
          iconId="fr-icon-delete-bin-line"
          nativeButtonProps={{
            "aria-label": `${t("credential.delete")} ${label}`,
          }}
          onClick={() => onDelete(credential.id)}
        >
          {t("credential.delete")}
        </Button>
      )}
    </li>
  );
}
