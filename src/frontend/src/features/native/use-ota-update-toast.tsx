import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { addToast, ToasterItem } from "@/features/ui/components/toaster";

import { applyStagedOtaUpdate, subscribeOtaUpdateStaged } from "./ota";
import { isNativePlatform } from "./platform";

const OTA_UPDATE_TOAST_ID = "ota-update-staged";

/**
 * Surface a staged OTA bundle (see ota.ts) as a persistent, non-dismissible
 * toast whose only action applies it. Updating is not optional — there is no
 * close button and no "later" — but the user picks the moment, and never
 * tapping is fine too: Capgo applies the staged bundle at the next app
 * backgrounding anyway.
 *
 * Must be mounted where <Toaster/> lives: a toast fired without its container
 * is lost, and the subscription replays a bundle staged before mount (the boot
 * check usually wins that race).
 */
export const useOtaUpdateToast = (): void => {
  const { t } = useTranslation();

  useEffect(() => {
    if (!isNativePlatform()) {
      return;
    }
    return subscribeOtaUpdateStaged(() => {
      addToast(
        <ToasterItem
          type="info"
          closeButton={false}
          actions={[
            {
              label: t("Update"),
              icon: "restart_alt",
              showLabel: true,
              onClick: () => void applyStagedOtaUpdate(),
            },
          ]}
        >
          {t("A new version of the app is available.")}
        </ToasterItem>,
        {
          // A language change re-runs this effect and the subscription replays
          // the staged bundle: the stable id makes that a no-op instead of a
          // duplicate toast.
          toastId: OTA_UPDATE_TOAST_ID,
          autoClose: false,
          closeOnClick: false,
          draggable: false,
        },
      );
    });
  }, [t]);
};
