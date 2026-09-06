/**
 * `crypto.randomUUID` is a secure-context API: it is undefined when the app is
 * served over plain HTTP on a non-localhost origin (the e2e stack behind
 * `http://proxy`, a LAN preview), where calling it throws a TypeError.
 * `crypto.getRandomValues` carries no such restriction, so it backs the
 * fallback below.
 */
export const randomUUID = (): string => {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();

    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 1
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

export default randomUUID;
