# Measurements

Everything claimed about this fork's behaviour, with how it was measured and what did not improve.

Two machines are involved. The Bravia is a Sony BRAVIA_4K_AE2 (MediaTek MT5897, Mali GPU, 4.7 GB RAM, no swap) running Kodi 22; it is where the original failure happened. The desktop is x86 Linux running Kodi 21.3 on a 39 Mbit/s wired connection, used for the before/after comparisons because it can be driven repeatably.

## The failure this fork exists to fix

Kodi was killed by the Linux OOM killer on the Bravia at 20:05:56, after roughly six and a half minutes browsing nine Embuary Info pages in a single session.

```
oom-kill:constraint=CONSTRAINT_NONE,...,task=org.xbmc.kodi,pid=2830
Out of memory: Killed process 2830 (org.xbmc.kodi)
             total-vm:3854724kB anon-rss:1982212kB file-rss:1236876kB
```

Nothing appears in `kodi.log` — it stops mid-line. The evidence is only in logcat's kernel buffer, which also carries the Mali driver's own accounting at the moment of death:

```
mali0: OOM notifier: dev mali0  1253180 kB
mali0: OOM notifier: tsk RenderThread tgid (2830) 1202732 kB
```

Kodi held 1.15 GB of the device's 1.19 GB of GPU memory, against a 46 MB idle baseline. `am_pss` shows the process flat at ~620 MB for ten hours and then climbing to 2440 MB in the 26 minutes of browsing.

## Memory, upstream vs fork

Eight pages navigated within one session, desktop Kodi, RSS sampled after each page.

| page | upstream 2.0.8 | fork 2.1.0 |
|---|---|---|
| settled | 655 MB | 647 MB |
| 1 | 717 MB | 716 MB |
| 2 | 946 MB | 726 MB |
| 3 | 945 MB | 728 MB |
| 4 | 975 MB | 735 MB |
| 5 | 966 MB | 714 MB |
| 6 | 1012 MB | 718 MB |
| 7 | 1329 MB | 754 MB |
| 8 | 1343 MB | 737 MB |
| after collapsing the session | 652 MB | 666 MB |

Upstream grows 688 MB over eight pages and the fork grows 90 MB. Both recover fully when the session ends, which is why the bug never looked like a leak in the usual sense — it is unbounded accumulation *within* a live session, released on exit.

The same walk on the Bravia, where GPU memory is separately visible via `dumpsys meminfo`, reached 983 MB of GL memory and 2107 MB RSS by page seven against a 46 MB / 582 MB baseline. That run was stopped at a guard; the user's own session went to nine pages and was killed.

## Page open time

Desktop Kodi, five movies, builds alternating per movie so network drift lands on both arms. Cold means simplecache emptied and that movie's textures dropped; warm means the same page again immediately after.

Two moments are timed separately, because the window appears well before its artwork. `setArt()` only stores URL strings, so Kodi's texture loader starts fetching after the controls are populated.

| | upstream 2.0.8 | fork 2.1.0 |
|---|---|---|
| cold, dialog on screen | 4.3s | 2.5s |
| cold, backdrop landed | 4.7s | 3.1s |
| warm, dialog on screen | 1.4s | 1.4s |

Per movie, cold time to dialog: 7.7→2.9, 4.3→2.3, 3.7→2.7, 4.9→2.5, 2.5→2.1.

## What did not improve, on the desktop

**The warm path is unchanged at 1.4s.** Warm has no network, so what remains is Kodi building roughly 700 ListItems and standing up the window. The indexed local-library matching, the de-quadratic'd crew merge and the set-based similar filter are all in that path and none of them move that number.

The cold improvement on the desktop is therefore connection reuse plus the parallel trailer checks, not the algorithmic work.

## Why the desktop was the wrong place to judge that

The local-library matching was briefly written off as unproven on the strength of the paragraph above. That was measuring the right code on the wrong machine.

The same workload — one page of 45 items matched against the library — timed on all three devices, against a library of 1767 movies, which is what this install actually holds:

| device | upstream scan | index build (once per session) | index lookup |
|---|---|---|---|
| desktop x86, Kodi 21.3 | 18.8 ms | 2.2 ms | 0.1 ms |
| Bravia aarch64, Kodi 22 | 148 ms | 15 ms | 0.55 ms |
| LibreELEC armv7 | 369 ms | 28 ms | 1.2 ms |

