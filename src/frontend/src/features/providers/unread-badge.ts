import { useSyncExternalStore } from "react";

import { setFaviconBadge } from "@/features/providers/theme-favicons";

/** The marker the tab title carries where the favicon dot cannot render. A dot
 * rather than a count: it means "mail arrived while you were away", not "you
 * have N unread" — see `useUnreadBadge` for why the app draws that line. */
const TITLE_PREFIX = "• ";

let badged = false;
const listeners = new Set<() => void>();

/**
 * Raise or clear the unread badge: the single writer behind both of its
 * renderings — the favicon dot and the title marker — so the two can never
 * drift apart.
 */
export const setUnreadBadge = (enabled: boolean): void => {
  if (badged === enabled) return;
  badged = enabled;
  setFaviconBadge(enabled);
  listeners.forEach((listener) => listener());
};

export const subscribeUnreadBadge = (listener: () => void): (() => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

export const getUnreadBadge = (): boolean => badged;

export type UnreadTracking = {
  baseline: number;
  /** Raise (true), clear (false), or leave the badge untouched (absent). */
  badge?: boolean;
};

/**
 * Decide what a fresh unread total means for the badge (the pure core of
 * `useUnreadBadge`, extracted for testability).
 *
 * Visible tab — or the very first total on a hidden one: the total becomes the
 * baseline and the badge clears; the user is (or was just) looking. Hidden
 * tab: only a rise above the baseline raises the badge, and the baseline
 * follows decreases — mail read on another device must not absorb the next
 * arrival — without ever lowering the badge itself.
 */
export const trackUnreadTotal = (
  baseline: number | undefined,
  unreadTotal: number,
  hidden: boolean,
): UnreadTracking => {
  if (hidden && baseline !== undefined) {
    const lowered = Math.min(baseline, unreadTotal);
    return unreadTotal > lowered
      ? { baseline: lowered, badge: true }
      : { baseline: lowered };
  }
  return { baseline: unreadTotal, badge: false };
};

/**
 * True on WebKit — Safari, and every iOS browser, which all wrap it.
 *
 * WebKit reads the favicon once at first paint and never re-reads it, so the
 * favicon dot never reaches the tab there (see `setFaviconBadge`); the title is
 * the only surface left to carry the signal. Chromium and Gecko do re-read it,
 * so they show the dot and their title stays clean.
 *
 * Sniffing the UA is unavoidable here: "does this engine re-read the favicon?"
 * has no feature test. Chromium ships both "Chrome"/"Edg" and "AppleWebKit",
 * hence the exclusion; iOS Chrome ("CriOS") and iOS Firefox ("FxiOS") ship
 * neither and are deliberately caught — they are WebKit underneath and share
 * the limitation.
 */
const isWebKit = (): boolean => {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  return /AppleWebKit/.test(ua) && !/Chrome|Chromium|Edg\//.test(ua);
};

/** The tab title's unread marker, or "" where the favicon dot carries it. */
export const unreadTitlePrefix = (enabled: boolean): string =>
  enabled && isWebKit() ? TITLE_PREFIX : "";

/** Live `unreadTitlePrefix`, for `useDocumentTitle`. */
export const useUnreadTitlePrefix = (): string =>
  unreadTitlePrefix(useSyncExternalStore(subscribeUnreadBadge, getUnreadBadge));
