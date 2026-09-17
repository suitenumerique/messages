/**
 * `crypto.randomUUID` is a secure-context API: it is undefined when the app is
 * served over plain HTTP on a non-localhost origin (the e2e stack behind
 * `http://proxy`, a LAN preview), where calling it throws a TypeError.
 * `crypto.getRandomValues` carries no such restriction, so it backs the
 * fallback below.
 */
const uuidFromRandomBytes = (): string => {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 1
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

export const randomUUID = (): string =>
    typeof crypto.randomUUID === "function" ? crypto.randomUUID() : uuidFromRandomBytes();

/**
 * Same gap, but in third-party code we cannot route through `randomUUID`:
 * `@gouvfr-lasuite/ui-components`' FileUploader ids every picked file with a
 * bare `crypto.randomUUID()`. Outside a secure context that call throws inside
 * its change handler, so `onAddFiles` never fires and the selected file never
 * reaches the form — silently, since the throw stays in the event handler.
 * Installing the fallback on `crypto` itself covers those call sites too.
 *
 * It installs `uuidFromRandomBytes`, not `randomUUID`: the latter reads back
 * `crypto.randomUUID` and would call itself once installed.
 */
export const installRandomUUIDPolyfill = (): void => {
    if (typeof crypto.randomUUID === "function") return;

    Object.defineProperty(crypto, "randomUUID", {
        value: uuidFromRandomBytes,
        configurable: true,
        writable: true,
    });
};

export default randomUUID;
