"""LocalIndex must agree with the linear scan it replaces.

Replacing a scan with an index is exactly the kind of change that looks
obviously correct and quietly is not, because the original's matching rules are
asymmetric: an exact year match accepts a title match in three different
directions, a near year match demands both titles agree, and the winner is
whichever row comes first in list order regardless of which rule matched it.

So rather than assert against hand-picked expectations, this reimplements
upstream's loop verbatim as a reference and asserts the two agree across a
generated corpus.
"""

import itertools
import random

import pytest

from resources.lib.localdb import LocalIndex


def reference_scan(local_items, title, originaltitle, year, imdbnumber=False):
    """Upstream's tmdb_check_localdb matching loop, transcribed unchanged.

    Kept deliberately close to the original -- including the `imdbnumber`-only
    id comparison -- so that any divergence this suite reports is a real
    behavioural change and not a difference in transcription.
    """
    for item in local_items:
        if imdbnumber and item["imdbnumber"] == imdbnumber:
            return item

        try:
            tmdb_year = int(year)
            item_year = int(item["year"])

            if item_year == tmdb_year:
                if (
                    item["originaltitle"] == originaltitle
                    or item["title"] == originaltitle
                    or item["title"] == title
                ):
                    return item

            elif tmdb_year in [
                item_year - 2,
                item_year - 1,
                item_year + 1,
                item_year + 2,
            ]:
                if item["title"] == title and item["originaltitle"] == originaltitle:
                    return item

        except (TypeError, ValueError):
            pass

    return None


TITLES = ["Dune", "Arrival", "Sicario", ""]
YEARS = [1998, 2000, 2001, 2002, 2003, ""]


def make_row(dbid, title, originaltitle, year, imdb=""):
    return {
        "dbid": dbid,
        "title": title,
        "originaltitle": originaltitle,
        "year": year,
        "imdbnumber": imdb,
        "tvdbid": "",
        "playcount": 0,
        "file": "",
    }


def corpus(seed):
    rng = random.Random(seed)
    rows = []
    for dbid in range(1, 25):
        rows.append(
            make_row(
                dbid,
                rng.choice(TITLES),
                rng.choice(TITLES),
                rng.choice(YEARS),
                "tt%07d" % rng.randrange(50) if rng.random() < 0.4 else "",
            )
        )
    return rows


@pytest.mark.parametrize("seed", range(12))
def test_index_matches_the_scan_it_replaced(seed):
    rows = corpus(seed)
    index = LocalIndex(rows)

    queries = itertools.product(TITLES, TITLES, YEARS, ["", "tt0000007"])

    for title, originaltitle, year, imdb in queries:
        expected = reference_scan(rows, title, originaltitle, year, imdb)
        actual = index.find(title, originaltitle, year, imdb)

        assert actual == expected, "diverged on %r" % (
            (title, originaltitle, year, imdb),
        )


def test_earliest_row_wins_even_when_a_later_one_matches_more_exactly():
    """Order beats rule strength, as it did in the scan.

    Row 1 matches only by the loose +/-2 year rule; row 2 is an exact-year
    match. Upstream returned row 1 because it came first, and an index that
    checked exact matches before loose ones would silently return row 2.
    """
    rows = [
        make_row(1, "Dune", "Dune", 2019),
        make_row(2, "Dune", "Dune", 2021),
    ]

    assert LocalIndex(rows).find("Dune", "Dune", 2021)["dbid"] == 1
    assert reference_scan(rows, "Dune", "Dune", 2021)["dbid"] == 1


def test_tvdb_ids_are_indexed_too():
    """The one deliberate divergence from upstream.

    Upstream compared the caller's id against `imdbnumber` only, but the TV
    show path passes a TVDb id -- so shows could never match by id and fell
    back to title and year. Both id fields are indexed here. IMDb ids start
    with 'tt' and TVDb ids are numeric, so widening this cannot make a movie
    match something it did not match before.
    """
    rows = [make_row(7, "Show", "Show", 2001)]
    rows[0]["tvdbid"] = "82345"

    assert LocalIndex(rows).find("Nothing", "Nothing", "", "82345")["dbid"] == 7
    assert reference_scan(rows, "Nothing", "Nothing", "", "82345") is None


def test_empty_library_finds_nothing():
    assert LocalIndex([]).find("Dune", "Dune", 2021) is None
    assert LocalIndex(None).find("Dune", "Dune", 2021) is None
