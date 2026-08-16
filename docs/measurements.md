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
