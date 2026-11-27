import { readStoredIds, spanOf, timelineScale, type ConflictInfo } from "./conflict-check";

const HOUR = 60 * 60 * 1000;
const at = (iso: string) => new Date(iso).getTime();

const conflict = (overrides: Partial<ConflictInfo>): ConflictInfo => ({
    summary: "Busy",
    start: "2026-11-19T10:30:00Z",
    end: "2026-11-19T12:00:00Z",
    calendar_name: "Acme Support",
    calendar_id: "https://caldav.example.com/support/",
    calendar_color: null,
    ...overrides,
});

describe("spanOf", () => {
    it("returns the start and end of a timed event", () => {
        expect(spanOf(conflict({}))).toEqual({
            start: at("2026-11-19T10:30:00Z"),
            end: at("2026-11-19T12:00:00Z"),
        });
    });

    it("returns null for an all-day event or missing times", () => {
        expect(spanOf(conflict({ all_day: true }))).toBeNull();
        expect(spanOf(conflict({ end: null }))).toBeNull();
    });
});

describe("timelineScale", () => {
    it("covers whole hours around the spans, at least two of them", () => {
        const scale = timelineScale([
            { start: at("2026-11-19T10:30:00Z"), end: at("2026-11-19T11:15:00Z") },
        ]);
        expect(scale.from).toBe(at("2026-11-19T10:00:00Z"));
        expect(scale.to).toBe(at("2026-11-19T12:00:00Z"));
        expect(scale.ticks).toEqual([scale.from, scale.from + HOUR, scale.from + 2 * HOUR]);
    });

    it("spaces ticks out over a long range", () => {
        const scale = timelineScale([
            { start: at("2026-11-19T08:00:00Z"), end: at("2026-11-19T16:00:00Z") },
        ]);
        expect(scale.ticks[1] - scale.ticks[0]).toBe(2 * HOUR);
    });

    it("has no ticks when nothing has a time", () => {
        expect(timelineScale([null]).ticks).toEqual([]);
    });
});

describe("readStoredIds", () => {
    afterEach(() => window.localStorage.clear());

    it("reads the ids stored under the key", () => {
        window.localStorage.setItem("k", JSON.stringify(["a", 1, "b"]));
        expect(readStoredIds("k")).toEqual(["a", "b"]);
    });

    it("returns null without a key, a value, or valid JSON", () => {
        expect(readStoredIds(null)).toBeNull();
        expect(readStoredIds("missing")).toBeNull();
        window.localStorage.setItem("k", "{oops");
        expect(readStoredIds("k")).toBeNull();
    });
});
