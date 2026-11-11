"""Parsing of SMTP command lines. Pure functions: no I/O, no state.

Only the shape of a command is decided here. Whether an address is one we
accept is :mod:`pymta.address`'s job, and what to do with a command is
:mod:`pymta.smtp_protocol`'s.
"""

from __future__ import annotations


class CommandSyntaxError(Exception):
    """A command line that does not parse."""


class ArgumentNotAscii(CommandSyntaxError):
    """Bytes over 127 in an argument while SMTPUTF8 is switched off."""


def split_command(line: bytes, *, utf8: bool) -> tuple[str, str | None]:
    """Split a command line, line ending already removed, into verb and argument.

    The verb is upper-cased ASCII. The argument is None when there is none.
    With ``utf8`` it is decoded leniently: a byte that is not UTF-8 becomes a
    lone surrogate, which the address validation refuses with its own reply.
    """
    verb_bytes, _, arg_bytes = line.partition(b" ")
    try:
        verb = verb_bytes.decode("ascii").upper()
    except UnicodeDecodeError as error:
        raise CommandSyntaxError("verb is not ASCII") from error
    if not verb:
        raise CommandSyntaxError("no verb")
    if not arg_bytes:
        return verb, None
    arg_bytes = arg_bytes.strip()
    if utf8:
        return verb, arg_bytes.decode("utf-8", "surrogateescape")
    try:
        return verb, arg_bytes.decode("ascii")
    except UnicodeDecodeError as error:
        raise ArgumentNotAscii from error


def strip_keyword(keyword: str, arg: str | None) -> str | None:
    """What follows ``FROM:`` or ``TO:``, or None when the keyword is not there."""
    if arg is None or arg[: len(keyword)].upper() != keyword:
        return None
    return arg[len(keyword) :].strip()


def _find_unquoted(char: str, text: str, start: int) -> int | None:
    """Index of the first ``char`` outside a quoted string, else ``len(text)``.

    None when a quoted string is still open at the end.
    """
    quoted = False
    position = start
    while position < len(text):
        current = text[position]
        if quoted:
            if current == "\\":
                position += 1
            elif current == '"':
                quoted = False
        elif current == '"':
            quoted = True
        elif current == char:
            return position
        position += 1
    return None if quoted else len(text)


def split_path(text: str) -> tuple[str, str] | None:
    """Split ``<path> parameters`` into the path and the parameter text.

    The brackets are optional, as Postfix has it; without them the path ends
    at the first space. Inside a quoted local-part neither ``>`` nor a space
    ends anything. Returns None when there is no path, or when the brackets
    or the quotes never close.

    The path comes back without its brackets, ``<>`` excepted (the null
    sender), and without a source route: RFC 5321 section 4.1.1.3 says to
    accept ``<@a,@b:user@c>`` and ignore the route.
    """
    if not text.startswith("<"):
        end = _find_unquoted(" ", text, 0)
        # ``not end``: an open quote, or no path at all before the space.
        return (text[:end], text[end:]) if end else None
    end = _find_unquoted(">", text, 1)
    if end is None or end == len(text):
        return None
    path, rest = text[1:end], text[end + 1 :]
    if path == "":
        return "<>", rest
    path = path.strip(" \t")
    if path.startswith("@") and ":" in path:
        path = path.partition(":")[2]
    if path == "":
        return None
    return path, rest


def parse_parameters(text: str) -> dict[str, str | None] | None:
    """ESMTP parameters as ``{NAME: value}``, the value None for a bare keyword.

    Names are upper-cased, values kept as sent. Returns None when one of them
    is not ``NAME`` or ``NAME=value`` (RFC 5321 section 4.1.2), or comes
    twice: which of the two values counts would be ours to guess.
    """
    parameters: dict[str, str | None] = {}
    for item in text.split():
        name, equals, value = item.partition("=")
        name = name.upper()
        if not (name.isascii() and name.isalnum()) or (equals and not value) or name in parameters:
            return None
        parameters[name] = value if equals else None
    return parameters
