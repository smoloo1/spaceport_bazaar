# Current client architecture

The normal entry point is `python client/run_live.py --automate`.
There is one active implementation, not parallel V1/V2 copies of the client.

```text
run_live.py       loads token and settings; opens the connection and log
    bazaar.py    sends/receives Protobuf messages over WebSocket
    automated.py waits for readiness, budgets commands, matches results
        strategy.py  reads a snapshot and proposes an action with a reason
    journal.py   writes snapshots, decisions, messages, and outcomes
```

## Responsibilities

| Module | Owns | Does not own |
| --- | --- | --- |
| `run_live.py` | CLI options, authentication configuration, mode selection, error messages | Trade selection |
| `bazaar.py` | Command builders, wire format, subprotocol, sending and receiving | Strategy |
| `strategy.py` | Reserve calculations, trade and gift candidates, reasons | Sockets or mutable game state |
| `automated.py` | Latest snapshot, readiness, pending request, per-tick budgets, cooldowns | Server-authoritative inventory updates |
| `journal.py` | Append-only JSONL history and token redaction | Decisions |
| `manual.py` | Optional typed commands for testing and debugging | Autonomous decisions |
| `run_exercise.py` | Fixed starter-guide walkthrough | General multiplayer play |

The automation reads the initial state and declares readiness. Once the server
confirms readiness and the phase is running, it asks the strategy for candidates.
It sends one eligible command, waits for the matching result and new state,
and reevaluates. Inventory comes from server snapshots; the client never adds
transaction amounts to inventory a second time.

The strategy accounts for outstanding offers even though the server does not
reserve their stock. This reduces overcommitment but cannot prevent a trade
settling while a withdrawal is in flight. A disconnect stops the client;
restart it to reconnect. Pending commands are not persisted or replayed.

## Other files

- `client/bazaar_pb2.py` is generated from the supplied `.proto`. Regenerate it
  with `sh client/generate.sh`; do not maintain a hand-edited copy.
- `client/requirements.txt` declares the runtime and code-generation dependencies.
- `tests/helpers.py` contains shared test fixtures. Test suites do not import
  fixtures from other test suites.
- `simulations/` is optional offline research and is never imported by the live
  client. It calls the decision function with modeled game states.
- `docs/analysis/` and `docs/archive/v1/` contain historical V1 material. They are
  not alternative startup instructions or active strategy definitions.

Use the [strategy guide](automated-trading.md) for current settings and the
[game-day guide](game-day.md) for operating the client.
