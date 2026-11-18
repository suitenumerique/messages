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
        origin && data.timezone === origin.timeZone && value === input
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
    if (/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) return Date.parse(value);
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
