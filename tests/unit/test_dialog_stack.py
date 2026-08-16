"""The navigation loop must not accumulate pages.

These are regression tests for the defect that motivated the fork: browsing
enough pages in one session grew Kodi's memory without bound until the kernel
killed it. The invariant they hold down is that no matter how far a user
browses, at most MAX_LIVE_DIALOGS dialogs stay instantiated.
"""

import sys
import weakref

import pytest

from resources.lib import main as main_module
from resources.lib.main import TheMovieDB

""" Every bound below is an absolute number, never `main_module.MAX_LIVE_DIALOGS`.

    The first version of this file asserted `len(cache) <= MAX_LIVE_DIALOGS`,
    which passes for any value of the constant -- including the unbounded
    behaviour the fork exists to fix. Raising the constant to a billion left
    the suite green. Tests that move with the thing they are pinning are not
    tests, so the cap is forced to a known value and the counts are checked
    against literals.
"""
CAP = 3


class FakeDialog:
    """Stands in for a WindowXMLDialog.

    A real dialog holds a Kodi window and a whole page of GPU textures, so an
    instance surviving here is the unit of the leak being tested.

    Liveness is tracked by the owning Script's WeakSet rather than by a class
    counter incremented in __init__ and decremented in __del__. That counter
    was the first attempt and it silently did not work: __del__ for one test's
    dialogs runs during a later test, driving the shared counter negative, so
    the later test started from a negative base and could not exceed its
    ceiling no matter how badly the code leaked. A deliberately leaky mutation
    measured 21 live dialogs standalone and still passed the suite.
    """

    def __init__(self, script, actions):
        self.script = script
        self.actions = list(actions)
        self.action = {}
        self.modal_calls = 0

    def __getitem__(self, key):
        return self.action[key]

    def doModal(self):
        """Apply the next scripted user action, sampling the run as we go.

        Sampling happens here, mid-browse, because that is the only moment the
        numbers mean anything: quit() clears the cache, so anything measured
        after dialog_manager returns is zero whether the cap works or not.
        Peak-while-browsing is also simply the right measure -- it is what has
        to fit in the device's memory.
        """
        self.modal_calls += 1
        self.script.depths.append(_python_depth())
        self.script.peak_cached = max(
            self.script.peak_cached, len(self.script.tmdb.dialog_cache)
        )
        self.script.peak_alive = max(self.script.peak_alive, len(self.script.live))

        if self.actions:
            self.action = dict(self.actions.pop(0))
        else:
            self.action = {"id": "", "call": "close", "season": ""}


def _python_depth():
    depth = 0
    frame = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


class Script:
    """Drives a TheMovieDB instance without touching Kodi or TMDb.

    Builds a TheMovieDB via __new__ so that __init__ -- which talks to TMDb and
    opens a busy dialog -- never runs, then substitutes build_dialog with a
    factory that hands back FakeDialogs following a canned click script.
    """

    def __init__(self, clicks_per_page, pages):
        self.depths = []
        self.built = []
        self.peak_cached = 0
        self.peak_alive = 0
        """ Scoped to this run, so one test cannot corrupt another's count. """
        self.live = weakref.WeakSet()
        self.pages = pages
        self.clicks_per_page = clicks_per_page

        self.tmdb = TheMovieDB.__new__(TheMovieDB)
        self.tmdb.monitor = _NeverAborts()
        self.tmdb.history = []
        self.tmdb.dialog_cache = __import__("collections").OrderedDict()
        self.tmdb.call = "movie"
        self.tmdb.tmdb_id = 0
        self.tmdb.season = ""
        self.tmdb.call_params = {}
        self.tmdb.build_dialog = self._build

    def _build(self):
        page = len(self.built)
        self.built.append(page)

        if page < self.pages:
            actions = [{"id": page + 1, "call": "movie", "season": ""}]
        else:
            actions = [{"id": "", "call": "close", "season": ""}]

        dialog = FakeDialog(self, actions)
        self.live.add(dialog)
        key = self.tmdb.request_key(self.tmdb.call, self.tmdb.tmdb_id, self.tmdb.season)
        self.tmdb.cache_dialog(key, dialog)
        return dialog


class _NeverAborts:
    def abortRequested(self):
        return False

    def waitForAbort(self, seconds):
        return False


@pytest.fixture(autouse=True)
def _force_cap(monkeypatch):
    monkeypatch.setattr(main_module, "MAX_LIVE_DIALOGS", CAP)


def _run(pages):
    script = Script(clicks_per_page=1, pages=pages)
    first = script.tmdb.build_dialog()

    with pytest.raises(SystemExit):
        script.tmdb.dialog_manager(first)

    return script


def test_request_key_is_one_spelling():
    """A season page and its show must not collide, and must be stable.

    Upstream wrote the key without the season and read it back with, so season
    pages never hit their own cache entry.
    """
    show = TheMovieDB.request_key("tv", 1399, "")
    season = TheMovieDB.request_key("tv", 1399, 2)

    assert show != season
    assert TheMovieDB.request_key("tv", 1399, None) == show
    assert TheMovieDB.request_key("tv", 1399, 2) == season


def test_shipped_cap_is_small():
    """The default that actually ships has to stay in a sane range.

    The other tests force the cap to CAP so their numbers mean something; this
    is the one that notices if the shipped value drifts.
    """
    assert 1 <= main_module.MAX_LIVE_DIALOGS <= 5


def test_cache_is_bounded_while_browsing():
    """Twenty pages deep, the cache never held more than CAP."""
    script = _run(pages=20)

    assert len(script.built) == 21
    assert script.peak_cached == CAP


def test_dialogs_are_released_not_merely_unreferenced():
    """Evicted dialogs must actually be collected, not just unlinked.

    The run's WeakSet counts instances that are still reachable, so this fails
    if an eviction leaves the object referenced from anywhere else -- which is
    what upstream's recursion did. The loop's own local holds the page being
    shown on top of whatever is cached, hence the +1.
    """
    script = _run(pages=20)

    assert script.peak_alive <= CAP + 1


def test_history_holds_descriptors_not_dialogs():
    """Depth must cost tuples, not windows."""
    script = Script(clicks_per_page=1, pages=5)
    first = script.tmdb.build_dialog()

    with pytest.raises(SystemExit):
        script.tmdb.dialog_manager(first)

    for entry in script.tmdb.history:
        assert isinstance(entry, tuple)
        assert len(entry) == 3


def test_navigation_does_not_recurse():
    """Stack depth must be flat across pages.

    This is the property that made the original unbounded: each page was
    entered from inside the frame of the one before it, so every page ever
    visited stayed reachable from the Python stack. A loop keeps the depth
    constant.
    """
    script = _run(pages=20)

    assert len(script.depths) >= 20
    assert max(script.depths) - min(script.depths) <= 1
