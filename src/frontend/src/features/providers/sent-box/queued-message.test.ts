import { describe, expect, it } from "vitest";

import { getDeliveryToast, SendTaskResult } from "./queued-message";

// The toast only ever renders `t(key)`, so an identity function keeps the
// assertions on the key that was chosen rather than on a translation.
const t = (key: string) => key;

describe("getDeliveryToast", () => {
    it("celebrates only a fully delivered send", () => {
        const toast = getDeliveryToast({ delivery_status: "completed" }, t);

        expect(toast.message).toBe("Message sent successfully");
        expect(toast.type).toBe("info");
        expect(toast.chime).toBe(true);
    });

    it.each([
        ["partial", "warning"],
        ["pending", "warning"],
        ["cancelled", "warning"],
        ["failed", "error"],
    ] as const)(
        "does not report %s as a success",
        (delivery_status, expectedType) => {
            const toast = getDeliveryToast({ delivery_status }, t);

            expect(toast.type).toBe(expectedType);
            expect(toast.message).not.toBe("Message sent successfully");
            // The chime is the "it's gone" cue; anything short of full
            // delivery must not play it.
            expect(toast.chime).toBe(false);
        },
    );

    it("names the total failure distinctly from a crashed task", () => {
        const toast = getDeliveryToast({ delivery_status: "failed" }, t);

        // "The message could not be sent." is the FAILURE-state toast, for a
        // task that raised. This one means the task ran and nobody got it.
        expect(toast.message).toBe(
            "The message could not be delivered to any recipient.",
        );
    });

    it.each([
        ["a result with no delivery status", {}],
        ["a null result", null],
        // A worker from before the aggregated status shipped.
        ["an unrecognised status", { delivery_status: "something-new" }],
    ])("keeps the optimistic toast for %s", (_label, result) => {
        const toast = getDeliveryToast(result as SendTaskResult | null, t);

        expect(toast.message).toBe("Message sent successfully");
        expect(toast.chime).toBe(true);
    });
});
