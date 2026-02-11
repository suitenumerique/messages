import { useEffect, useMemo, useRef } from 'react';
import MailHelper from "@/features/utils/mail-helper";

/**
 * Replaces base64 data URLs with lightweight Object URLs in sanitized HTML.
 * This avoids bloating the DOM with large base64 strings (e.g. ~2.6MB per image)
 * while keeping the visual rendering identical.
 *
 * Object URLs are revoked when the input HTML changes or on unmount.
 */
export const useHtmlWithObjectUrls = (
  html: string | null,
): string | null => {
  const activeUrlsRef = useRef<string[]>([]);

  const { processedHtml, createdUrls } = useMemo(() => {
    if (!html) return { processedHtml: null, createdUrls: [] as string[] };

    const urls: string[] = [];
    let imageIndex = 0;

    const result = html.replace(
      /src="(data:image\/[^"]+)"/g,
      (fullMatch, dataUrl: string) => {
        const file = MailHelper.dataUrlToFile(dataUrl, `sig-img-${imageIndex++}`);
        if (!file) return fullMatch;

        const objectUrl = URL.createObjectURL(file);
        urls.push(objectUrl);
        return `src="${objectUrl}"`;
      },
    );

    return { processedHtml: result, createdUrls: urls };
  }, [html]);

  // Revoke previous Object URLs when the input HTML changes
  useEffect(() => {
    const previousUrls = activeUrlsRef.current;
    activeUrlsRef.current = createdUrls;

    return () => {
      for (const url of previousUrls) {
        URL.revokeObjectURL(url);
      }
    };
  }, [createdUrls]);

  // Revoke all Object URLs on unmount
  useEffect(() => () => {
    for (const url of activeUrlsRef.current) {
      URL.revokeObjectURL(url);
    }
  }, []);

  return processedHtml;
};