And at 5000 movies on the LibreELEC box: 1059 ms of scanning per page, against an 87 ms one-off build and 1.2 ms per page.

So on the slowest device at the real library size this is 369 ms of blocking Python per page, on the thread the UI is waiting on. Over the nine-page session that got Kodi killed, that is 3.3 seconds of scanning versus 39 ms indexed. On the desktop the same change saves 17 ms out of a 1400 ms page open — 1.2%, comfortably inside the noise, which is exactly why it looked like nothing.

The ARM/x86 gap for this workload is about 8x on the Bravia and 20x on the LibreELEC box. Worth stating because a much larger figure circulates for Python on Kodi hardware; it does not hold here.

The general lesson is the one worth keeping: a null result from the fastest machine available says nothing about the machines this add-on is actually used on.

## Image bandwidth

The number worth quoting is what Kodi actually fetches when a page opens, measured by snapshotting the texture cache, opening the page, waiting for fetching to stop, and summing `Content-Length` over exactly the URLs that appeared.

| build | textures fetched | downloaded |
|---|---|---|
| upstream 2.0.8 | 43 | 20.0 MB |
| fork 2.1.0 | 45 | 4.0 MB |

The fork fetches two textures *more* while downloading five times less. That is a real if minor cost of the size split: the hero backdrop and the details poster now exist at one size for the page and another for the images grid, where upstream used `original` for both and cached each once.

**An earlier draft of this claimed 579 MB per page and that was wrong.** It summed every image URL the page constructs — 707 for Dune — which is an upper bound on exposure, not a cost, because Kodi's texture loader is lazy and the bulk of those URLs belong to the browsable images grid that only loads if you open that tab and scroll it. The Bravia log had already contradicted it: across the nine pages of the fatal session, newly cached images ran 66, 33, 21, 43, 28, 34, 21, 36, 17 — a median of about 33 per page, not 707.

The 707 figure is not meaningless, though. Opening the images tab and scrolling it is precisely the path that made upstream fetch hundreds of `original`-sized images, and that is the OOM case.

## Public surface

Compared between the fork point (`f4e1f02`) and HEAD: RunScript arguments, control ids 10051-10059, ListItem properties, window properties, the `onnext`/`onback_<id>`/`onclose` skin hooks, and skin XML filenames are all unchanged. `setProperty` call count goes 65 to 66, the addition being `fullsize`.

That is what makes 2.1.0 a minor version rather than a major one: nothing integrating with this add-on has to change.

## Entering the add-on, and where that time goes

The complaint was that entering the add-on from a library list is slow. It was,
and almost none of it was the add-on's own work.

Desktop Kodi 21.3, library of 1775 movies, opening a movie page from the library
by dbid, warm (TMDb responses already in simplecache). Time is measured from
firing the launch to the dialog reporting itself visible, polled over JSON-RPC.

| stage | cumulative | this stage |
|---|---|---|
| imports done | 1175 ms | **1175 ms** |
| find_id done | 1277 ms | 102 ms |
| library index built | 1292 ms | 15 ms |
| page data fetched | 1666 ms | 374 ms |
| onInit reached | 1742 ms | 76 ms |
| listitems added | 1787 ms | 45 ms |

Imports are 1175 ms of a 1642 ms median page open. Broken down by import:

| import | cost |
|---|---|
| `requests` | **825 ms** |
| `arrow` | 154 ms |
| stdlib + Kodi modules | 130 ms |
| `xml.etree` | 31 ms |
| `concurrent.futures` | 20 ms |
| `urllib.request` | 8 ms |

`requests` alone is half the time it takes to open a page. That matches the
1.11 s figure measured elsewhere on Kodi 21; it is executing the package's
module bodies, and bytecode caching does not help.

## Interpreter reuse, and the half of it that does not apply

`<reuselanguageinvoker>` parks the interpreter instead of tearing it down, so a
second invocation skips every import. Two things had to be established the hard
way.

**It needs a Kodi restart, not an add-on bounce.** Kodi caches the parsed
addon.xml, so disable/enable leaves the flag inert. Measured with the flag in
place but only bounced, four consecutive plugin fetches:

| | fetch 1 | fetch 2 | fetch 3 | fetch 4 |
|---|---|---|---|---|
| after a bounce | 1429 ms | 1663 ms | 1452 ms | 2104 ms |
| after a restart | 1671 ms | 262 ms | 206 ms | 170 ms |

