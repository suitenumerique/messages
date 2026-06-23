/**
 * RealtimeClient — one SSE connection per *browser*, shared across tabs.
 *
 * Tabs elect a single leader via the Web Locks API; only the leader opens the
 * EventSource and relays every event (and the connection's live/offline state)
 * to the other tabs over a BroadcastChannel. This keeps us to one connection
 * and one Redis fan-out per browser regardless of tab count.
 *
 * Events are intentionally thin ("something changed") — consumers react by
 * refetching over the normal authenticated API. The DB stays the source of
 * truth, so a missed live event is harmless (the adaptive poll reconciles).
 *
 * EventSource can't refresh its token on its native auto-reconnect, so the
 * leader manages reconnection itself: on error it closes, mints a fresh token,
 * and reopens with capped backoff.
 */
import { realtimeTokenCreate } from "@/features/api/gen";
import { getRealtimeOrigin } from "@/features/api/utils";

export type RealtimeEvent = { event: string; data: unknown };
type EventListener = (e: RealtimeEvent) => void;
type StatusListener = (live: boolean) => void;

const LOCK_NAME = "messages-realtime-leader";
const CHANNEL_NAME = "messages-realtime";
const STATUS_REANNOUNCE_MS = 8_000; // leader re-broadcasts status for late joiners
const STATUS_EXPIRY_MS = 20_000; // follower assumes offline if no status within
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

type BroadcastMessage =
  | { kind: "event"; payload: RealtimeEvent }
  | { kind: "status"; live: boolean }
  | { kind: "whois" };

export interface RealtimeClientOptions {
  /** Path the browser opens its SSE stream on, e.g. "/realtime-relay/". */
  eventsPath: string;
  /** Event names to listen for (EventSource requires per-name listeners). */
  eventNames: string[];
}

export class RealtimeClient {
  private opts: RealtimeClientOptions;
  private listeners = new Set<EventListener>();
  private statusListeners = new Set<StatusListener>();
  private channel: BroadcastChannel | null = null;
  private es: EventSource | null = null;
  private started = false;
  private isLeader = false;
  private live = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reannounceTimer: ReturnType<typeof setInterval> | null = null;
  private statusExpiryTimer: ReturnType<typeof setTimeout> | null = null;
  // Aborting this releases the Web Lock so a new client (e.g. React StrictMode
  // double-mount, or a re-created provider) can take leadership.
  private lockAbort: AbortController | null = null;

  constructor(opts: RealtimeClientOptions) {
    this.opts = opts;
  }

  start(): void {
    if (this.started || typeof window === "undefined") return;
    if (!("BroadcastChannel" in window) || !("EventSource" in window)) return;
    this.started = true;

    this.channel = new BroadcastChannel(CHANNEL_NAME);
    this.channel.onmessage = (ev) => this.onBroadcast(ev.data as BroadcastMessage);
    // Ask any existing leader to (re)announce its status so this fresh tab
    // doesn't sit in an unknown state until the next periodic re-announce.
    this.channel.postMessage({ kind: "whois" } satisfies BroadcastMessage);

    this.electLeader();
  }

