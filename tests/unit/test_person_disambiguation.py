"""When the "choose a person" dialog can be skipped.

Opening an actor asked you to disambiguate about a third of the time, because
the only condition was `len(result) > 1` and TheMovieDB's person search is fuzzy
over a database full of near-duplicate stubs.

Every fixture below is a real TheMovieDB response, trimmed to the fields the
rule reads. They are the cases that actually occur — a stub with a misspelled
name, a stub sharing the name exactly, and the one where the real person is
himself obscure.
"""

import pytest

from resources.lib.tmdb import POPULARITY_DOMINANCE, unambiguous_person


def person(name, popularity):
    return {
        "id": abs(hash((name, popularity))) % 100000,
        "name": name,
        "popularity": popularity,
    }


########################
""" Real searches that should stop asking
"""

MARLON_BRANDO = [
    person("Marlon Brando", 3.75),
    person("Marlon Brando sr.", 0.16),
    person("Marlon Brandon", 0.24),
]
JESSICA_ALBA = [
    person("Jessica Alba", 5.69),
    person("Jessica Albano", 0.19),
    person("Jessica Albano", 0.21),
    person("Jessica Albarn", 0.23),
]
CLIVE_OWEN = [person("Clive Owen", 4.25), person("Clive Owen", 0.24)]
MICHAEL_MADSEN = [
    person("Michael Madsen", 2.50),
    person("Michael Madsen", 0.20),
    person("Jacob Michael Madsen", 0.21),
    person("Jon Madsen", 0.36),
]
CARTER_BURWELL = [person("Carter Burwell", 0.93), person("Carter Burwell", 0.22)]


@pytest.mark.parametrize(
    "query,results",
    [
        ("Marlon Brando", MARLON_BRANDO),
        ("Jessica Alba", JESSICA_ALBA),
        ("Clive Owen", CLIVE_OWEN),
        ("Michael Madsen", MICHAEL_MADSEN),
        ("Carter Burwell", CARTER_BURWELL),
    ],
)
def test_the_obvious_person_is_picked(query, results):
    chosen = unambiguous_person(results, query)

    assert chosen is not None
    assert chosen is results[0]


def test_carter_burwell_is_the_tightest_case_the_threshold_has_to_clear():
    """0.93 against 0.22 is 4.2x — the narrowest real margin measured, and the
    reason the threshold is 4 rather than a rounder 5.
    """
    assert 0.93 >= 0.22 * POPULARITY_DOMINANCE
    assert unambiguous_person(CARTER_BURWELL, "Carter Burwell") is not None


def test_a_misspelled_stub_is_discarded_rather_than_weighed():
    """`Marlon Brandon` is not `Marlon Brando`. The rule does not have to decide
    which is more popular, because only exact names are candidates at all.
    """
    chosen = unambiguous_person(MARLON_BRANDO, "Marlon Brando")

    assert chosen["name"] == "Marlon Brando"
    assert chosen["popularity"] == 3.75


########################
""" Cases that must keep asking
"""


def test_two_equally_known_people_of_the_same_name_still_ask():
    """The case the whole rule exists to not get wrong."""
    twins = [person("John Williams", 3.10), person("John Williams", 2.80)]

    assert unambiguous_person(twins, "John Williams") is None


def test_a_name_that_matches_nothing_exactly_still_asks():
    """If TheMovieDB spells the person differently — an accent, a `Jr.` — we do
    not know which was meant, so the dialog is right.
    """
    results = [person("Gérard Depardieu", 3.0), person("Gerard Depardieu Jr.", 0.2)]

    assert unambiguous_person(results, "Gerard Depardieu") is None


def test_just_under_the_threshold_still_asks():
    close = [person("Someone", 1.00), person("Someone", 0.30)]

    assert unambiguous_person(close, "Someone") is None


def test_no_query_still_asks():
    """The external-id path reaches the same code with no query at all."""
    assert unambiguous_person(CLIVE_OWEN, None) is None
    assert unambiguous_person(CLIVE_OWEN, "") is None


def test_empty_results_are_handled():
    assert unambiguous_person([], "Clive Owen") is None
    assert unambiguous_person(None, "Clive Owen") is None


########################
""" Shapes TheMovieDB actually sends
"""


def test_names_are_compared_ignoring_case_and_spacing():
    results = [person("CLIVE   OWEN", 4.25), person("Clive Owen", 0.24)]

    assert unambiguous_person(results, "  clive owen ") is results[0]


def test_a_missing_or_malformed_popularity_does_not_raise():
    results = [
        {"id": 1, "name": "Someone"},
        {"id": 2, "name": "Someone", "popularity": "lots"},
    ]

    assert unambiguous_person(results, "Someone") is None


def test_a_single_result_is_never_this_functions_problem():
    """find_id only consults the rule when there is more than one result, but
    the rule should still answer sensibly if handed one.
    """
    assert unambiguous_person([person("Clive Owen", 4.25)], "Clive Owen") is not None