The imports log 0 ms from the second fetch onward and the interpreter's module
ids are identical across all four, so this is genuine reuse rather than warming.
Without knowing about the restart, the flag measures as doing nothing at all.

**It does not apply to `RunScript`.** Six consecutive info-dialog launches each
got a fresh interpreter and paid the full import cost. Checked with nothing else
running in between (the log shows no other script), and again from a clean
restart having never touched the plugin path, in case the single reusable
invoker slot was simply held by `plugin.py`. Same result both times: different
module id, 1100-1400 ms of imports, every launch.

So the path the complaint is about cannot amortise its imports. The only lever
on it is importing less.

## What changed, and what it bought

| | before | after |
|---|---|---|
| info dialog (RunScript), warm | 1892 ms | 1388 ms |
| plugin listing, first call | ~1450 ms | 1069 ms |
| plugin listing, repeat calls | ~1450 ms | 117-224 ms |
| cached local library index | 2213 KB | 717 KB |

The dialog's 504 ms comes from dropping `arrow` (154 ms), deferring `xml.etree`
(31 ms) and `concurrent.futures` (20 ms) into the paths that need them, deleting
an unused `urllib.request` (8 ms), caching the external-id lookup that ran on
every open (~66 ms), and a smaller library blob to read and parse.

**`import requests` is untouched and is still 825 ms of every dialog launch.**
It is now by a wide margin the largest single cost in the add-on, and interpreter
reuse cannot reach it. Removing it means replacing the shared `requests.Session`
with `http.client`, keeping per-host connection reuse, across tmdb, omdb, trakt
and the trailer HEAD pool.

## What this does not tell you

Every figure above is from a desktop. Per the section further up, the ARM gap on
this add-on's workload measured 8x to 20x rather than the much larger number
often quoted, and a null result on a desktop says nothing either way. The import
costs in particular are CPU-bound module-body execution, so they should scale
with the gap: 1175 ms here is plausibly several seconds on a TV box, which is
what "very slow on first entering" sounds like.

None of the numbers here have been reproduced on the Bravia.

## Replacing requests

`import requests` was the largest single cost left, at 825 ms of every info
dialog launch. What it was being used for is four call sites doing a plain GET
or HEAD and reading a body, so it was replaced with `http.client` behind the
same `SESSION.get`/`SESSION.head` interface.

Builds alternated per round so network drift lands on both arms, same method as
the page-open comparison further up. Two runs per arm per round, desktop Kodi
21.3.

| round | cold: requests | cold: stdlib | warm: requests | warm: stdlib |
|---|---|---|---|---|
| 1 | 1772 ms | 1446 ms | 1128 ms | 651 ms |
| 2 | 2432 ms | 1344 ms | 1179 ms | 572 ms |
| 3 | 1711 ms | 1332 ms | | |

Warm roughly halves, which is the import cost coming out. Cold improves by less
in proportion, as it should: the network round trips are unchanged and they are
what the rest of the cold time is.

Taken with everything before it, the info dialog warm goes from **1892 ms to
around 600 ms**, and plugin listings settle at 73-213 ms once the interpreter is
parked.

What was kept from `requests.Session`, because dropping any of it would have
been a regression rather than a saving: per-host connection reuse (the reason a
session existed at all), gzip, redirect following, thread safety for the trailer
pool, and one retry when a pooled connection turns out to have been closed by
the server. That last one urllib3 handled invisibly; `http.client` surfaces it
as an exception on the *next* request, and without the retry it presents as an
intermittent failure that reads as a flaky network.

What was not kept, deliberately: cookies, auth, streaming, multipart, retry
ladders. No call site wanted them.

### A note on the method

An earlier attempt at this A/B reported the two arms as within 2% of each other.
That was wrong, and the reason is worth writing down: the deploy helper ran
`git checkout <branch> -- .`, which overwrote the working tree, so both arms
measured the same code. The tell was that a change measured at 825 ms of import
time appeared to do nothing at all. **If an A/B says a large, well-understood
change did nothing, check that the two arms are actually different before
believing it.**

## End to end, against the build this work started from

The comparisons above each measure one change. This is the whole of it: the fork
as it stood at `main`, against the finished branch, on the path the original
complaint named — opening a movie page from a library item.

Cold means the add-on's simplecache emptied before each run, so every TMDb call,
the OMDb call and the trailer checks are paid for real. That is the first time
you open a given title. Builds alternated per round.

