# PRIM fixtures

Real responses from the Île-de-France Mobilités PRIM APIs, recorded on 16 November 2026 at
12:24 UTC. The envelope and every kept item are unmodified; only the number of items was
reduced so the files are small enough to commit. They contain no API key.

| File | API | What was kept |
|---|---|---|
| `estimated_timetable.json` | Next departures, global | 7 RER B journeys and 1 RER A journey |
| `stop_monitoring.json` | Next departures, unitary (Cité Universitaire) | 12 trains, 6 of them also in the global fixture |
| `traffic_info.json` | Traffic info, global | the RER B and one other line, with their 6 disruptions |
| `general_message.json` | Screen messages for the RER B | everything |

The RER B journeys in `estimated_timetable.json` were chosen to cover the cases the parser
must handle:

- 2 journeys with some calls `CANCELLED`
- 2 journeys with `DELAYED` calls, one of them serving 36 stations
- 1 on-time journey in each direction
- 1 journey with a single call left (arrival at the terminus, no departure times)
- 1 RER A journey, which the RER B filter must drop

See `docs/data.md` for the meaning of the fields.
