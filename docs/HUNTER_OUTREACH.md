# Hunter Outreach — first 10

Notes for the first round of Strecker beta invites. Target: landowners
and hunters you've already been in contact with (TNDeer forum, Matagorda
Bay field-calibration network, etc.). Goal of the first cohort:

1. Confirm the 3-phase upload flow works end-to-end on a real SD card
   (not our curated TNDeer fixture).
2. Catch filename conventions we haven't seen — station codes, date
   formats, species words.
3. Harvest 3-5 quotes usable for the landing page.

Free for beta. No pricing conversation yet.

---

## Channel 1 — TNDeer forum DM

Subject: **Free tool that sorts your trail-cam photos by species**

> Hey — saw your post about [specific thing: mudhole bear pattern /
> buck ratio on the Plateau / whatever's specific to them]. I've been
> building a thing for hunters that's now running and I'd love to put
> it in front of one or two people who keep real cards.
>
> It's called **Strecker** (strecker.basal.eco). Here's what it does:
> pull your SD card, drop the photos in, and it sorts every frame by
> species, camera station, and hour. A ten-point at Crooked Well on
> October 17 at 4 AM becomes one row; 40 burst frames of the same doe
> become one event. Run it on a full season's card and you get a list
> of every animal that used your land, when, and where.
>
> Completely free while in beta. No subscription, no data-sharing, your
> photos stay yours. I run the classifier (SpeciesNet, the Google one)
> on a server and you get back a dashboard.
>
> If you want to be one of the first to try it, just register at
> **https://strecker.basal.eco** and drop a card in. It'll take 15-30
> minutes to process the first time while the classifier warms up.
>
> Happy to jump on a call for 15 min if you want me to walk you through
> it. And if there's anything broken — I want to hear about it.
>
> — Jonah

---

## Channel 2 — Email to existing ground-truth contacts

Subject: **The trail-cam thing is live — want to try it?**

> Hey [name],
>
> Quick note — the tool I mentioned a while back is actually running now.
> It's called Strecker and it takes the grunt work out of going through
> a full season's SD card: upload a zip, and it sorts every frame by
> species, station, and time.
>
> Live at **https://strecker.basal.eco**. Free while in beta.
>
> I'd really value you being one of the first to run a real card
> through. I've got about 300 photos of mine own tested so far and I
> want a second perspective — especially on the filename conventions
> you use and what species list you'd want surfaced.
>
> Want to give it a shot this weekend? I'll be watching for your
> upload and will fix anything that breaks on you immediately.
>
> Thanks,
> Jonah

---

## Channel 3 — Short-form (Twitter/X, Instagram DM)

> Built a trail-cam classifier for hunters. Upload your SD card,
> get back every animal by species + camera + hour.
> Free beta. strecker.basal.eco
> DM me if you want in — looking for the first 10.

---

## First-touch logistics

- Send to **no more than 10 people** in the first wave. Each one takes
  ~20 min of live support during their first upload. Watching 10
  uploads is a weekend; 50 is a disaster.
- Leave the worker Droplet running. First uploads will warm the model
  cache; subsequent ones run in 3-8 minutes.
- Keep `strecker-worker` logs tailing in a terminal while you're
  available — you want to see their job status flip to `complete` (or
  catch the failure before they email you).
- After each upload: ask for a one-sentence quote — "what did you
  notice first?" or "was there anything you expected to see that
  wasn't there?" — and log it in `docs/HUNTER_QUOTES.md`.

## What to watch for

Each first-card upload is also a quality-assurance signal. Track:

- **Filename patterns** we haven't seen. The tndeer fixture has
  `CF MH Bear 40.JPG`-style labels — yours probably won't. Add new
  station-code regex cases to `strecker/filename_labels.py` if
  needed.
- **Species we misclassify.** SpeciesNet is tuned for North America
  but ranked against a mega-taxonomy. Watch the per-photo
  `confidence` in the gallery — any detection under 0.5 that shows
  the wrong animal needs feedback.
- **Upload failures.** The three most likely: ZIP over 2 GB (should
  400 at request), session expiry mid-upload (presigned URL expires
  in 30 min), Spaces CORS on a new host (shouldn't happen now that
  we've wildcarded strecker.basal.eco).

## Follow-up rhythm

Day 1 after their upload: short message asking what stood out.
Week 1: share their dashboard screenshot back to them if it looks
good — "here's what your land looked like in October." Month 1:
early renewal conversation if they're active.

Don't let the first 10 go cold. That's how you get the next 100.
