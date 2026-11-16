# The PRIM data for the RER B

What the Île-de-France Mobilités PRIM APIs return for the RER B, and the definitions this
project builds on. Everything here was measured with `scripts/explore_prim.py` on Monday
16 November 2026 between 12:24 and 12:33 UTC (13:24–13:33 in Paris, off-peak): one full
snapshot of the four APIs, then 12 polls of the global and unitary APIs 30 seconds apart.

Points still to confirm are listed in [Open questions](#open-questions).

## APIs used

All calls go to `https://prim.iledefrance-mobilites.fr/marketplace` with the key in an
`apikey` header.

| API | Request | Response | Size |
|---|---|---|---|
| Next departures, global | `GET /estimated-timetable?LineRef=ALL` | SIRI Lite `EstimatedTimetableDelivery`, whole network | 58 MB |
| Next departures, unitary | `GET /stop-monitoring?MonitoringRef=STIF:StopArea:SP:473843:` | SIRI Lite `StopMonitoringDelivery`, one station | 0.1 MB |
| Traffic info, global | `GET /disruptions_bulk/disruptions/v2` | `disruptions` and `lines`, whole network | 1.3 MB |
| Screen messages | `GET /general-message?LineRef=STIF:Line::C01743:` | SIRI Lite `GeneralMessageDelivery` | 3 KB |

The RER B is line `STIF:Line::C01743:`. Stations are identified as
`STIF:StopArea:SP:<number>:`, where the number is the one in the open data stop ID
(`IDFM:monomodalStopPlace:<number>`). `make stations` lists all 47.

## Quotas

- Each API has its own quota of **1,000 requests per day**.
- Every response carries `x-ratelimit-remaining-day` and `ratelimit-reset` (seconds until the
  reset). The reset falls at **midnight UTC**, which is 01:00 in Paris in winter and 02:00 in
  summer.
- Whether failed requests count against the quota is not known yet.

## Global feed (`estimated-timetable`)

**Refresh.** The feed is rebuilt **every 2 minutes**, one second after each even minute
(12:26:01, 12:28:01, 12:30:01, 12:32:01). Requests between two rebuilds return identical
data. A request takes 6 to 12 seconds to download and 0.3 seconds to parse with the standard
`json` module, so streaming parsing is not needed.

**Coverage.** The whole network: about 15,500 journeys on 1,200 lines. For the RER B, 103
to 108 journeys at a time, covering **all 47 stations**, both directions, from a few
minutes in the past to about 4 hours ahead. That is about 2,400 station calls per version.

**One journey** (`EstimatedVehicleJourney`) is one train:

| Field | Content | Example |
|---|---|---|
| `DatedVehicleJourneyRef` | Train ID, unique in the feed | `SNCF_MAGENTA_PRD:VehicleJourney::75310902-…:LOC` |
| `DirectionRef` | `Aller` = southbound, `Retour` = northbound | `Aller` |
| `JourneyNote` | Mission code | `KALI` |
| `VehicleJourneyName` | Mission code and train number | `KALI48` |
| `OriginName`, `DestinationName` | Terminals, which give the branch | `Massy-Palaiseau` |
| `RecordedAtTime` | When the producer last updated the train | |
| `EstimatedCalls.EstimatedCall[]` | One entry per station still to serve | |

Calls are **not** in chronological order inside a journey; sort them by aimed time.

**One call** is one train at one station:

| Field | Presence | Notes |
|---|---|---|
| `StopPointRef` | always | station reference |
| `AimedDepartureTime`, `ExpectedDepartureTime` | 96% of calls | missing at the terminus |
| `AimedArrivalTime`, `ExpectedArrivalTime` | 97% of calls | missing at the origin |
| `DepartureStatus` | always | `ON_TIME`, `DELAYED`, `CANCELLED` |
| `ArrivalStatus` | always | same values, plus `ARRIVED` |
| `DeparturePlatformName` | always | platform |

Times are in UTC with one-second precision.

**What the snapshot showed:**

- **The same train ID is used along the whole line**, including across Gare du Nord where the
  operator changes: 89 of 103 journeys had calls on both sides.
- **Cancellation is flagged per call.** A train can be cancelled at some stations only: 3
  journeys had cancelled calls, none was cancelled at every station.
- **`ON_TIME` does not mean on time.** Calls marked `ON_TIME` had delays of up to 327 seconds.
  The project computes delay from the times and ignores this status, except `CANCELLED`.
- **Delays at that moment:** median 0 s, 90th percentile 64 s, maximum 923 s, minimum −64 s
  (early).
- **Predictions move.** Between two versions of the feed, about 6% of the expected departure
  times changed.
- **A call leaves the feed around 100 seconds after its expected time** (median; observed
  range from 135 seconds before to 289 seconds after, over 59 calls). A few calls stay much
  longer; the oldest was 15 minutes in the past.

## Unitary feed (`stop-monitoring`)

- **One request covers both directions** at the station: 73 trains at Cité Universitaire,
  up to 3 hours ahead.
- **Same train IDs as the global feed** (`FramedVehicleJourneyRef.DatedVehicleJourneyRef`).
  Every train in the unitary feed was also in the global feed, in all 12 polls.
- **Fresher than the global feed.** Responses are at most about 20 seconds old, against up to
  2 minutes for the global feed. The expected times were identical in 95% of comparisons;
  the largest difference was 207 seconds.
- **Extra fields:** `VehicleAtStop` (the train is at the platform) and `Order` (position of
  the station in the journey).
- **Different spelling of statuses:** `onTime`, `delayed`, `arrived` instead of `ON_TIME`,
  `DELAYED`, `ARRIVED`. The parser must normalise them.
- A train leaves this feed at about its departure time, while it is still present in the
  global feed.

## Traffic info (`disruptions_bulk`)

- `lines[]` links each line to disruption IDs, for the line as a whole and per station.
  `disruptions[]` holds the details.
- A disruption has an `id`, a `cause`, a `severity`, a `title`, an HTML `message`, a
  `lastUpdate`, and a list of `applicationPeriods` (`begin` and `end`).
- **Causes:** `TRAVAUX` (planned works), `PERTURBATION` (incident), `INFORMATION`.
  **Severities:** `BLOQUANTE`, `PERTURBEE`, `INFORMATION`.
- **Most disruptions are planned works**: 498 of 842 on the network, and all 4 on the RER B at
  the time. Many apply only at night, on a list of dates.
- Period times have **no time zone** (`20261116T224500`). They are read as Paris local time.

Consequence: a disruption is **active** only inside one of its application periods, and alerts
must separate incidents (`PERTURBATION`) from planned works (`TRAVAUX`).

## Screen messages (`general-message`)

Three messages for the RER B, all on the `Perturbation` channel, all about planned works, with
a `ValidUntilTime`. They overlap with the traffic info API and carry less structure, so the
project uses traffic info as its source and keeps this API as a backup.

## Definitions

- **Departure**: one train at one station, identified by (train ID, station reference). Both
  feeds update the same departure.
- **Actual departure time**: the last `ExpectedDepartureTime` seen for a departure. The APIs
  never give an observed time. At the terminus, where there is no departure, the arrival
  times are used.
- **Delay**: actual departure time − `AimedDepartureTime`, in seconds. It can be negative.
- **Final**: a departure is final when it is absent from the latest global feed, or when its
  last expected time is more than 20 minutes in the past.
- **Cancelled**: the departure's last known status is `CANCELLED`. A train cancelled at some
  stations only counts as cancelled at those stations.
- **Unknown outcome**: a departure that leaves the feed more than 5 minutes before its
  expected time without being flagged. It is recorded separately and counted neither as
  cancelled nor as on time, until more data shows what these are.
- **Direction and branch**: `DirectionRef` gives the direction (`Aller` southbound, `Retour`
  northbound). The branch comes from the origin and destination stations.
- **Waiting time**: for the gaps *h* between consecutive actual departures at a station in one
  direction, the average wait of a passenger arriving at a random moment is Σh² / (2·Σh).
- **Time**: stored in UTC, aggregated by hour and day in `Europe/Paris`. A train running after
  midnight belongs to the previous service day.

## Metrics

Computed by `app/analytics/queries.py` from the departures that are over.

- **Punctuality**: number of trains that ran, average and 90th percentile delay, share of
  trains more than 5 minutes late (exactly 5 minutes is not late), number of cancelled
  departures and cancellation rate (cancelled / (ran + cancelled)). It can be broken down by
  branch, direction, station, weekday or weekend, hour and service date.
- **Branch**: the part of the line the train is heading to, given by its destination
  station: `cdg`, `mitry`, `robinson`, `saint_remy`, or `trunk` for a train that ends on the
  central section.
- **Reliable prediction**: a departure counts in delay figures only if it was still seen in
  a feed less than 5 minutes before its actual time. Otherwise collection had stopped and the
  last prediction is too old to stand for what happened. Cancelled departures always count.
- **Segments**: a train is followed from stop to stop by its train ID. The delay gained on a
  segment is the delay at the second stop minus the delay at the first, averaged over
  trains. Consecutive means consecutive stops of that train, so semi-direct trains give
  longer segments.
- **Waiting time**: see Definitions. A gap is attributed to the hour in which it starts;
  gaps over one hour are ignored; the last train of a service day is not linked to the first
  of the next.
- **Measurement error**: at the station polled by the unitary feed, the difference between
  the last prediction of the global feed and the last prediction of the unitary feed, for
  departures seen in both.

The delay, the service date, the Paris hour and the reliability flag are stored on each
departure when it is closed, so the queries do not recompute them.

### Query performance

Measured with `uv run python -m scripts.bench_queries --days 30`, which fills a separate
database with 30 days of invented departures at the expected volume (450,000 rows) and
reports the median of 7 runs. On a laptop (PostgreSQL 16 in Docker, default settings):

| Query | Time |
|---|---|
| Punctuality, 7 days, overall | 28 ms |
| Punctuality, 7 days, by branch and direction | 49 ms |
| Punctuality, 7 days, by station | 77 ms |
| Punctuality, 7 days, one station by hour | 2 ms |
| Punctuality, 30 days, by day type and hour | 194 ms |
| Segments, 7 days, one direction | 69 ms |
| Waiting time, 7 days, one station | 1 ms |
| Measurement error, 7 days | 1 ms |

Every 7-day query is under the 100 ms target. The 30-day breakdown is not: it reads every
row and the 90th percentile forces a sort of all of them. Stored columns, a covering index
and more sort memory were tried and did not bring it under 100 ms. The fix, if long windows
are needed, is a table of pre-aggregated hourly figures.

## Polling budget

| API | Schedule | Requests per day | Quota |
|---|---|---|---|
| Global | every 2 minutes, at second 20 of each even minute, around the clock | 720 | 1,000 |
| Unitary (my station) | every minute, 07:00–10:00 and 17:00–20:00 Paris time | 360 | 1,000 |
| Traffic info | every 5 minutes, around the clock | 288 | 1,000 |
| Screen messages | not polled | 0 | 1,000 |

- Polling the global feed more often than every 2 minutes returns the same data. Polling
  20 seconds after each rebuild captures every version with a lag of about 20 seconds.
- 720 requests leave 280 per day for retries, so the global feed can be polled around the
  clock. Stopping at night is optional and can be decided once the service hours are known.
- The quota counter must reset at midnight UTC, not at midnight in Paris.

**Expected volume** (estimate, to confirm after one day of collection): about 500 to 600 trains
a day and 25 stations per train on average, so about 15,000 departure rows a day. A first
run stored 2,708 departures from three consecutive polls, in line with that.

**Precision.** The last prediction for a departure is seen at most about 2 minutes before the
train leaves, since calls stay in the feed until after their expected time. The unitary feed
at my station, polled every minute with data at most 20 seconds old, is the reference to
measure the error of the global-only data.

## Open questions

- **Service hours**: when the first and last trains appear in the feed. Needs polls around
  05:00 and 01:00.
- **Rush hour and real incidents**: this capture was off-peak on a calm day. How cancellations
  and large delays appear in the feed needs a capture during a disruption.
- **Unexplained disappearances**: 2 of 59 calls left the feed about 2 minutes before their
  expected time. Not enough to tell what they are.
- **Calls that linger**: why some calls stay 15 minutes after their expected time.
- **Failed requests and the quota**: whether a 4xx or 5xx response uses a request.
- **License**: the terms of use of each API and the attribution to put in the README.

## Test fixtures

`tests/fixtures/prim/` holds responses from the snapshot above, cut down to a few RER B
journeys but otherwise unmodified. See the README in that folder.
