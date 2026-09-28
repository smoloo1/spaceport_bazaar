# Game-day guide

## Before class

In the devcontainer, run:

```sh
python -m unittest discover -s tests -v
python tests/check_automated_local.py
```

These verify local behavior, not access to the classroom server. Confirm the
assigned client token in `.env` and the endpoint with the administrator.
Previous live attempts returned HTTP 530; resolve that before the run if it
persists. The observatory token does not authenticate the client.

When the server is available, check the connection:

```sh
python client/run_live.py
```

A received `state` confirms authentication and decoding. Stop with Ctrl+C
before starting automation. Run only one client for your station; a second
connection replaces the first. This check does not declare readiness.

## Start automation

```sh
python client/run_live.py --automate
```

Look for `Run history:`, a received state, and `Automation readiness confirmed.`
The administrator controls game phase. The bot waits for a running game before
trading; you do not type manual commands.

Defaults remain: protect 3 upkeep ticks of our specialty and 5 of imported
resources; seek imported supplies below 5 ticks; pay at most 2 units per trade;
allow up to 2-for-1 when a needed resource has less than one upkeep tick left;
gift at most 1 surplus specialty unit; expire offers after 2 ticks.

Keep the terminal visible. After a disconnect, inspect the latest log and
restart the client. It reads fresh state and declares readiness again; it does
not automatically reconnect or replay uncertain commands. Coordinate with
your teammate so you do not replace each other's connections.

Coordinate with classmates about who produces what and whether their clients
accept offers and gifts. Our bot infers needs from advertisements, not private
peer inventories. Shared survival with classmates' clients is still unverified.

## After the game

After receiving the final state, stop with Ctrl+C. Use the exact history path
printed at startup:

```sh
python scripts/summarize-run.py run/logs/client-TIMESTAMP-ID.jsonl
```

Replace the example filename with the actual file. The summary shows first and
latest inventory and health, observed tick range, command outcomes, and waiting
reasons. The last observed snapshot is not necessarily the final game result if
the connection ended early. Sent offers are not completed trades; check results
and the snapshots' transaction histories to see what settled.

Keep your history alongside the administrator's final observatory log. Our
client log cannot establish every planet's survival. Compare both sources to
understand collective outcomes and when shortages first developed.

## What is recorded

- A unique file per launch, UTC timestamps, and log sequence numbers.
- Session start/end and connection status, including rejected-handshake HTTP codes.
- Full decoded snapshots, server replies, and successfully sent messages.
- Automated/advisory settings, reasons, action arguments, run ID, tick, snapshot
  sequence, world version, and request IDs for correlating decisions and results.
- Waiting reasons such as readiness, pending command, game phase, and rate limits.

A decision without a matching sent message and result does not prove completion.
Network failure can leave a command's outcome uncertain; do not blindly repeat
it with a new ID.

Logs exclude authorization headers, raw connection URLs, token configuration,
and `.env` contents. The current client token is additionally redacted from
text fields. Files have owner-only permissions and flush after every entry;
flushing does not guarantee persistence through power failure. The summary
reports and skips malformed lines, including an incomplete final line after a
crash. A log write failure stops the client. Use `--log-dir` for another location;
keep custom locations out of Git. The default `run/` directory is already ignored.

## Explain the architecture

- `bazaar.py`: communicates and decodes messages.
- `strategy.py`: proposes actions with reasons from a snapshot.
- `automated.py`: handles readiness, budgets, and action/result sequencing.
- `journal.py`: saves the history without making trading decisions.
- `run_live.py`: loads configuration and starts the selected mode.
