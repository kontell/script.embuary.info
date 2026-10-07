"""Hide movies and shows by original language or country of origin.

This is a cache lookup and a thread pool, not another list predicate, so it
stays out of tmdb.py. Movie countries that a list result does not carry are
read from the video page's details cache through details_cache_key.
"""

from resources.lib.helper import get_cache, write_cache
from resources.lib.settings import filter_hidden_countries, filter_hidden_languages
from resources.lib.tmdb import details_cache_key, tmdb_query

""" Movie-detail requests in flight at once. The same number is the batch
    we wait for before deciding the host is down: an all-failed batch stops
    the rest, so a down TMDb costs this many lookups rather than one per credit.
"""
_LOOKUP_WIDTH = 8
_MOVIE_ORIGIN_KEY = "movie_origin_"


def _country_codes(value):
    """Normalise TMDb's TV codes or movie production-country objects."""
    if isinstance(value, str):
        return frozenset((value,)) if value else frozenset()
    codes = set()
    for country in value or ():
        code = country.get("iso_3166_1") if isinstance(country, dict) else country
        if code:
            codes.add(code)
    return frozenset(codes)


def _movie_countries(items):
    """Origin countries for movie summaries, fetching missing details in parallel.

    Credits, Similar, Collection and Discover carry original_language but not
    production_countries. The country filter is opt-in, and each movie detail
    is cached so later pages do not pay for this lookup again. All cache I/O
    stays on the calling thread; only HTTP requests run in the pool.
    """
    countries = {}
    missing = set()

    for item in items:
        movie_id = item.get("id")
        if not movie_id or movie_id in countries:
            continue
        if "production_countries" in item:
            countries[movie_id] = _country_codes(item["production_countries"])
            continue

        details = get_cache(details_cache_key("movie", movie_id))
        if isinstance(details, dict) and "production_countries" in details:
            countries[movie_id] = _country_codes(details["production_countries"])
            continue

        cached = get_cache(_MOVIE_ORIGIN_KEY + str(movie_id))
        if isinstance(cached, dict) and "countries" in cached:
            countries[movie_id] = frozenset(cached["countries"])
        else:
            missing.add(movie_id)

    if missing:
        from concurrent.futures import ThreadPoolExecutor

        def lookup(movie_id):
            details = tmdb_query(action="movie", call=movie_id, use_language=False)
            if isinstance(details, dict) and "production_countries" in details:
                return movie_id, _country_codes(details["production_countries"])
            return movie_id, None

        pending = list(missing)
        width = min(_LOOKUP_WIDTH, len(pending))
        with ThreadPoolExecutor(max_workers=width) as pool:
            for start in range(0, len(pending), width):
                batch = list(pool.map(lookup, pending[start : start + width]))
                for movie_id, result in batch:
                    if result is not None:
                        countries[movie_id] = result
                        write_cache(
                            _MOVIE_ORIGIN_KEY + str(movie_id),
                            {"countries": sorted(result)},
                        )
                # An offline TMDb would otherwise spend three retries on
                # every credit of a prolific actor. Keep the unseen titles.
                if all(result is None for _, result in batch):
                    break

    return countries


def exclude_origins(items, media_type):
    """Remove movies or shows matching either selected language or country.

    Missing metadata and failed country lookups keep the item: neither is
    evidence that it belongs to a country the user chose to hide.
    """
    languages = filter_hidden_languages()
    hidden_countries = filter_hidden_countries()
    if not languages and not hidden_countries:
        return items

    visible = [item for item in items if item.get("original_language") not in languages]
    if not hidden_countries or not visible:
        return visible

    if media_type == "tv":
        return [
            item
            for item in visible
            if not (_country_codes(item.get("origin_country")) & hidden_countries)
        ]

    if media_type == "movie":
        origins = _movie_countries(visible)
        return [
            item
            for item in visible
            if not (origins.get(item.get("id"), frozenset()) & hidden_countries)
        ]

    return visible
