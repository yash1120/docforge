The daimon repository provides a public API through the `src/daimon` module [src/daimon/__init__.py:1].
## src/daimon
* `service_worker`: returns a FileResponse with a service worker script [src/daimon/api.py:280-285]
* `cli`: provides various commands for interacting with the application, including generating letters and mining correspondence [src/daimon/cli.py:1-300]
## src/daimon/api
No public API is exposed directly from this module, but it contains several handlers for HTTP routes.
## src/daimon/cli
No public API is exposed directly from this module, but it contains several handlers for CLI commands.
## src/daimon/db
No public API is exposed directly from this module, but it contains several functions for interacting with the database, including `init_db` [src/daimon/db.py:1].
## src/daimon/graph
No public API is exposed directly from this module, but it contains several functions for building and generating graphs.

The application uses a `backend` to handle requests and responses [src/daimon/api.py:10].
To establish a connection, the application uses `connect` [src/daimon/db.py:10].
The application also uses `session_id` to track user sessions [src/daimon/api.py:20].
The application supports several request types, including `GenerateRequest`, `ReplyRequest`, `SalonRequest`, `MeRequest`, `BookmarkRequest`, and `PrefsRequest` [src/daimon/api.py:30-50].

### HTTP Routes
| Method | Path | Handler | Citation |
| --- | --- | --- | --- |
| GET | / | index | src/daimon/api.py:267 |
| GET | /sw.js | service_worker | src/daimon/api.py:279 |
| GET | /manifest.webmanifest | manifest | src/daimon/api.py:288 |
| GET | /api/philosophers | list_philosophers | src/daimon/api.py:301 |
| GET | /api/about | about | src/daimon/api.py:310 |
| GET | /api/letters | list_letters | src/daimon/api.py:324 |
| GET | /api/letters/{letter_id} | get_letter | src/daimon/api.py:341 |
| POST | /api/generate | generate | src/daimon/api.py:357 |
| POST | /api/reply | reply | src/daimon/api.py:413 |
| POST | /api/salon | salon | src/daimon/api.py:433 |
| GET | /api/me | get_me | src/daimon/api.py:461 |
| POST | /api/me | set_me | src/daimon/api.py:467 |
| POST | /api/letters/{letter_id}/bookmark | bookmark | src/daimon/api.py:487 |
| GET | /api/bookmarks | bookmarks | src/daimon/api.py:496 |
| GET | /api/search | search | src/daimon/api.py:501 |
| GET | /api/stats | stats | src/daimon/api.py:509 |
| GET | /api/prefs | prefs | src/daimon/api.py:518 |
| POST | /api/prefs | save_prefs | src/daimon/api.py:523 |
| GET | /api/health | health | src/daimon/api.py:538 |
| GET | /api/philosophy | philosophy | src/daimon/api.py:550 |

### CLI Commands
| Method | Path | Handler | Citation |
| --- | --- | --- | --- |
| CMD | init | init | src/daimon/cli.py:28 |
| CMD | write | write | src/daimon/cli.py:37 |
| CMD | reply | reply | src/daimon/cli.py:69 |
| CMD | log | log | src/daimon/cli.py:119 |
| CMD | web | web | src/daimon/cli.py:149 |
| CMD | themes | themes | src/daimon/cli.py:165 |
| CMD | send | send | src/daimon/cli.py:197 |
| CMD | salon | salon | src/daimon/cli.py:237 |
| CMD | dbcheck | dbcheck | src/daimon/cli.py:275 |
