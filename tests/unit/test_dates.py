"""Dates, after arrow was taken out of the info dialog's import path.

arrow cost 154 ms to import inside Kodi's interpreter and was paid on every
launch of the dialog. These functions are what it was doing: parse two shapes,
call strftime. The tests are here because a date helper that silently returns
its input on failure is exactly the kind of thing that rots unnoticed.

Kodi's xbmc.getRegion returns strftime format strings ('%Y-%m-%d' for
dateshort, measured on Kodi 21.3), which is why strftime is fed directly.
"""

import datetime

import pytest

from resources.lib.helper import (
    date_delta,
    date_format,
    date_weekday,
    date_year,
    parse_date,
    utc_to_local,
)

########################
""" parse_date
"""


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2008-01-22", datetime.datetime(2008, 1, 22)),
        ("1956-07-09", datetime.datetime(1956, 7, 9)),
        ("2026", datetime.datetime(2026, 1, 1)),
        ("2026-08-18T20:00:00", datetime.datetime(2026, 8, 18, 20, 0)),
    ],
)
def test_parses_the_shapes_tmdb_sends(value, expected):
    assert parse_date(value) == expected


def test_parses_trakts_zulu_timestamps():
    """Trakt sends '...Z', which fromisoformat rejected before Python 3.11 --
    so Kodi 20 would have choked on exactly the field Next aired is built on.
    """
    parsed = parse_date("2026-08-18T20:00:00.000Z")
    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 8, 18, 20)
    assert parsed.utcoffset() == datetime.timedelta(0)


@pytest.mark.parametrize("value", ["", None, "not a date", "18/08/2026", "0000"])
def test_unparseable_values_are_none_rather_than_raising(value):
    assert parse_date(value) is None


########################
""" date_year / date_format
"""


def test_date_year():
    assert date_year("1956-07-09") == "1956"
    assert date_year("") == ""
    assert date_year(None) is None
    assert date_year("rubbish") == ""


def test_date_format_uses_an_explicit_scheme_when_given():
    """The OMDb caller passes one. It used to pass `scheme=`, which was not a
    parameter at all, so the call raised TypeError every time -- taking the
    whole OMDb parse with it, three requests deep, caching nothing.
    """
    assert date_format("2008-01-22", scheme="%d %b %Y") == "22 Jan 2008"


def test_date_format_returns_the_input_when_it_cannot_parse():
    assert date_format("rubbish", scheme="%d %b %Y") == "rubbish"
    assert date_format("") == ""


########################
""" date_delta
"""


def test_date_delta_counts_forward_and_back():
    today = datetime.date.today()
    assert date_delta(today.isoformat()).days == 0
    assert date_delta((today + datetime.timedelta(days=10)).isoformat()).days == 10
    assert date_delta((today - datetime.timedelta(days=3)).isoformat()).days == -3


def test_date_delta_treats_an_unparseable_date_as_the_far_future():
    """Upstream let arrow raise here and no caller catches it, so one bad date
    took down the whole page instead of misfiltering one item. The callers'
    own fallback for "no date" is a year-2900 sentinel, so that is what an
    unreadable one becomes.
    """
    assert date_delta("rubbish").days > 300000


########################
""" date_weekday
"""


def test_date_weekday_accepts_the_types_its_callers_hold():
    """widgets.py still holds arrow objects and passes them straight in, so
    anything with a .date() has to work alongside strings and dates.
    """
    monday = datetime.date(2026, 8, 17)

    class ArrowLike:
        def date(self):
            return monday

    assert date_weekday("2026-08-17")[1] == 0
    assert date_weekday(monday)[1] == 0
    assert date_weekday(datetime.datetime(2026, 8, 17, 12))[1] == 0
    assert date_weekday(ArrowLike())[1] == 0


def test_date_weekday_of_an_unparseable_string_is_empty():
    assert date_weekday("rubbish") == ("", "")


def test_date_weekday_with_no_argument_is_today():
    assert date_weekday()[1] == datetime.date.today().weekday()


########################
""" utc_to_local
"""


def test_utc_to_local_converts_rather_than_relabelling():
    date_str, time_str = utc_to_local("2026-08-18T20:00:00.000Z")
    expected = datetime.datetime(
        2026, 8, 18, 20, tzinfo=datetime.timezone.utc
    ).astimezone()

    assert date_str == expected.strftime("%Y-%m-%d")
    assert time_str == expected.strftime("%H:%M")


def test_utc_to_local_survives_a_missing_timestamp():
    """nextaired feeds this straight from a Trakt payload."""
    assert utc_to_local("") == ("", "")
    assert utc_to_local(None) == ("", "")
