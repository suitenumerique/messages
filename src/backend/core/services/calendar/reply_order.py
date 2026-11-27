"""Apply iTIP REPLYs to an event in the order of their arrival.

The apply task can retry an old reply after a newer one. The answers of the
attendee are the result of all the replies, applied in the order of arrival,
so the order of processing does not change them.
"""

import hashlib
from dataclasses import asdict, dataclass, replace

from django.conf import settings as django_settings

from icalendar import Calendar as ICalendar

from core.services.calendar.attendees import attendees_of
from core.services.calendar.reply import (
    Answer,
    ReplyOutcome,
    answers_by_key,
    apply_answers,
    reply_events,
)

# Seconds. More than the time in which the apply task retries a reply,
# about 40 minutes.
REPLY_RETRY_TIME = 3600
# Past this number of answers, the oldest replies go into the answers from
# before the replies. A later reply that is older than them is superseded.
# Each new reply applies the stored replies again, so the limit is on the
# answers: a reply can have 100 of them.
MAX_REPLY_ORDER_ANSWERS = 100
# The parameters of an attendee that a reply sets.
ANSWER_PARAMS = ("PARTSTAT", "RSVP", "SCHEDULE-STATUS")


def reply_order_ttl():
    """Return the seconds that the state of the reply order stays.

    The inbound pipeline can defer a message, and the reply keeps the time
    when the mailbox received it. The state must cover the deferral and the
    retries of the task.
    """
    return django_settings.MESSAGES_INBOUND_DEFERRAL_MAX_AGE + REPLY_RETRY_TIME


def reply_order_key(resource_url, attendee):
    """Return the cache key of the state of the reply order.

    There is one key for each attendee of each stored event.
    """
    digest = hashlib.sha256(f"{resource_url}\n{attendee}".encode()).hexdigest()
    return f"calendar_reply_order:{digest}"


@dataclass(frozen=True)
class ReceivedReply:
    """A reply that arrived, with its answers.

    ``written`` tells that the event has its answers.
    """

    at: float
    answers: tuple[Answer, ...]
    written: bool = False

    def same(self, other):
        """Tell if ``other`` is the same reply, for example a retry of it."""
        return self.at == other.at and self.answers == other.answers


@dataclass(frozen=True)
class ReplyState:
    """The replies of one attendee to one event.

    ``baseline`` holds the answer parameters of the attendee before the
    replies, for each VEVENT. ``floor`` is the time of the newest reply that
    lost against a change by someone else. ``replies`` are sorted by arrival.
    """

    baseline: dict
    floor: float | None
    replies: tuple[ReceivedReply, ...]

    def written(self):
        """Return the state with each of its replies in the event."""
        return replace(
            self, replies=tuple(replace(old, written=True) for old in self.replies)
        )

    def to_cache(self):
        """Return the state as plain data.

        A pickle of a class refers to its path, and the state stays in the
        cache over a deploy. Plain data does not break when the class changes.
        """
        return asdict(self)

    @classmethod
    def from_cache(cls, value):
        """Return the state from ``to_cache`` data, or None for other data."""
        try:
            replies = tuple(
                ReceivedReply(
                    at=old["at"],
                    answers=tuple(Answer(**answer) for answer in old["answers"]),
                    written=old["written"],
                )
                for old in value["replies"]
            )
            return cls(
                baseline=value["baseline"], floor=value["floor"], replies=replies
            )
        except (KeyError, TypeError):
            return None


