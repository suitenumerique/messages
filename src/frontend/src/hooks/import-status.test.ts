import { describe, expect, it } from "vitest";
import {
    DEFAULT_IMPORT_LIST_STATUSES,
    importListStatusOf,
    parseImportListStatuses,
} from "./import-status";

describe("parseImportListStatuses", () => {
    it("reads the comma-separated statuses in display order", () => {
        expect(parseImportListStatuses("completed,in_progress")).toEqual(["in_progress", "completed"]);
    });

    it("ignores unknown or repeated statuses", () => {
        expect(parseImportListStatuses("failed,active,failed")).toEqual(["failed"]);
    });

    it.each([undefined, "", "all", 42])("falls back to the default statuses for %s", (value) => {
        expect(parseImportListStatuses(value)).toEqual(DEFAULT_IMPORT_LIST_STATUSES);
    });
});

describe("importListStatusOf", () => {
    it.each([
        [{ status: "running", is_active: true }, "in_progress"],
        [{ status: "completed", is_active: true }, "in_progress"],
        [{ status: "failed", is_active: true }, "failed"],
        [{ status: "failed", is_active: false }, "failed"],
        [{ status: "completed", is_active: false }, "completed"],
    ])("files %o as %s", (run, expected) => {
        expect(importListStatusOf(run)).toBe(expected);
    });
});