| round | `main` | final |
|---|---|---|
| 1 | 2364 ms | 1414 ms |
| 2 | 2404 ms | 1479 ms |
| 3 | 3242 ms | 1444 ms |
| 4 | 2471 ms | 1394 ms |
| **median** | **2438 ms** | **1429 ms** |

**Cold: 41% faster, 1.7x.** Warm, from the sections above: 1892 ms to around
600 ms, **68% faster, 3.2x**.

Cold improves by less in proportion, and that is the expected shape rather than
a disappointment. What was removed was fixed CPU cost — imports, a library blob,
an id lookup. The network round trips are untouched, and having taken a second
off the fixed cost they are now most of what remains. Roughly 1.4 s of a cold
open is TMDb, OMDb and YouTube answering.

One incidental result worth noting: the spread across runs collapses, 2364-3242
ms on `main` against 1394-1479 ms after. Same machine, same connection,
interleaved runs. No claim about the cause; it is simply much more predictable
than it was.

### Reading these two numbers

Warm is the one that describes browsing: paging between cast, similar titles and
back again, which is where a session's time actually goes and where the 3.2x
lands. Cold is the first touch of each new title.

Neither has been reproduced on the Bravia. Per the ARM section above, the fixed
costs removed here are CPU-bound and should scale with the 8-20x gap measured on
this add-on's workload, while the network half will not — so the cold split on a
TV box should tilt further toward the network than it does here.

## On the Bravia at last

Everything above was measured on a desktop, with a standing note that none of it
had been reproduced on the ARM box the add-on exists for. This section is that
box: a Sony BRAVIA 4K AE2, Android 14, Kodi 22.0 beta, 1772 movies and 82 shows
in the library — the same order as the desktop's 1775.

Driven over ADB. Builtins have to originate on the device: Kodi's EventServer
there binds `udp6` only, so a `RunScript` sent from a v4 LAN is discarded with
no error anywhere. `Addons.ExecuteAddon` is not a substitute — it resolves to
the add-on's **first** `<extension>`, which is the plugin, so it ran `plugin.py`
and dispatched to the root listing instead of opening the dialog.

### Where a page open actually goes

Warm — TheMovieDB responses already cached — opening a movie page, timed from
firing the launch to the window reporting itself visible. Stage marks are inside
the process.

| stage | cumulative | this stage |
|---|---|---|
| imports done | 221 ms | 221 ms |
| find_id done | 235 ms | 14 ms |
| local_db read and parsed | 290 ms | **55 ms** |
| shows indexed (82 rows) | 291 ms | 1 ms |
| movies indexed (1772 rows) | 334 ms | **43 ms** |
| page data fetched | 453 ms | 119 ms |
| onInit reached | 559 ms | 106 ms |
| listitems added | 602 ms | 43 ms |
| **window visible** | **~1079 ms** | **~477 ms** |

**The single largest cost is not Python.** Nearly half the wait is Kodi standing
up the window after the add-on has finished. Three separate signals — window
visibility, the details container, the cast container — all flip at the same
instant, so this is not the 400 ms `WindowOpen` fade being waited on; it is
window construction.

### The desktop is a better proxy than the docs above assume

1079 ms here against roughly 600 ms on the desktop is **1.8x**, not the 8–20x
the library-scan section measured. That gap was for a Python-bound workload, and
after the import and matching work there is not much Python left in this path.

### Two things that turned out not to be worth doing

**Image count does not drive page-open time.** Dune builds 358 image ListItems
to Fight Club's 143 and opens in the same time: 1093 ms against 1183 ms, warm,
which is inside the noise. The plan for this work carried "build image-grid
ListItems lazily" as a candidate on the strength of 358 of ~490 ListItems being
images. On this hardware it would buy nothing.

**A 110 ms Python saving did not move the wall clock.** Deferring `ssl` cut the
import phase from 221 ms to 110 ms, measured in-process, and the time to a
visible window did not change: 1079 ms before, 1122 ms after, distributions
overlapping. Both paths are bounded by something else.

### And one measurement that lied, again

The first A/B of that change on the plugin listing path read 1403–1577 ms
without it against 624–823 ms with — a clean 2x, and wrong. Re-run alternating,
both arms sit at 583–823 ms. The first arm had been warmed by earlier activity
and the second was measured cold straight after an add-on bounce.

That is the second time in this file a large, plausible, one-directional result
came from an A/B that was not alternating. **Alternate the arms, or do not
report the number.**