  stop(): void {
    if (!this.started) return;
    this.started = false;
    this.closeEventSource();
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.reannounceTimer) clearInterval(this.reannounceTimer);
    if (this.statusExpiryTimer) clearTimeout(this.statusExpiryTimer);
    // Release the Web Lock so another client can become leader. Without this,
    // the never-resolving lock promise keeps leadership for the page lifetime.
    this.lockAbort?.abort();
    this.lockAbort = null;
    this.channel?.close();
    this.channel = null;
  }

  subscribe(fn: EventListener): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onStatus(fn: StatusListener): () => void {
    this.statusListeners.add(fn);
    fn(this.live);
    return () => this.statusListeners.delete(fn);
  }

  isLive(): boolean {
    return this.live;
  }

  // --- leadership -------------------------------------------------------

  private electLeader(): void {
    if (!("locks" in navigator)) {
      // No Web Locks: degrade to one connection per tab. More connections,
      // still correct (Redis fan-out reaches them all).
      this.becomeLeader();
      return;
    }
    // The lock is held until this promise resolves; we never resolve, so the
    // tab keeps leadership until it's closed (then another tab acquires it).
    // The abort signal lets stop() relinquish it (StrictMode / re-mounts).
    this.lockAbort = new AbortController();
    navigator.locks
      .request(LOCK_NAME, { signal: this.lockAbort.signal }, () => {
        if (!this.started) return; // aborted/stopped before acquisition
        this.becomeLeader();
        return new Promise<void>(() => {});
      })
      .catch(() => {});
  }

  private becomeLeader(): void {
    this.isLeader = true;
    if (this.statusExpiryTimer) {
      clearTimeout(this.statusExpiryTimer);
      this.statusExpiryTimer = null;
    }
    this.reannounceTimer = setInterval(() => {
      this.channel?.postMessage({ kind: "status", live: this.live });
    }, STATUS_REANNOUNCE_MS);
    void this.connect();
  }

  // --- leader: the actual SSE connection --------------------------------

  private async connect(): Promise<void> {
    if (!this.started) return; // a reconnect timer may fire after stop()
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.closeEventSource();
    let token: string | null;
    try {
      token = await this.fetchToken();
    } catch {
      // Network / 5xx (e.g. enabled-but-misconfigured) — transient: retry with
      // backoff, polling fills the gap meanwhile.
      this.scheduleReconnect();
      return;
    }
    if (token === null) {
      // Realtime is disabled server-side — a normal answer, not an error. Stop
      // opening streams and fall back to polling. No self-re-check: the hourly
      // /config refetch flips `enabled`, which tears this client down (and
      // re-creates it on a later re-enable) — and a reload recovers instantly.
      this.setLiveAndAnnounce(false);
      return;
    }
    // Relay origin (same-origin in prod via Caddy; the relay's own port in dev).
    const url = `${getRealtimeOrigin()}${this.opts.eventsPath}?token=${encodeURIComponent(token)}`;
    // No withCredentials: the relay authenticates via the token in the URL, not
    // cookies — keeping it off avoids the stricter credentialed-CORS rules.
    const es = new EventSource(url);
    this.es = es;

    es.onopen = () => {
      this.reconnectAttempt = 0;
      this.setLiveAndAnnounce(true);
    };
    es.onerror = () => {
      // Native auto-reconnect can't refresh the (possibly expired) token, so
      // take over: drop the stream and reconnect with a fresh one.
      this.setLiveAndAnnounce(false);
      this.closeEventSource();
      this.scheduleReconnect();
    };
    const handler = (e: MessageEvent) => {
      let data: unknown = e.data;
      try {
        data = JSON.parse(e.data);
      } catch {
        /* keep raw */
      }
      this.emit({ event: e.type === "message" ? "message" : e.type, data });
    };
    es.addEventListener("message", handler);
    for (const name of this.opts.eventNames) es.addEventListener(name, handler);
  }

  private scheduleReconnect(): void {
    if (!this.started || !this.isLeader) return;
    const delay = Math.min(
      RECONNECT_BASE_MS * 2 ** this.reconnectAttempt,
      RECONNECT_MAX_MS,
    );
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => void this.connect(), delay);
  }

  private closeEventSource(): void {
    if (this.es) {
      this.es.close();
      this.es = null;
    }
  }

  /** Mint a connection token, or `null` when realtime is disabled server-side.
   *
   * Goes through the generated client (fetchAPI), so it inherits CSRF headers,
   * credentials, and the shared 401→logout flow. A 503 (enabled-but-
   * misconfigured) throws → the caller retries with backoff; a 200 null token
   * means realtime is off → the caller falls back to polling. */
  private async fetchToken(): Promise<string | null> {
    const { data } = await realtimeTokenCreate();
    return data?.token ?? null;
  }

  // --- fan-out & status -------------------------------------------------

  private setLiveAndAnnounce(live: boolean): void {
    this.setLive(live);
    this.channel?.postMessage({ kind: "status", live });
  }

  private setLive(live: boolean): void {
    if (this.live === live) return;
    this.live = live;
    this.statusListeners.forEach((fn) => fn(live));
  }

  /** Deliver to local listeners and (if leader) to the other tabs. */
  private emit(evt: RealtimeEvent): void {
    this.listeners.forEach((fn) => fn(evt));
    if (this.isLeader) {
      this.channel?.postMessage({ kind: "event", payload: evt });
    }
  }

  private onBroadcast(msg: BroadcastMessage): void {
    if (msg.kind === "event") {
      // Follower path: deliver the leader's events locally.
      if (!this.isLeader) this.listeners.forEach((fn) => fn(msg.payload));
    } else if (msg.kind === "status") {
      if (!this.isLeader) this.followerStatus(msg.live);
    } else if (msg.kind === "whois") {
      if (this.isLeader) this.channel?.postMessage({ kind: "status", live: this.live });
    }
  }

  /** Track leader-announced status; if it stops arriving, assume offline. */
  private followerStatus(live: boolean): void {
    this.setLive(live);
    if (this.statusExpiryTimer) clearTimeout(this.statusExpiryTimer);
    this.statusExpiryTimer = setTimeout(
      () => this.setLive(false),
      STATUS_EXPIRY_MS,
    );
  }
}
