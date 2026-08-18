"""Which of a person's credits get hidden, and which must not.

The complaint that motivated this: the "hide appearances" setting was on and
"lots still get through". Upstream matched two spellings -- `himself` and
`herself`, as substrings -- and only when TMDb had filled the character in at
all, which for talking-head credits it usually has not.

So the cases below are mostly the shapes TMDb actually emits. The ones that
matter just as much are the negatives: broadening a filter is only worth doing
if it does not start eating real roles, and `Selfish` and `Ghostbuster` are the
reason word boundaries are in the pattern rather than substrings.
"""

import pytest

from conftest import set_setting

from resources.lib.person import (
    GENRE_DOCUMENTARY,
    is_appearance,
    is_documentary,
    is_posthumous,
    skip_credit,
)

DRAMA = 18
COMEDY = 35
TALK_SHOW = 10767


def credit(character=None, genres=(DRAMA,)):
    """One entry as TMDb returns it in movie_credits/tv_credits cast."""
    item = {"id": 1, "genre_ids": list(genres)}
    if character is not None:
        item["character"] = character
    return item


########################
""" is_appearance
"""

APPEARANCES = [
    "Self",
    "self",
    "SELF",
    "Himself",
    "Herself",
    "Themselves",
    "Themself",
    "Self - Host",
    "Himself - Narrator",
    "Herself - Interviewee",
    "Self (archive footage)",
    "Self (archival footage)",
    "Narrator",
    "Presenter",
    "Commentator",
    "Moderator",
    "Interviewer",
    "Hostess",
    "Various Self",
]


@pytest.mark.parametrize("character", APPEARANCES)
def test_documentary_appearances_are_recognised(character):
    assert is_appearance(credit(character, genres=(GENRE_DOCUMENTARY,)))


def test_documentary_with_no_character_is_an_appearance():
    """The case upstream could not reach.

    Its check was nested inside `if character:`, so a blank character -- how
    TMDb records most documentary self-credits -- fell straight through.
    """
    assert is_appearance(credit(None, genres=(GENRE_DOCUMENTARY,)))
    assert is_appearance(credit("", genres=(GENRE_DOCUMENTARY,)))
    assert is_appearance(credit("   ", genres=(GENRE_DOCUMENTARY,)))


REAL_ROLES = [
    "Selfish Giant",
    "Ghostbuster",
    "Hostage",
    "Selfridge",
    "Mr. Selfridge",
    "Ethan Hunt",
    "Narrator's Brother",  # possessive breaks the boundary on the right
]


@pytest.mark.parametrize("character", REAL_ROLES)
def test_real_roles_in_documentaries_are_kept(character):
    """Word boundaries, not substrings.

    Every name here contains one of the words the pattern looks for. A
    substring test -- which is what upstream did for `himself`/`herself` --
    would hide all of them.
    """
    assert not is_appearance(credit(character, genres=(GENRE_DOCUMENTARY,)))


@pytest.mark.parametrize("character", APPEARANCES + ["", None])
def test_nothing_outside_a_documentary_is_an_appearance(character):
    """`Narrator` and `Host` are ordinary parts in a film that is not a
    documentary, and a blank character is just missing data.
    """
    assert not is_appearance(credit(character, genres=(DRAMA, COMEDY)))


def test_missing_genres_is_not_a_documentary():
    """TMDb omits genre_ids on some credits; upstream indexed it directly."""
    assert not is_documentary({"id": 1})
    assert not is_appearance({"id": 1})
    assert not is_documentary({"id": 1, "genre_ids": None})


########################
""" skip_credit, and how the two settings compose
"""


def test_both_settings_off_hides_nothing():
    set_setting("filter_movies", "false")
    set_setting("filter_documentaries", "false")

    assert not skip_credit(credit("Self", genres=(GENRE_DOCUMENTARY,)))
    assert not skip_credit(credit("Ethan Hunt", genres=(GENRE_DOCUMENTARY,)))


