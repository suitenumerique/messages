import { describe, expect, it } from "vitest";
import { DateHelper } from "@/features/utils/date-helper";
import { buildAutoreplyMetadata, startsBeforeEnd } from "./autoreply-schedule";

describe("startsBeforeEnd", () => {
    const range = (start_at: string, end_at: string, timezone = "Europe/Paris") => ({
        schedule_type: "date_range" as const,
        start_at,
        end_at,
        timezone,
    });

    it("compares as instants in the zone, whatever the precision", () => {
        expect(startsBeforeEnd(range("2026-06-01T10:00", "2026-06-01T10:00:00"), "Europe/Paris")).toBe(false);
        expect(startsBeforeEnd(range("2026-06-01T10:00", "2026-06-01T10:01"), "Europe/Paris")).toBe(true);
    });

    it("reads a skipped hour as the backend does", () => {
        // 02:30 does not exist on 2026-03-29 in Paris: read as 01:30Z (CET),
        // after 03:10 CEST (01:10Z).
        expect(startsBeforeEnd(range("2026-03-29T02:30", "2026-03-29T03:10"), "Europe/Paris")).toBe(false);
    });

    it("leaves unreadable values to the backend", () => {
        expect(startsBeforeEnd(range("garbage", "2026-06-01T10:00", "UTC"), "UTC")).toBe(true);
    });

    describe("in the repeated hour, with stored offsets", () => {
        // The first 02:30 of 2026-10-25 in Paris (00:30Z), then the second (01:30Z).
        const metadata = {
            schedule_type: "date_range",
            start_at: "2026-10-25T02:30:00+02:00",
            end_at: "2026-10-25T02:30:00+01:00",
            timezone: "Europe/Paris",
        };
        const origin = {
            metadata,
            timeZone: "Europe/Paris",
            startInput: DateHelper.toWallClock(metadata.start_at, "Europe/Paris")!,
            endInput: DateHelper.toWallClock(metadata.end_at, "Europe/Paris")!,
        };

        it("accepts the unchanged range, shown as the same wall-clock time", () => {
            expect(origin.startInput).toBe(origin.endInput);
            expect(startsBeforeEnd(range(origin.startInput, origin.endInput), "Europe/Paris", origin)).toBe(true);
        });

        it("compares an edited start with the stored end", () => {
            // The first 02:45 (00:45Z) is before the stored end (01:30Z).
            expect(startsBeforeEnd(range("2026-10-25T02:45", origin.endInput), "Europe/Paris", origin)).toBe(true);
            // 03:00 CET (02:00Z) is after it.
            expect(startsBeforeEnd(range("2026-10-25T03:00", origin.endInput), "Europe/Paris", origin)).toBe(false);
        });

        it("rejects a new range whose bounds read as the same instant", () => {
            expect(startsBeforeEnd(range(origin.startInput, origin.endInput), "Europe/Paris")).toBe(false);
        });
    });
});

describe("buildAutoreplyMetadata", () => {
    // The second 02:30 of 2026-10-25 in Paris (CET).
    const storedEnd = "2026-10-25T02:30:00+01:00";
    const metadata = {
        schedule_type: "date_range",
        start_at: "2026-10-20T09:00:00",
        end_at: storedEnd,
        timezone: "Europe/Paris",
    };
    const origin = {
        metadata,
        timeZone: "Europe/Paris",
        startInput: DateHelper.toWallClock(metadata.start_at, "Europe/Paris")!,
        endInput: DateHelper.toWallClock(storedEnd, "Europe/Paris")!,
    };

    it("sends unchanged bounds back as stored (the end instant does not move)", () => {
        expect(origin.endInput).toBe("2026-10-25T02:30:00");
        const built = buildAutoreplyMetadata(
            {
                schedule_type: "date_range",
                start_at: origin.startInput,
                end_at: origin.endInput,
                timezone: "Europe/Paris",
            },
            origin,
        );
        expect(built.end_at).toBe(storedEnd);
        expect(new Date(built.end_at as string).toISOString()).toBe("2026-10-25T01:30:00.000Z");
    });

    it("sends an edited bound as its wall-clock time", () => {
        const built = buildAutoreplyMetadata(
            {
                schedule_type: "date_range",
                start_at: origin.startInput,
                end_at: "2026-10-26T10:00",
                timezone: "Europe/Paris",
            },
            origin,
        );
        expect(built.start_at).toBe(metadata.start_at);
        expect(built.end_at).toBe("2026-10-26T10:00");
    });

    it("sends the shown values when the zone changes", () => {
        const built = buildAutoreplyMetadata(
            {
                schedule_type: "date_range",
                start_at: origin.startInput,
                end_at: origin.endInput,
                timezone: "UTC",
            },
            origin,
        );
        expect(built.end_at).toBe(origin.endInput);
        expect(built.timezone).toBe("UTC");
    });

    it("builds a new template's metadata without an origin", () => {
        expect(
            buildAutoreplyMetadata({
                schedule_type: "recurring_weekly",
                interval_start_day: "1",
                interval_start_time: "09:00",
                interval_end_day: "5",
                interval_end_time: "17:00",
                timezone: "UTC",
            }),
        ).toEqual({
            schedule_type: "recurring_weekly",
            intervals: [{ start_day: 1, start_time: "09:00", end_day: 5, end_time: "17:00" }],
            timezone: "UTC",
        });
    });
});
