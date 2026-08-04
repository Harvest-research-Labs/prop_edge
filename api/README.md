# ProEdge API Gateway (Phase 2 · Step 1)

Thin, **synchronous** FastAPI wrappers around the existing PropEdge models — stable
contracts around current behavior. **No model logic is changed.** No Redis / workers /
websockets / Postgres / multi-sport in this slice.

## Services
| Endpoint | Wraps | Notes |
|---|---|---|
| `POST /extract` | `model.screenshot.extract_picks` | needs `ANTHROPIC_API_KEY`; returns `unavailable` (not fabricated) without it |
| `POST /resolve` | `config.canonical_stat` | stat canonicalization + confidence; unmatched → `needs_review` |
| `POST /project` | `projections` / `mlb_stats` / `matchup` | own mean (MLB today) + distribution; flags sports with no own model |
| `POST /price` | `projections.over_prob` + `sources.base.devig_two_way` | hit prob, fair odds, edge; leaves unpriced when no mean/market |
| `POST /rank` | `brain.candidates` + `brain.leg_verdict` | best pick + top 5 + avoid, verdicts |
| `POST /slip/eval` | `recommend.combined_prob` | naive **and** correlation-adjusted (labeled), weakest, risk |
| `POST /explain` | `brain.get_take` | grounded analyst; never generates a probability |

Plus `GET /health` and `GET /version`.

Every response uses one envelope: `request_id`, `service_version`, `model_version`,
`data_timestamp`, `status` (`ok`/`partial`/`error`/`unavailable`), `source`, `confidence`,
`warnings[]`, `errors[]`, `data`.

## Run locally
```bash
pip install -r requirements.txt          # root deps
pip install -r api/requirements.txt      # gateway deps
cp .env.example .env                      # optional: add ANTHROPIC_API_KEY

uvicorn api.main:app --reload --port 8000
# → http://localhost:8000/health   ·   interactive docs: http://localhost:8000/docs
```

## Test
```bash
python -m pytest tests/ -q
```
- `tests/test_api.py` — endpoint contracts (offline, no key needed).
- `tests/test_smoke.py` — end-to-end on a real priced prop (`annotate` → `/rank` → `/slip/eval`).

## Scope note
`/project` and `/price` currently take decomposed per-pick inputs but rely on the same
mean/distribution math the live board uses. Full **entity resolution** and **multi-sport
projection** are later Phase-2 builds; those are flagged in `warnings` today, never faked.
