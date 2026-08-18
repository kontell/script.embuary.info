#!/usr/bin/python
# coding: utf-8

########################

import json
import sys
import requests
from urllib.parse import quote

from resources.lib.helper import *

########################


def omdb_api(imdbnumber=None, title=None, year=None, content_type=None):
    if imdbnumber:
        url = (
            "http://www.omdbapi.com/?apikey=%s&i=%s&plot=short&r=xml&tomatoes=true"
            % (omdb_api_key(), imdbnumber)
        )

    elif title and year and content_type:
        """Currently unreachable -- the only caller passes an imdbnumber --
        and broken twice over if it ever were reached: `urllib.quote` is
        the Python 2 spelling, and this module never imported urllib at
        all, so it would have raised NameError rather than the KeyError
        being caught. Repaired instead of deleted because the branch is a
        documented entry point, and left dead code is a landmine for
        whoever wires it up next.
        """
        try:
            title = quote(title)
        except Exception:
            return

        url = (
            "http://www.omdbapi.com/?apikey=%s&t=%s&year=%s&plot=short&r=xml&tomatoes=true"
            % (omdb_api_key(), title, year)
        )

    else:
        return

    omdb = get_cache(url)
    if omdb:
        return omdb

    elif omdb_api_key():
        """Imported here rather than at module scope: it costs ~31 ms inside
        Kodi's interpreter and is dead weight for everyone who has not
        entered an OMDb key, which is the default.
        """
        import xml.etree.ElementTree as ET

        omdb = {}

        for i in range(1, 4):  # loop if heavy server load
            try:
                request = SESSION.get(url, timeout=HTTP_TIMEOUT)

                if not request.ok:
                    raise Exception(str(request.status_code))

                result = request.text

                tree = ET.ElementTree(ET.fromstring(result))
                root = tree.getroot()

                for child in root:
                    # imdb ratings
                    omdb["imdbRating"] = child.get("imdbRating", "").replace("N/A", "")
                    omdb["imdbVotes"] = (
                        child.get("imdbVotes", "0").replace("N/A", "0").replace(",", "")
                    )

                    # regular rotten rating
                    omdb["tomatometerallcritics"] = child.get(
                        "tomatoMeter", ""
                    ).replace("N/A", "")
                    omdb["tomatometerallcritics_avg"] = child.get(
                        "tomatoRating", ""
                    ).replace("N/A", "")
                    omdb["tomatometerallcritics_votes"] = (
                        child.get("tomatoReviews", "0")
                        .replace("N/A", "0")
                        .replace(",", "")
                    )

                    # user rotten rating
                    omdb["tomatometerallaudience"] = child.get(
                        "tomatoUserMeter", ""
                    ).replace("N/A", "")
                    omdb["tomatometerallaudience_avg"] = child.get(
                        "tomatoUserRating", ""
                    ).replace("N/A", "")
                    omdb["tomatometerallaudience_votes"] = (
                        child.get("tomatoUserReviews", "0")
                        .replace("N/A", "0")
                        .replace(",", "")
                    )

                    # metacritic
                    omdb["metacritic"] = child.get("metascore", "").replace("N/A", "")

                    # other
                    omdb["awards"] = child.get("awards", "").replace("N/A", "")
                    """ date_format has no `scheme` keyword and never has,
                        so this line raised TypeError every time an OMDb key
                        was configured. Being inside the try, it skipped the
                        else branch that caches and breaks -- so every movie
                        page made three OMDb requests, cached none of them, and
                        never filled in DVD. The scheme is now a real argument
                        and takes a strftime string, which is what getRegion
                        returns and what date_format has always fed strftime.
                    """
                    omdb["DVD"] = date_format(
                        child.get("DVD", "").replace("N/A", ""), scheme="%d %b %Y"
                    )

            except Exception as error:
                log("OMDB Error: %s" % error)
                pass

            else:
                write_cache(url, omdb)
                break

        return omdb