class ReplyOrder:
    """Apply a reply to the stored event, in the order of arrival."""

    def __init__(self, stored, reply):
        self.stored = stored
        self.reply = reply

    def apply(self, saved):
        """Apply the reply to the stored event, in-place.

        ``saved`` is the state of the earlier replies, or None. Returns the
        new state and the outcome of each answer of the reply. A reply without
        an arrival time applies in the order of the tasks, with no state.
        """
        received_at = self.reply.received_at
        if received_at is None:
            return None, apply_answers(self.stored, self.reply)
        state = self._check(saved)
        superseded = [ReplyOutcome.SUPERSEDED] * len(self.reply.answers)
        entry = ReceivedReply(at=received_at, answers=self.reply.answers)
        # A relay or a shared mailbox can give the same reply twice, and a
        # retry gives it again.
        if any(old.same(entry) for old in state.replies):
            self._fold_stored(state, state.replies)
            return state, [ReplyOutcome.UNCHANGED] * len(self.reply.answers)
        if state.floor is not None and received_at <= state.floor:
            return state, superseded
        replies = tuple(sorted([*state.replies, entry], key=lambda old: old.at))
        state = self._prune(replace(state, replies=replies))
        outcomes = self._fold_stored(state, state.replies)
        for old, result in zip(state.replies, outcomes, strict=True):
            if old.same(entry):
                return state, result
        return state, superseded

    def _check(self, state):
        """Return the state of the earlier replies, checked against the event.

        The event must have the answers of the replies that are written, or
        of all of them: a failed write can still reach the server. Else,
        someone else changed the answers. The state then starts again from
        the event. The written replies lose against that change, and so do
        the later replies that are older than them. The replies that are not
        written stay: nobody saw their answers yet.
        """
        if state is None:
            return self._new_state(floor=None)
        if self._has_answers_of(state, state.replies):
            return state.written()
        written = tuple(old for old in state.replies if old.written)
        if self._has_answers_of(state, written):
            return state
        times = [old.at for old in written]
        if state.floor is not None:
            times.append(state.floor)
        return self._new_state(
            floor=max(times, default=None),
            replies=tuple(old for old in state.replies if not old.written),
        )

    def _new_state(self, floor, replies=()):
        """Return a state from the answers in the stored event."""
        return ReplyState(
            baseline=self._answer_params(self.stored),
            floor=floor,
            replies=replies,
        )

    def _prune(self, state):
        """Return ``state`` with its newest replies of the TTL.

        They have at most ``MAX_REPLY_ORDER_ANSWERS`` answers in all, except
        when the newest reply has more. The older replies go into the answers
        from before the replies. A later reply that is older than them is
        then superseded.
        """
        replies = state.replies
        oldest = replies[-1].at - reply_order_ttl()
        kept = [old for old in replies if old.at > oldest]
        count = 0
        for index in range(len(kept) - 1, -1, -1):
            count += len(kept[index].answers)
            if count > MAX_REPLY_ORDER_ANSWERS and index < len(kept) - 1:
                kept = kept[index + 1 :]
                break
        if len(kept) == len(replies):
            return state
        dropped = replies[: len(replies) - len(kept)]
        copy = ICalendar.from_ical(self.stored.to_ical())
        self._fold(copy, state, dropped)
        return ReplyState(
            baseline=self._answer_params(copy),
            floor=dropped[-1].at,
            replies=tuple(kept),
        )

    def _has_answers_of(self, state, replies):
        """Tell if the attendee has the answers of ``replies`` in the event."""
        copy = ICalendar.from_ical(self.stored.to_ical())
        self._fold(copy, state, replies)
        expected = answers_by_key(copy, self.reply)
        current = answers_by_key(self.stored, self.reply)
        # A VEVENT that only one of them has follows the series.
        return all(
            expected.get(key, expected.get(None)) == current.get(key, current.get(None))
            for key in expected.keys() | current.keys()
        )

    def _fold_stored(self, state, replies):
        """Give the attendee the answers of ``replies`` in the stored event.

        A VEVENT where the answer does not change keeps its answer parameters.
        So a reconfirmation request from the organizer, an RSVP=TRUE with the
        same PARTSTAT, stays. The check of the state compares only PARTSTAT.

        Returns the outcomes of each reply.
        """
        before = self._answer_params(self.stored)
        answers = answers_by_key(self.stored, self.reply)
        outcomes = self._fold(self.stored, state, replies)
        after = answers_by_key(self.stored, self.reply)
        for key, event in reply_events(self.stored, self.reply):
            if key in before and after.get(key) == answers.get(key):
                self._set_answer_params(event, before[key])
        return outcomes

    def _fold(self, cal, state, replies):
        """Give the attendee the answers of ``replies`` in ``cal``, in-place.

        The attendee first gets back the answers from before the replies. A
        VEVENT that has no such answers, for example an exception that a
        reply adds, gets the answers of the series. Then each reply applies,
        in the order of arrival.

        Returns the outcomes of each reply.
        """
        # No message went to the attendee for a new exception.
        series = [
            {name: value for name, value in params.items() if name != "SCHEDULE-STATUS"}
            for params in state.baseline.get(None, [])
        ]
        for key, event in reply_events(cal, self.reply):
            self._set_answer_params(event, state.baseline.get(key, series))
        return [
            apply_answers(
                cal, replace(self.reply, received_at=old.at, answers=old.answers)
            )
            for old in replies
        ]

    def _answer_params(self, cal):
        """Return the answer parameters of the attendee on each VEVENT, by key."""
        return {
            key: [_answer_params_of(att) for att in self._attendees(event)]
            for key, event in reply_events(cal, self.reply)
        }

    def _set_answer_params(self, event, saved):
        """Give the attendee the answer parameters ``saved``, in-place.

        The other parameters, for example a ROLE from the organizer, stay.
        """
        for att, params in zip(self._attendees(event), saved, strict=False):
            if _answer_params_of(att) == params:
                continue
            for name in ANSWER_PARAMS:
                att.params.pop(name, None)
            att.params.update(params)

    def _attendees(self, event):
        return attendees_of(event, self.reply.attendee)


def _answer_params_of(attendee):
    return {
        name: str(value)
        for name, value in attendee.params.items()
        if name in ANSWER_PARAMS
    }
