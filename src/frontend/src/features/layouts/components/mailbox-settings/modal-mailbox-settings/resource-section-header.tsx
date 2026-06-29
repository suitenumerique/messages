import { ReactNode } from "react";

type ResourceSectionHeaderProps = {
  /**
   * Resource-count summary shown on the leading edge (e.g. "3 signatures" or
   * "No signatures"). Left empty while the list is still loading so the label
   * does not flash an inaccurate "none" before the count arrives.
   */
  label?: ReactNode;
  /** Create-resource action (a nano button) shown on the trailing edge. */
  action: ReactNode;
};

/**
 * Shared header for the feature settings tabs (signatures, message templates,
 * auto-replies, integrations): a muted resource count on the left and the
 * "New …" action on the right, mirroring the access-sharing tab layout.
 */
export const ResourceSectionHeader = ({
  label,
  action,
}: ResourceSectionHeaderProps) => (
  <header className="mailbox-settings__section-header">
    <strong className="mailbox-settings__section-count">{label}</strong>
    {action}
  </header>
);
