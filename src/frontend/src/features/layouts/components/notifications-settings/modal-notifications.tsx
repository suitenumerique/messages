import { Modal, ModalSize } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";

import { useConfig } from "@/features/providers/config";

import { UserDevicesGrid } from "../mailbox-settings/devices-view/user-devices-grid";

export const MODAL_NOTIFICATIONS_ID = "modal-notifications";

type ModalNotificationsProps = {
  isOpen: boolean;
  onClose: () => void;
};

/**
 * Account-level notifications settings.
 *
 * Unlike the mailbox settings modal this is *user*-scoped — push devices are
 * personal and span every mailbox — so it is reachable by every authenticated
 * user from the header menu, not gated on mailbox-admin abilities.
 *
 * Controlled via `isOpen`/`onClose` props and bound to the global modal store in
 * a SEPARATE file (controlled-modals/notifications). This component must NOT
 * import the modal store: the header imports `MODAL_NOTIFICATIONS_ID` from here,
 * and a store import would close the `modal-store → controlled-modals → modal →
 * store` cycle and trip a temporal-dead-zone error on the id (same reason the
 * mailbox-settings modal is split this way).
 */
export const ModalNotifications = ({
  isOpen,
  onClose,
}: ModalNotificationsProps) => {
  const { t } = useTranslation();
  const config = useConfig();

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t("Notifications")}
      size={ModalSize.MEDIUM}
    >
      <div className="mailbox-settings__section">
        <p className="mailbox-settings__section-description">
          {t(
            "Devices where you receive push notifications. These are personal to you and span all your mailboxes.",
          )}
        </p>
        {config.PUSH_ENABLED ? (
          <UserDevicesGrid />
        ) : (
          <p>{t("Notifications are not available on this server.")}</p>
        )}
      </div>
    </Modal>
  );
};
