import Button from "@codegouvfr/react-dsfr/Button";
import Input from "@codegouvfr/react-dsfr/Input";
import type { CredentialContainer, UserRepresentation } from "../api";
import { CredentialRow } from "../components/CredentialRow";
import { t } from "../i18n";
import { navigate, routePaths } from "../router";

const MFA_TYPES = ["otp", "webauthn-passwordless", "webauthn"];

type Props = {
  user: UserRepresentation;
  credentials: CredentialContainer[];
  runAction: (action: string) => Promise<void>;
};

export function AccountAndLogin({ user, credentials, runAction }: Props) {
  const containers = credentials.filter((c) => MFA_TYPES.includes(c.type));
  const rows = containers.flatMap((container) =>
    container.userCredentialMetadatas.map((metadata) => ({
      container,
      metadata,
    })),
  );

  const canCreate = credentials.some(
    (c) => MFA_TYPES.includes(c.type) && c.createAction,
  );

  return (
    <>
      <h1 className="fr-h3">{t("account.title")}</h1>

      <h2 className="fr-h5">{t("account.credentials")}</h2>
      <Input
        label={t("account.email")}
        nativeInputProps={{ type: "email", value: user.email, readOnly: true }}
        disabled
      />
      <Button
        priority="secondary"
        className="fr-mb-5w"
        onClick={() => void runAction("UPDATE_PASSWORD")}
      >
        {t("account.changePassword")}
      </Button>

      <h2 className="fr-h5">{t("account.twoFactor")}</h2>
      {rows.length === 0 ? (
        <>
          <p>{t("account.noTwoFactor")}</p>
          {canCreate && (
            <Button
              priority="secondary"
              onClick={() => navigate(routePaths.choice)}
            >
              {t("account.setupTwoFactor")}
            </Button>
          )}
        </>
      ) : (
        <>
          <ul
            id="credential-list"
            className="fr-raw-list lst-credential-list"
            role="list"
          >
            {rows.map(({ container, metadata }) => (
              <CredentialRow
                key={metadata.credential.id}
                container={container}
                metadata={metadata}
                onDelete={(id) => void runAction("delete_credential:" + id)}
              />
            ))}
          </ul>
          {canCreate && (
            <Button
              priority="tertiary"
              iconId="fr-icon-add-line"
              onClick={() => navigate(routePaths.choice)}
            >
              {t("account.addMethod")}
            </Button>
          )}
        </>
      )}
    </>
  );
}
