# Runbook: Job Queue (example — copy to job-queue.md and fill in your details)

Source: `<your-service>/job_queue.py`

## What DEAD_LETTER means

A job reaches `DEAD_LETTER` in two ways:

**1. Terminal error — immediate, no retries**
The error matches a known-deterministic failure. Retrying would waste resources.
Add your terminal markers here:
- `<error string 1>` — user input was invalid (e.g. wrong file type)
- `<error string 2>` — upstream asset is gone (404/403/410)
- `<error string 3>` — malformed payload

**2. Exhausted retries**
Non-terminal errors retry with backoff until `max_retries` is reached, then → DEAD_LETTER.

## Retry policy

| Retry # | Delay | Condition |
|---|---|---|
| 1 | immediate | any non-terminal error |
| 2 | +60 seconds | any non-terminal error |
| 3 | +300 seconds | any non-terminal error |
| → DEAD_LETTER | — | terminal OR retry >= max_retries |

## How to query DEAD_LETTER signals from OpenClaw

```sql
SELECT entity, message, COUNT(*) as n
FROM signals
WHERE source = 'cloudwatch_logs'
  AND message LIKE '%DEAD_LETTER%'
GROUP BY entity, message
ORDER BY n DESC
```

## Triage guide

| Log message | Root cause | Action |
|---|---|---|
| `<terminal error 1>` | Invalid user input | Validate input client-side before enqueue |
| `<terminal error 2>` | Asset missing | Check storage lifecycle policy |
| `after N retries` | Worker down or overloaded | Check service health; scale if sustained |

## What OpenClaw should NOT flag as a bug

- A DEAD_LETTER from a terminal error is **intentional** — the code skips retries
  deliberately. The fix is input validation, not changing the dead-letter logic.
- Retry backoff delays (jobs sitting in PENDING for 60–300s) are **intentional**.

## Related files (fill in your paths)

- `<service>/job_queue.py` — `retry_or_dead_letter()`, `_TERMINAL_ERROR_MARKERS`, `_is_terminal_error()`
- `<service>/worker.py` — where the worker calls `retry_or_dead_letter`
