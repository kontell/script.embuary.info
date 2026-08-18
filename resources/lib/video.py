#!/usr/bin/python
# coding: utf-8

########################

import sys
import xbmc
import xbmcgui

from collections import OrderedDict

from resources.lib.helper import *
from resources.lib.tmdb import *

########################

""" Concurrency for the trailer liveness check. These are latency-bound HEAD
    requests to one host, so a small pool saturates the useful parallelism;
    going wider mostly annoys YouTube.
"""
YT_CHECK_WORKERS = 8

""" Crew jobs worth showing, and the order their departments appear in. Both
    were inline in get_crew; the department order is load-bearing, so it is
    named rather than implied by the order of four near-identical loops.
"""
CREDITED_JOBS = frozenset(
    (
        "Creator",
        "Director",
        "Producer",
        "Screenplay",
        "Writer",
        "Original Music Composer",
        "Novel",
        "Storyboard",
        "Executive Producer",
        "Comic Book",
    )
)
CREW_DEPARTMENTS = ("Directing", "Writing", "Production", "Sound")

########################


def filter_live_videos(videos):
    """Drop trailers whose YouTube thumbnail is gone.

    Upstream checked these one at a time, with no timeout and a fresh
    connection per video. A title with twenty trailers meant twenty sequential
    round trips before the page could open, and any one unresponsive host hung
    the dialog indefinitely behind a busy spinner with no way out. It was the
    largest fixed cost in opening a movie page.

    Two deliberate choices here:

    A check that errors or times out keeps the video rather than dropping it.
    Showing one dead trailer is a far smaller harm than silently hiding every
    working one because the network hiccuped -- and upstream's version did
    exactly that, since an exception propagated out and lost the whole list.

    The shared SESSION is used across the pool, which its connection pool is
    built for: handing a connection out removes it from the pool, so no two
    threads can ever hold the same one.
    """
    if not videos:
        return []

    """ Imported here rather than at module scope: ~20 ms inside Kodi's
        interpreter, and every page whose trailer list is already cached never
        reaches this function at all.
    """
    from concurrent.futures import ThreadPoolExecutor

    def alive(item):
        try:
            request = SESSION.head(
                "https://img.youtube.com/vi/%s/0.jpg" % str(item["key"]),
                timeout=HTTP_TIMEOUT,
            )
            return request.status_code == 200

        except Exception:
            return True

    with ThreadPoolExecutor(max_workers=min(YT_CHECK_WORKERS, len(videos))) as pool:
        results = list(pool.map(alive, videos))

    return [item for item, ok in zip(videos, results) if ok]


