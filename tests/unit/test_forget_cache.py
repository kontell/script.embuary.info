"""Forgetting the actors this add-on has remembered.

The settings button behind this deletes rows out of simplecache's own SQLite
database, because simplecache offers no delete of any kind. That database is
shared with every other add-on on the box, so the interesting question is not
whether the right rows go but whether the wrong ones stay.

The fixtures below are the four shapes a real database holds at once: entries
of ours that match, entries of ours that do not, entries of ours written by an
older version or in another language, and somebody else's entries that happen
to contain the same fragment.
"""

import sqlite3

import pytest

import conftest

from resources.lib import helper
from resources.lib.tmdb import PERSON_ID_KEY

OURS = "script.embuary.info"
THEIRS = "plugin.video.something"

ROWS = [
    "%s_2.1.0_enGB_person_id_clive owen" % OURS,
    "%s_2.1.0_enGB_person_id_jessica alba" % OURS,
    # An earlier version and another language. The prefix carries both, so
    # neither of these would ever be read again -- but neither would be
    # collected until it expired either, and both are ours, so both go.
    "%s_2.0.0_deDE_person_id_clive owen" % OURS,
    "%s_2.1.0_enGB_movie550" % OURS,
    "%s_2.1.0_enGB_person3924" % OURS,
    # A key that SQL LIKE would call a match: `_` is a single-character
    # wildcard there, so 'person_id_%' matches this too.
    "%s_2.1.0_enGB_personXidY_trap" % OURS,
    "%s_person_id_clive owen" % THEIRS,
]

DOOMED = {r for r in ROWS if r.startswith(OURS) and PERSON_ID_KEY in r}


@pytest.fixture
def cache(tmp_path):
    """A simplecache database with the rows above, shaped like the real one."""
    dbfile = tmp_path / "simplecache.db"
    connection = sqlite3.connect(dbfile, isolation_level=None)
    connection.execute("""CREATE TABLE IF NOT EXISTS simplecache(
        id TEXT UNIQUE, expires INTEGER, data TEXT, checksum INTEGER)""")
    connection.executemany(
        "INSERT INTO simplecache(id, expires, data, checksum) VALUES (?, 0, '1', 0)",
        [(row,) for row in ROWS],
    )
    connection.close()

    conftest.SIMPLECACHE_PROFILE["path"] = str(tmp_path)
    yield dbfile
    conftest.SIMPLECACHE_PROFILE["path"] = ""


def remaining(dbfile):
    connection = sqlite3.connect(dbfile)
    try:
        return {row[0] for row in connection.execute("SELECT id FROM simplecache")}
    finally:
        connection.close()


def test_the_remembered_actors_go(cache):
    assert helper.forget_cache(PERSON_ID_KEY) == len(DOOMED)
    assert not remaining(cache) & DOOMED


def test_nothing_else_of_ours_goes_with_them(cache):
    """The cached pages sit in the same table under the same prefix. Clearing
    the remembered names is meant to cost one TMDb search per actor, not a
    rebuild of every page the user has opened.
    """
    helper.forget_cache(PERSON_ID_KEY)

    assert "%s_2.1.0_enGB_movie550" % OURS in remaining(cache)
    assert "%s_2.1.0_enGB_person3924" % OURS in remaining(cache)


def test_another_addons_entry_is_never_touched(cache):
    """Same fragment, different add-on. The database is shared, so this is the
    row that decides whether the match is safe at all.
    """
    helper.forget_cache(PERSON_ID_KEY)

    assert "%s_person_id_clive owen" % THEIRS in remaining(cache)


def test_the_fragment_is_matched_literally(cache):
    """Not as SQL LIKE, where `_` is a single-character wildcard. Every key
    here is full of underscores, so a LIKE would reach well past what was
    asked for.
    """
    helper.forget_cache(PERSON_ID_KEY)

    assert "%s_2.1.0_enGB_personXidY_trap" % OURS in remaining(cache)


def test_a_fragment_that_matches_nothing_removes_nothing(cache):
    before = remaining(cache)

    assert helper.forget_cache("nobody_has_this") == 0
    assert remaining(cache) == before


def test_a_missing_database_is_not_an_error(tmp_path):
    """A box where nothing has been cached yet. The button still has to answer
    rather than raise, and must not leave an empty database behind.
    """
    conftest.SIMPLECACHE_PROFILE["path"] = str(tmp_path)

    try:
        assert helper.forget_cache(PERSON_ID_KEY) == 0
        assert not (tmp_path / "simplecache.db").exists()
    finally:
        conftest.SIMPLECACHE_PROFILE["path"] = ""
