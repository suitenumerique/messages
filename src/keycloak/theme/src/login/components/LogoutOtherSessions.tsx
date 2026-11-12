import type { I18n } from "../i18n";

export function LogoutOtherSessions(props: { i18n: I18n }) {
  const { msg } = props.i18n;

  return (
    <div className="fr-checkbox-group fr-mb-2w">
      <input
        type="checkbox"
        id="logout-sessions"
        name="logout-sessions"
        value="on"
      />
      <label className="fr-label" htmlFor="logout-sessions">
        {msg("logoutOtherSessions")}
      </label>
    </div>
  );
}
