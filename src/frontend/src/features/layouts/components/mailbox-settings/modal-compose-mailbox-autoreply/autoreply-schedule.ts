import { DateHelper } from "@/features/utils/date-helper";

export type AutoreplyScheduleValues = {
    schedule_type: "always" | "date_range" | "recurring_weekly";
    start_at?: string;
    end_at?: string;
    interval_start_day?: string;
    interval_start_time?: string;
    interval_end_day?: string;
    interval_end_time?: string;
    timezone?: string;
};

/**
 * What the form was opened with, to send unchanged bounds back as stored.
 */
export type AutoreplyScheduleOrigin = {
    metadata?: Record<string, unknown>;
    timeZone: string;
    startInput: string;
    endInput: string;
};

/** ``HH:mm`` as ``HH:mm:ss``: a datetime-local input drops zero seconds. */
const withSeconds = (value: string | undefined) => value?.replace(/T(\d{2}:\d{2})$/, "T$1:00");

/**
 * The date range bounds to save. A bound left as it was shown, in the same
 * zone, is sent back as stored: a value with an offset in a repeated hour
 * (the second 02:30) would otherwise come back as a wall-clock time, read as
 * the first one, and move an hour. An edited bound is a wall-clock time in
 * the zone, read by the backend as ``fromWallClock`` does.
 */
export const effectiveBounds = (
    data: AutoreplyScheduleValues,
    origin?: AutoreplyScheduleOrigin,
): { start_at: unknown; end_at: unknown } => {
    const keep = (value: string | undefined, input: string | undefined, key: "start_at" | "end_at") =>
        origin && data.timezone === origin.timeZone && withSeconds(value) === withSeconds(input)
            ? origin.metadata?.[key] ?? value
            : value;
    return {
        start_at: keep(data.start_at, origin?.startInput, "start_at"),
        end_at: keep(data.end_at, origin?.endInput, "end_at"),
    };
};

/** The instant of a bound: with an offset as given, otherwise in ``timeZone``. */
const boundInstant = (value: unknown, timeZone: string): number => {
    if (typeof value !== "string") return NaN;
    if (DateHelper.hasOffset(value)) return DateHelper.offsetInstant(value);
    return DateHelper.fromWallClock(value, timeZone);
};

/**
 * Whether the range starts before it ends, comparing the bounds that will
 * be saved (``effectiveBounds``) as the backend does. True when either
 * cannot be read: the backend then answers.
 */
export const startsBeforeEnd = (
    data: AutoreplyScheduleValues,
    timeZone: string,
    origin?: AutoreplyScheduleOrigin,
): boolean => {
    const { start_at, end_at } = effectiveBounds(data, origin);
    const startInstant = boundInstant(start_at, timeZone);
    const endInstant = boundInstant(end_at, timeZone);
    if (Number.isNaN(startInstant) || Number.isNaN(endInstant)) return true;
    return startInstant < endInstant;
};

/**
 * The time zone options: this browser's zones, plus UTC (the backend's
 * default, which V8 leaves out) and ``current``. A zone the backend accepts
 * may be listed here under another name (V8 lists "Asia/Calcutta", not
 * "Asia/Kolkata"): without it, the select would show empty.
 */
export const timeZoneOptions = (zones: readonly string[], current: string) =>
    Array.from(new Set(["UTC", ...zones, current])).map((tz) => ({
        label: tz.replace(/_/g, " "),
        value: tz,
    }));

type IntervalFormValues = Pick<
    AutoreplyScheduleValues,
    "interval_start_day" | "interval_start_time" | "interval_end_day" | "interval_end_time"
>;

/**
 * The form values of a stored weekly interval. "24:00", stored before the
 * backend refused it, is not a time input value: it becomes 00:00 of the
 * next day, which the backend reads alike, or 23:59 for an end that would
 * then meet the start (a whole week).
 */
export const intervalFormValues = (interval?: Record<string, unknown>): IntervalFormValues => {
    const day = (value: unknown) => (value != null ? String(value) : "");
    const nextDay = (value: unknown) => String((Number(value) % 7) + 1);
    const values: IntervalFormValues = {
        interval_start_day: day(interval?.start_day),
        interval_start_time: (interval?.start_time as string) ?? "",
        interval_end_day: day(interval?.end_day),
        interval_end_time: (interval?.end_time as string) ?? "",
    };
    if (values.interval_start_time === "24:00") {
        values.interval_start_day = nextDay(interval?.start_day);
        values.interval_start_time = "00:00";
    }
    if (values.interval_end_time === "24:00") {
        const endDay = nextDay(interval?.end_day);
        const meetsStart = endDay === values.interval_start_day && values.interval_start_time === "00:00";
        values.interval_end_day = meetsStart ? values.interval_end_day : endDay;
        values.interval_end_time = meetsStart ? "23:59" : "00:00";
    }
    return values;
};

/** The metadata to save, with the date range bounds of ``effectiveBounds``. */
export const buildAutoreplyMetadata = (
    data: AutoreplyScheduleValues,
    origin?: AutoreplyScheduleOrigin,
): Record<string, unknown> => {
    const meta: Record<string, unknown> = { schedule_type: data.schedule_type };
    if (data.schedule_type === "date_range") {
        Object.assign(meta, effectiveBounds(data, origin));
        if (data.timezone) meta.timezone = data.timezone;
    }
    if (data.schedule_type === "recurring_weekly") {
        meta.intervals = [
            {
                start_day: Number(data.interval_start_day),
                start_time: data.interval_start_time,
                end_day: Number(data.interval_end_day),
                end_time: data.interval_end_time,
            },
        ];
        if (data.timezone) meta.timezone = data.timezone;
    }
    return meta;
};
