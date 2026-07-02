import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import { addToast, ToasterItem } from "@/features/ui/components/toaster";

import { nativeDownloadFile } from "./download";

/**
 * UI wrapper around {@link nativeDownloadFile}: the download runs through the
 * native HTTP layer with no browser chrome to surface a failure, so every call
 * site needs the same catch + error toast. Centralized here so none of them
 * can leave the promise dangling.
 */
export const useNativeDownload = () => {
  const { t } = useTranslation();

  return useCallback(
    async (url: string, filename: string): Promise<void> => {
      try {
        await nativeDownloadFile(url, filename);
      } catch (error) {
        console.error("Native download failed", error);
        addToast(
          <ToasterItem type="error">
            {t("Failed to download {{name}}.", { name: filename })}
          </ToasterItem>,
        );
      }
    },
    [t],
  );
};
