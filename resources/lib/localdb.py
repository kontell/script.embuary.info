#!/usr/bin/python
# coding: utf-8

########################

import json
import sys

from resources.lib.helper import *

########################


class LocalIndex:
    """Indexed view of one local library list, for matching TMDb items against it.

    Upstream re-scanned the whole list for every TMDb item drawn on a page --
    every cast credit, every similar title, every collection member. A page
    carries roughly 45 such items and a real library runs to thousands of
    rows, so that is hundreds of thousands of comparisons per page, each with
    an int() conversion inside a try/except, all on the thread the UI is
    waiting on.

    Match ordering is preserved exactly rather than merely approximately. The
    original returned the first row in list order that satisfied *either* the
    exact-year rule or the loose one, so an early loose match beat a later
    exact match. Positions are carried through the index and the lowest wins,
    which keeps that behaviour instead of quietly preferring exact matches.
    """

    def __init__(self, items):
        self.items = items or []
        """ Keyed by any external id the row carries. Upstream only ever
            compared against `imdbnumber`, which meant TV shows -- whose caller
            passes a TVDb id -- could never match by id at all and fell back to
            title and year. Indexing both fields fixes that without touching
            movie behaviour: IMDb ids start with 'tt' and TVDb ids are numeric,
            so the two cannot collide.
        """
        self.by_uniqueid = {}
        """ The exact-year rule is three separate comparisons, and which side
            each one reads is load-bearing:

                item.originaltitle == originaltitle     (1)
                item.title         == originaltitle     (2)
                item.title         == title             (3)

            So a row's originaltitle may only ever be matched against the
            query's originaltitle, while its title may be matched against
            either. One combined name index gets this wrong by letting a row's
            originaltitle match the query's title -- which is precisely the
            divergence the equivalence test caught. Two indexes, one per side.
        """
        self.by_year_title = {}
        self.by_year_original = {}
        """ (year, title, originaltitle) for the +/-2 year rule, which demands
            both titles agree.
        """
        self.by_year_pair = {}

        for position, item in enumerate(self.items):
            entry = (position, item)

            for key in ("imdbnumber", "tvdbid"):
                value = item.get(key)
                if value:
                    self.by_uniqueid.setdefault(str(value), entry)

            try:
                year = int(item["year"])
            except (KeyError, TypeError, ValueError):
                continue

            title = item.get("title", "")
            originaltitle = item.get("originaltitle", "")

            """ Empty names are indexed rather than skipped. '' == '' is a
                match in the original, and a library row with no originaltitle
                really does match a TMDb item with none either.
            """
            self.by_year_title.setdefault((year, title), entry)
            self.by_year_original.setdefault((year, originaltitle), entry)
            self.by_year_pair.setdefault((year, title, originaltitle), entry)

    def find(self, title, originaltitle, year, uniqueid=None):
        """The matching local row, or None.

        Every rule contributes a candidate and the earliest row wins, because
        the original returned the first row satisfying any rule -- including
        the id check, which it ran per row rather than ahead of the others. A
        year match on row 2 therefore beats an id match on row 5, and this has
        to reproduce that.
        """
        candidates = []

        if uniqueid:
            entry = self.by_uniqueid.get(str(uniqueid))
            if entry is not None:
                candidates.append(entry)

        try:
            tmdb_year = int(year)
        except (TypeError, ValueError):
            """An unparseable query year makes the year rules unreachable for
            every row, exactly as the original's ValueError did.
            """
            tmdb_year = None

        if tmdb_year is not None:
            for index, name in (
                (self.by_year_original, originaltitle),
                (self.by_year_title, originaltitle),
                (self.by_year_title, title),
            ):
                entry = index.get((tmdb_year, name))
                if entry is not None:
                    candidates.append(entry)

            """ The loose rule reads item_year +/- 2 around the TMDb year, so
                from the index's side the candidates are the four neighbouring
                years. Zero is excluded: that is the exact rule above.
            """
            for offset in (-2, -1, 1, 2):
                entry = self.by_year_pair.get(
                    (tmdb_year + offset, title, originaltitle)
                )
                if entry is not None:
                    candidates.append(entry)

        if not candidates:
            return None

        return min(candidates, key=lambda entry: entry[0])[1]


def get_local_media(force=False):
    local_media = get_cache("local_db")

    if not local_media or force:
        local_media = {}
        local_media["shows"] = query_local_media(
            "tvshow",
            get="VideoLibrary.GetTVShows",
            properties=[
                "title",
                "originaltitle",
                "year",
                "playcount",
                "episode",
                "watchedepisodes",
                "uniqueid",
                "art",
            ],
        )
        """ No "art" here, deliberately. Nothing reads a movie's artwork out of
            this cache: the matcher never looks at it, and nextaired -- the one
            consumer of the field -- only ever walks the shows list.

            It is 69% of the payload. Measured against this library of 1775
            movies, the blob goes 2213 KB to 677 KB, and with it the SQLite read
            and the json.loads that every launch of the add-on pays before it
            can draw anything.
        """
        local_media["movies"] = query_local_media(
            "movie",
            get="VideoLibrary.GetMovies",
            properties=[
                "title",
                "originaltitle",
                "year",
                "uniqueid",
                "playcount",
                "file",
            ],
        )

        if local_media:
            write_cache("local_db", local_media, 24)

    return local_media


def query_local_media(dbtype, get, properties):
    items = json_call(get, properties, sort={"order": "descending", "method": "year"})

    try:
        items = items["result"]["%ss" % dbtype]
    except Exception:
        return

    local_items = []
    for item in items:
        local_items.append(
            {
                "title": item.get("title", ""),
                "originaltitle": item.get("originaltitle", ""),
                "imdbnumber": item.get("uniqueid", {}).get("imdb", ""),
                "tmdbid": item.get("uniqueid", {}).get("tmdb", ""),
                "tvdbid": item.get("uniqueid", {}).get("tvdb", ""),
                "year": item.get("year", ""),
                "dbid": item.get("%sid" % dbtype, ""),
                "playcount": item.get("playcount", ""),
                "episodes": item.get("episode", ""),
                "watchedepisodes": item.get("watchedepisodes", ""),
                "file": item.get("file", ""),
                "art": item.get("art", {}),
            }
        )

    return local_items
