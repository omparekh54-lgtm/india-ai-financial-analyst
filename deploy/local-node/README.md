# Local research node

Runs the **research worker** (on-demand data fetch + the 16 agents) and the **daily shared-market
refresh** on your own computer in India. NSE blocks cloud servers, but a normal Indian home
connection can reach it, so this is where per-stock data fetching works.

The node needs **no open ports and no tunnel**. It polls the Supabase job queue, picks up jobs the
website created, and writes reports back to Supabase. If the computer is off, jobs wait in the queue
and run when it comes back.

## What runs

| Part | When | What it does |
| --- | --- | --- |
| Research worker | Always, one job at a time | Fetches the requested stock's missing data, checks readiness, runs the agents |
| Daily refresh | Weekdays after 19:00 IST (or on next start if missed) | Daily prices for supported stocks, NIFTY 50 and India VIX, India VIX macro sync |

## 1. Fill in the settings

```
cd deploy/local-node
cp .env.example .env
```

Open `.env` and set at least:

- `DATABASE_URL`: Supabase → Project Settings → Database → Connection string (URI). Use the
  same value the API uses.
- `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`: Supabase → Project Settings → API.
- `TAVILY_API_KEY`: needed by the News, Web, Sentiment and Risk agents.
- Optional: `GROQ_API_KEY` or `GEMINI_API_KEY` with `ENABLE_EXTERNAL_LLM_CALLS=true` for
  better-written reports.

Never commit `.env`.

## 2. Check this computer can reach NSE

With Docker:

```
docker compose run --rm node python scripts/check_nse_reachability.py
```

Without Docker (Python 3.11+): `./start-local-node.sh check` on macOS/Linux, or
`.\start-local-node.ps1 check` in Windows PowerShell.

Both NSE API checks must say `OK`. If they fail, try another Indian connection (mobile hotspot,
different ISP). The archive check is informational.

## 3. Start the node

With Docker (restarts automatically after a reboot while Docker is running):

```
docker compose up -d --build
docker compose logs -f
```

Without Docker: `./start-local-node.sh` or `.\start-local-node.ps1`. Keep the window open.

To run the shared-market refresh immediately the first time, add `--refresh-now`
(`docker compose run --rm node python scripts/run_local_node.py --refresh-now --no-worker`).

## Notes

- Keep it to this one node. Two nodes would double NSE traffic from your network.
- NSE can temporarily block an IP that sends too many requests. The worker handles one job at a
  time and the importers pace themselves; avoid running bulk backfills at the same time.
- The daily refresh state lives in `~/.india-ai-analyst/local-node-state.json` (or the
  `node-state` Docker volume). Delete it to force a refresh.
