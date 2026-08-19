#!/usr/bin/python
# coding: utf-8

########################

import re
import sys
import xbmc
import xbmcgui

from resources.lib.helper import *
from resources.lib.tmdb import *

########################

""" TMDb genre ids. 99 is Documentary and means the same thing for movies and
    for TV; the blacklist is TV-only -- news, reality, talk.
"""
GENRE_DOCUMENTARY = 99
FILTER_SHOWS_BLACKLIST = [10763, 10764, 10767]

""" How TMDb writes a credit for someone who turned up as themselves.

    Upstream tested for `himself` or `herself` as substrings and nothing else,
    which is why so many appearances got through: TMDb writes these at least a
    dozen ways -- `Self`, `Themselves`, `Self - Host`, `Himself - Narrator`,
    `Self (archive footage)`, `Interviewee` -- and leaves the character blank
    entirely more often than it fills it in.

    Word boundaries are the point, not decoration. A substring test for `self`
    matches `Selfish`, and for `host` matches `Ghostbuster`; both are ordinary
    character names. `himself` and `herself` are listed in their own right
    because `\bself\b` does not match inside them.

    The trailing lookahead is there because a word boundary alone is not quite
    enough: an apostrophe is a non-word character, so `\bnarrator\b` matches
    `Narrator's Brother` -- a part someone played, not an appearance.
"""
SELF_CREDIT = re.compile(
    r"\b("
    r"self|selves|himself|herself|themself|themselves|"
    r"narrator|narration|host|hostess|presenter|"
    r"interviewer|interviewee|commentator|moderator"
    r")\b(?!['’]s)",
    re.IGNORECASE,
)

""" Archive footage is the one signal that does not need the documentary gate.

    A film cut around old footage of someone is not a part they played, whatever
    genre it carries, and TMDb says so in the character: `Fred Astaire (archive
    footage)`, `Self - archive footage`, `(archival footage)`. The rest of the
    words above genuinely do need the gate, because `Narrator` and `Host` are
    ordinary roles outside a documentary.

    The help text for this setting has always promised archive footage. Until
    now only documentaries delivered it.
"""
ARCHIVE_FOOTAGE = re.compile(r"\barchiv(?:e|al)\s+footage\b", re.IGNORECASE)

########################


def is_documentary(item):
    """Whether a person credit is for a documentary."""
    return GENRE_DOCUMENTARY in (item.get("genre_ids") or ())


def is_appearance(item):
    """Whether a credit is an appearance rather than a part the person played.

    Archive footage counts wherever it appears: a film built around old footage
    of someone is not a part they played, whatever genre it carries.

    Everything else is judged only inside a documentary. Outside genre 99 a
    character called `Host` or `Narrator` is a role like any other, and
    filtering on the word alone would quietly eat real credits.

    A documentary credit with no character at all counts as an appearance.
    That is the case upstream could not reach at all: it read the character
    only when there was one, and TMDb leaves it blank for most talking-head
    credits.
    """
    character = (item.get("character") or "").strip()

    if ARCHIVE_FOOTAGE.search(character):
        return True

    if not is_documentary(item):
        return False

    if not character:
        return True

    return bool(SELF_CREDIT.search(character))


def is_posthumous(item, deathday):
    """Whether a credit was released after the person died.

    Dates are compared as ISO strings rather than parsed. TMDb writes both
    fields as YYYY-MM-DD, and that ordering is the same either way, so parsing
    would only add a way to raise on a malformed one.

    A credit with no usable date is not posthumous. Missing dates reach here as
    the 1900-01-01 sentinel sort_dict substitutes, which sorts before any
    deathday and so is kept -- correct, because "date unknown" is not evidence
    of anything.
    """
    if not deathday:
        return False

    released = (item.get("release_date") or item.get("first_air_date") or "")[:10]

    if len(released) < 10:
        return False

    return released > deathday[:10]


def skip_credit(item, deathday=None):
    """Whether the person-credit settings hide this credit between them.

    Kept as one function because the movie and TV lists ask exactly the same
    question, and because the order matters: hiding documentaries outright
    subsumes hiding the appearances within them.
    """
    if filter_documentaries() and is_documentary(item):
        return True

    if filter_movies() and is_appearance(item):
        return True

    if is_below_rating(item):
        return True

    return filter_posthumous() and is_posthumous(item, deathday)


########################