class TMDBVideos(object):
    def __init__(self, call_request):
        self.result = {}
        self.call = call_request["call"]
        self.tmdb_id = call_request["tmdb_id"]
        self.local_movies = call_request["local_movies"]
        self.local_shows = call_request["local_shows"]
        self.movie = get_bool(self.call, "movie")
        self.tvshow = get_bool(self.call, "tv")

        if self.tmdb_id:
            cache_key = self.call + str(self.tmdb_id)
            self.details = get_cache(cache_key)

            if not self.details:
                self.details = tmdb_query(
                    action=self.call,
                    call=self.tmdb_id,
                    params={
                        "append_to_response": "release_dates,content_ratings,external_ids,credits,videos,translations,similar"
                    },
                    show_error=True,
                )

                write_cache(cache_key, self.details)

            if not self.details:
                return

            self.created_by = (
                self.details["created_by"] if self.details.get("created_by") else ""
            )
            self.crew = self.details["credits"]["crew"]
            self.details["crew"] = self.crew
            self.similar_duplicate_handler = set()

            self.result["details"] = self.get_details()
            self.result["cast"] = self.get_cast()
            self.result["crew"] = self.get_crew()
            self.result["collection"] = self.get_collection()
            self.result["similar"] = self.get_similar()
            self.result["youtube"] = self.get_yt_videos()
            self.result["backdrops"], self.result["posters"] = self.get_images()
            self.result["seasons"] = self.get_seasons()

    def __getitem__(self, key):
        return self.result.get(key, "")

    def get_details(self):
        li = list()

        if self.movie:
            list_item, is_local = tmdb_handle_movie(
                self.details, self.local_movies, full_info=True
            )
        elif self.tvshow:
            list_item, is_local = tmdb_handle_tvshow(
                self.details, self.local_shows, full_info=True
            )

        li.append(list_item)
        return li

    def get_cast(self):
        li = list()

        for item in self.details["credits"]["cast"]:
            item["label2"] = item.get("character", "")
            list_item = tmdb_handle_credits(item)
            li.append(list_item)

        return li

    def get_crew(self):
        """Credited crew, deduplicated and grouped by department.

        Same output as upstream, without the quadratic behaviour. Upstream
        tested membership against a list and then rescanned the accumulated
        list to find the duplicate it had just detected, so a title with a
        hundred crew entries did on the order of ten thousand comparisons; it
        then walked the result four more times, once per department. A dict
        does the dedup in one pass and the departments are bucketed in one
        more, with the order of both departments and members preserved.
        """
        by_id = OrderedDict()

        """ Creators first, so they lead the Directing bucket.
        """
        for item in self.created_by:
            item["job"] = "Creator"
            item["department"] = "Directing"
            by_id.setdefault(item["id"], item)

        for item in self.crew:
            if item["job"] not in CREDITED_JOBS:
                continue

            existing = by_id.get(item["id"])

            if existing is None:
                by_id[item["id"]] = item
            else:
                """Same person, another job -- merge the titles rather than
                listing them twice.
                """
                existing["job"] = existing["job"] + " / " + item["job"]

        buckets = {department: [] for department in CREW_DEPARTMENTS}

        for item in by_id.values():
            bucket = buckets.get(item["department"])

            if bucket is None:
                continue

            if (
                item["department"] == "Sound"
                and item["job"] == "Original Music Composer"
            ):
                item["job"] = "Music Composer"

            item["label2"] = item.get("job", "")
            bucket.append(tmdb_handle_credits(item))

        li = list()
        for department in CREW_DEPARTMENTS:
            li.extend(buckets[department])

        return li

    def get_seasons(self):
        seasons = self.details.get("seasons")
        li = list()

        if seasons:
            for item in seasons:
                if item["season_number"] == 0:
                    continue
                list_item = tmdb_handle_season(item, self.details)
                li.append(list_item)

        return li

    def get_collection(self):
        collection = self.details.get("belongs_to_collection")
        li = list()

        if collection:
            collection_id = collection["id"]

            cache_key = "collection" + str(collection_id)
            collection_data = get_cache(cache_key)

            if not collection_data:
                collection_data = tmdb_query(action="collection", call=collection_id)

                write_cache(cache_key, collection_data)

            if collection_data["parts"]:
                set_items = sort_dict(collection_data["parts"], "release_date")

                for item in set_items:
                    """Filter to hide in production or rumored future movies"""
                    if filter_upcoming():
                        diff = date_delta(item.get("release_date", "2900-01-01"))
                        if diff.days > filter_daydelta():
                            continue

                    list_item, is_local = tmdb_handle_movie(item, self.local_movies)
                    li.append(list_item)

                    if similar_movies_filter():
                        self.similar_duplicate_handler.add(item["id"])

            """ Don't show sets with only 1 item
            """
            if len(li) == 1:
                self.similar_duplicate_handler = set()
                li = list()

        return li

    def get_similar(self):
        similar = self.details["similar"]["results"]
        li = list()

        if self.movie:
            similar = sort_dict(similar, "release_date", True)

            for item in similar:
                """Filter to hide item if it's part of the collection"""
                if (
                    similar_movies_filter()
                    and item["id"] in self.similar_duplicate_handler
                ):
                    continue

                """ Filter to hide in production or rumored future movies
                """
                if filter_upcoming():
                    diff = date_delta(item.get("release_date", "2900-01-01"))
                    if diff.days > filter_daydelta():
                        continue

                list_item, is_local = tmdb_handle_movie(item, self.local_movies)
                li.append(list_item)

        elif self.tvshow:
            similar = sort_dict(similar, "first_air_date", True)

            for item in similar:
                """Filter to hide in production or rumored future shows"""
                if filter_upcoming():
                    diff = date_delta(item.get("first_air_date", "2900-01-01"))
                    if diff.days > filter_daydelta():
                        continue

                list_item, is_local = tmdb_handle_tvshow(item, self.local_shows)
                li.append(list_item)

        return li

    def get_images(self):
        cache_key = "images" + str(self.tmdb_id)
        images = get_cache(cache_key)
        li_backdrops = list()
        li_poster = list()

        if not images:
            images = tmdb_query(
                action=self.call,
                call=self.tmdb_id,
                get="images",
                params={"include_image_language": "%s,en,null" % language_code()},
            )

            write_cache(cache_key, images)

        for item in images["backdrops"]:
            list_item = tmdb_handle_images(item)
            li_backdrops.append(list_item)

        for item in images["posters"]:
            list_item = tmdb_handle_images(item)
            li_poster.append(list_item)

        return li_backdrops, li_poster

    def get_yt_videos(self):
        cache_key = "ytvideos" + str(self.tmdb_id)
        videos = get_cache(cache_key)
        li = list()

        if not videos:
            videos = self.details["videos"]["results"]

            """ Add EN videos next to the user configured language
            """
            if language_code() != FALLBACK_LANGUAGE:
                videos_en = tmdb_query(
                    action=self.call,
                    call=self.tmdb_id,
                    get="videos",
                    use_language=False,
                )

                videos_en = videos_en.get("results")
                videos = videos + videos_en

            videos = filter_live_videos(videos)
            write_cache(cache_key, videos)

        for item in videos:
            if item["site"] == "YouTube":
                list_item = tmdb_handle_yt_videos(item)
                if not list_item == 404:
                    li.append(list_item)

        return li