def test_hiding_appearances_keeps_genuine_documentary_roles():
    """The whole point of keeping the two settings separate.

    Someone who acted in a documentary keeps that credit; someone who was
    interviewed for one loses it.
    """
    set_setting("filter_movies", "true")
    set_setting("filter_documentaries", "false")

    assert skip_credit(credit("Self", genres=(GENRE_DOCUMENTARY,)))
    assert not skip_credit(credit("Ethan Hunt", genres=(GENRE_DOCUMENTARY,)))


def test_hiding_documentaries_takes_the_roles_too():
    set_setting("filter_movies", "false")
    set_setting("filter_documentaries", "true")

    assert skip_credit(credit("Self", genres=(GENRE_DOCUMENTARY,)))
    assert skip_credit(credit("Ethan Hunt", genres=(GENRE_DOCUMENTARY,)))


def test_neither_setting_touches_a_drama():
    set_setting("filter_movies", "true")
    set_setting("filter_documentaries", "true")

    assert not skip_credit(credit("Ethan Hunt", genres=(DRAMA,)))
    assert not skip_credit(credit("Narrator", genres=(DRAMA,)))


def test_settings_are_read_per_call_not_per_import():
    """A regression guard for the module-constant pattern this replaced.

    person.py used to snapshot the filter flags at import. Under interpreter
    reuse that snapshot outlives the launch that took it, so flipping the
    setting stops having any effect -- silently, which is the part that makes
    it expensive.
    """
    item = credit("Self", genres=(GENRE_DOCUMENTARY,))

    set_setting("filter_movies", "false")
    assert not skip_credit(item)

    set_setting("filter_movies", "true")
    assert skip_credit(item)


def test_talk_show_genre_is_left_to_the_shows_filter():
    """A talk show is not a documentary, so these two settings ignore it;
    filter_shows owns that list and is unchanged.
    """
    set_setting("filter_movies", "true")
    set_setting("filter_documentaries", "true")

    assert not skip_credit(credit("Self", genres=(TALK_SHOW,)))


########################
""" is_posthumous
"""

DIED = "1993-10-31"  # River Phoenix, near enough


@pytest.mark.parametrize(
    "released,expected",
    [
        ("1993-11-01", True),  # the day after
        ("1994-06-10", True),
        ("2020-01-01", True),
        ("1993-10-31", False),  # the day itself is not posthumous
        ("1993-10-30", False),
        ("1991-04-05", False),
        ("1900-01-01", False),  # sort_dict's sentinel for a missing date
        ("", False),
        ("1994", False),  # partial date: not enough to judge
    ],
)
def test_posthumous_is_decided_by_the_release_date(released, expected):
    assert is_posthumous({"release_date": released}, DIED) is expected


def test_nothing_is_posthumous_for_someone_still_alive():
    """TMDb gives deathday as null, which reaches us as ''."""
    assert not is_posthumous({"release_date": "2026-01-01"}, "")
    assert not is_posthumous({"release_date": "2026-01-01"}, None)


def test_tv_credits_are_judged_on_first_air_date():
    """The TV list has not copied first_air_date into release_date yet at the
    point the filter runs, so the fallback is load-bearing.
    """
    assert is_posthumous({"first_air_date": "1994-01-01"}, DIED)
    assert not is_posthumous({"first_air_date": "1992-01-01"}, DIED)


def test_posthumous_filter_is_opt_in():
    set_setting("filter_movies", "false")
    set_setting("filter_documentaries", "false")

    set_setting("filter_posthumous", "false")
    assert not skip_credit({"release_date": "1994-06-10"}, DIED)

    set_setting("filter_posthumous", "true")
    assert skip_credit({"release_date": "1994-06-10"}, DIED)
    assert not skip_credit({"release_date": "1991-06-10"}, DIED)


def test_posthumous_filter_ignores_the_living():
    set_setting("filter_posthumous", "true")
    assert not skip_credit({"release_date": "2026-06-10"}, "")