class TMDBPersons(object):
    def __init__(self, call_request):
        self.tmdb_id = call_request["tmdb_id"]
        self.local_movies = call_request["local_movies"]
        self.local_shows = call_request["local_shows"]
        self.result = {}

        if self.tmdb_id:
            cache_key = "person" + str(self.tmdb_id)
            self.details = get_cache(cache_key)

            if not self.details:
                self.details = tmdb_query(
                    action="person",
                    call=self.tmdb_id,
                    params={
                        "append_to_response": "translations,movie_credits,tv_credits,images"
                    },
                    show_error=True,
                )

                write_cache(cache_key, self.details)

            if not self.details:
                return

            """ TMDb gives the person's deathday on the same payload as the
                credits, so the posthumous filter costs no extra request.
            """
            self.deathday = self.details.get("deathday") or ""

            self.local_movie_count = 0
            self.local_tv_count = 0
            self.all_credits = list()

            self.result["movies"] = self.get_movie_list()
            self.result["tvshows"] = self.get_tvshow_list()
            self.result["combined"] = self.get_combined_list()
            self.result["person"] = self.get_person_details()
            self.result["images"] = self.get_person_images()

    def __getitem__(self, key):
        return self.result.get(key, "")

    def get_person_details(self):
        li = list()

        list_item = tmdb_handle_person(self.details)
        list_item.setProperty("LocalMovies", str(self.local_movie_count))
        list_item.setProperty("LocalTVShows", str(self.local_tv_count))
        list_item.setProperty(
            "LocalMedia", str(self.local_movie_count + self.local_tv_count)
        )
        li.append(list_item)

        return li

    def get_combined_list(self):
        combined = sort_dict(self.all_credits, "release_date", True)
        li = list()

        for item in combined:
            if item["type"] == "movie":
                list_item, is_local = tmdb_handle_movie(item, self.local_movies)

            elif item["type"] == "tvshow":
                list_item, is_local = tmdb_handle_tvshow(item, self.local_shows)

            li.append(list_item)

        return li

    def get_movie_list(self):
        movies = self.details["movie_credits"]["cast"]
        movies = sort_dict(movies, "release_date", True)
        li = list()
        duplicate_handler = list()

        for item in movies:
            skip_movie = False

            """ Filter out documentaries, appearances within them, and
                releases the person did not live to see
            """
            if skip_credit(item, self.deathday):
                skip_movie = True

            """ Filter to hide in production or rumored future movies
            """
            if filter_upcoming():
                diff = date_delta(item.get("release_date", "2900-01-01"))
                if diff.days > filter_daydelta():
                    skip_movie = True

            if not skip_movie and item["id"] not in duplicate_handler:
                list_item, is_local = tmdb_handle_movie(item, self.local_movies)
                li.append(list_item)
                duplicate_handler.append(item["id"])
                item["type"] = "movie"

                if is_local:
                    self.local_movie_count += 1

                self.all_credits.append(item)

        return li

    def get_tvshow_list(self):
        tvshows = self.details["tv_credits"]["cast"]
        tvshows = sort_dict(tvshows, "first_air_date", True)
        li = list()
        duplicate_handler = list()

        for item in tvshows:
            skip_show = False

            """ Filter to only show real TV series and to skip talk, reality or news shows
            """
            if filter_shows():
                genre_ids = item.get("genre_ids")

                if not genre_ids:
                    skip_show = True
                else:
                    for genre in genre_ids:
                        if genre in FILTER_SHOWS_BLACKLIST:
                            skip_show = True
                            break

            """ Filter out documentary series, and appearances within them.

                Upstream never character-filtered TV credits at all, so a
                person's list carried every documentary series they had ever
                been interviewed for however the movie setting was set.
            """
            if skip_credit(item, self.deathday):
                skip_show = True

            """ Filter to hide in production or rumored future shows
            """
            if filter_upcoming():
                diff = date_delta(item.get("first_air_date", "2900-01-01"))
                if diff.days > filter_daydelta():
                    skip_show = True

            if not skip_show and item["id"] not in duplicate_handler:
                list_item, is_local = tmdb_handle_tvshow(item, self.local_shows)
                li.append(list_item)
                duplicate_handler.append(item["id"])
                item["type"] = "tvshow"
                item["release_date"] = item["first_air_date"]

                if is_local:
                    self.local_tv_count += 1

                self.all_credits.append(item)

        return li

    def get_person_images(self):
        li = list()

        for item in self.details["images"]["profiles"]:
            list_item = tmdb_handle_images(item)
            li.append(list_item)

        return li
