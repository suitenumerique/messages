import { Input } from "@codegouvfr/react-dsfr/Input";
import { Button } from "@codegouvfr/react-dsfr/Button";
import { kcSanitize } from "keycloakify/lib/kcSanitize";
import type { PageProps } from "keycloakify/login/pages/PageProps";
import type { KcContext } from "../KcContext";
import type { I18n } from "../i18n";
import "../login.css";
import { LogoutOtherSessions } from "../components/LogoutOtherSessions";

export default function LoginConfigTotp(
  props: PageProps<
    Extract<KcContext, { pageId: "login-config-totp.ftl" }>,
    I18n
  >,
) {
  const { kcContext, i18n, doUseDefaultCss, Template, classes } = props;
  const { url, isAppInitiatedAction, totp, mode, messagesPerField } = kcContext;
  const { msg, msgStr, advancedMsg } = i18n;

  const fieldError = (name: string, id: string) =>
    messagesPerField.existsError(name) ? (
      <span
        id={id}
        aria-live="polite"
        dangerouslySetInnerHTML={{
          __html: kcSanitize(messagesPerField.get(name)),
        }}
      />
    ) : undefined;

  return (
    <Template
      kcContext={kcContext}
      i18n={i18n}
      doUseDefaultCss={doUseDefaultCss}
      classes={classes}
      headerNode={msg("loginTotpTitle")}
      displayMessage={!messagesPerField.existsError("totp", "userLabel")}
    >
      <ol id="kc-totp-settings" className="fr-text--sm">
        <li>
          <p>{msg("loginTotpStep1")}</p>
          <ul id="kc-totp-supported-apps">
            {totp.supportedApplications.map((app) => (
              <li key={app}>{advancedMsg(app)}</li>
            ))}
          </ul>
        </li>
        {mode == "manual" ? (
          <>
            <li>
              <p>{msg("loginTotpManualStep2")}</p>
              <p>
                <span id="kc-totp-secret-key">{totp.totpSecretEncoded}</span>
              </p>
              <p>
                <a href={totp.qrUrl} id="mode-barcode">
                  {msg("loginTotpScanBarcode")}
                </a>
              </p>
            </li>
            <li>
              <p>{msg("loginTotpManualStep3")}</p>
              <ul>
                <li id="kc-totp-type">
                  {msg("loginTotpType")}: {msg(`loginTotp.${totp.policy.type}`)}
                </li>
                <li id="kc-totp-algorithm">
                  {msg("loginTotpAlgorithm")}: {totp.policy.getAlgorithmKey()}
                </li>
                <li id="kc-totp-digits">
                  {msg("loginTotpDigits")}: {totp.policy.digits}
                </li>
                {totp.policy.type === "totp" ? (
                  <li id="kc-totp-period">
                    {msg("loginTotpInterval")}: {totp.policy.period}
                  </li>
                ) : (
                  <li id="kc-totp-counter">
                    {msg("loginTotpCounter")}: {totp.policy.initialCounter}
                  </li>
                )}
              </ul>
            </li>
          </>
        ) : (
          <li>
            <p>{msg("loginTotpStep2")}</p>
            <img
              id="kc-totp-secret-qr-code"
              src={"data:image/png;base64, " + totp.totpSecretQrCode}
              alt={msgStr("loginTotpStep2")}
            />
            <p>
              <a href={totp.manualUrl} id="mode-manual">
                {msg("loginTotpUnableToScan")}
              </a>
            </p>
          </li>
        )}
        <li>
          <p>{msg("loginTotpStep3")}</p>
          <p>{msg("loginTotpStep3DeviceName")}</p>
        </li>
      </ol>

      <form action={url.loginAction} id="kc-totp-settings-form" method="post">
        <Input
          label={msg("authenticatorCode")}
          state={messagesPerField.existsError("totp") ? "error" : "default"}
          stateRelatedMessage={fieldError("totp", "input-error-otp-code")}
          nativeInputProps={{
            id: "totp",
            name: "totp",
            autoComplete: "off",
            inputMode: "numeric",
            required: true,
            autoFocus: true,
          }}
        />
        <input
          type="hidden"
          id="totpSecret"
          name="totpSecret"
          value={totp.totpSecret}
        />
        {mode && <input type="hidden" id="mode" name="mode" value={mode} />}
        <Input
          label={msg("loginTotpDeviceName")}
          state={
            messagesPerField.existsError("userLabel") ? "error" : "default"
          }
          stateRelatedMessage={fieldError("userLabel", "input-error-otp-label")}
          nativeInputProps={{
            id: "userLabel",
            name: "userLabel",
            autoComplete: "off",
            required: totp.otpCredentials.length >= 1,
          }}
        />
        <LogoutOtherSessions i18n={i18n} />
        <ul className="fr-btns-group fr-btns-group--inline-md">
          <li>
            <Button nativeButtonProps={{ id: "saveTOTPBtn", type: "submit" }}>
              {msgStr("doSubmit")}
            </Button>
          </li>
          {isAppInitiatedAction && (
            <li>
              <Button
                priority="secondary"
                nativeButtonProps={{
                  id: "cancelTOTPBtn",
                  name: "cancel-aia",
                  value: "true",
                  type: "submit",
                  formNoValidate: true,
                }}
              >
                {msg("doCancel")}
              </Button>
            </li>
          )}
        </ul>
      </form>
    </Template>
  );
}
