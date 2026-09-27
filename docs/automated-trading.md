# Automated cooperative survival

The agreed objective is to survive while helping other planets survive. The
initial policy is deliberately simple and configurable; it is not a claim of
optimal play or guaranteed survival.

## Run

After installing dependencies and generating Protobuf bindings as described in
the root README, start the autonomous client:

```sh
python client/run_live.py --automate
```

It loads your client token, connects to the provided live endpoint, declares
readiness after receiving state, and waits for the game to be running before
sending trades. It then acts without typed commands. Ctrl+C stops it.

To inspect recommendations without sending readiness or trading commands:

```sh
python client/run_live.py --advisory
```

Advisory mode still opens an authenticated connection; a new connection can
replace another client for the same station. Recommendations describe one
candidate per update and do not simulate execution or future inventory.

## Initial policy

- Retain three ticks of the reported upkeep for each resource. Do not assume
  last tick's production guarantees future production.
- Subtract the full give amounts of open outgoing offers from spendable
  surplus, because those offers do not reserve stock on the server.
- Withdraw outgoing offers when their aggregate commitments threaten reserves.
- Accept free gifts and small incoming exchanges that improve shortages without
  paying from reserves or committed stock. When fully supplied, also accept
  small surplus-funded exchanges proposed by peers.
- Seek the lowest-coverage shortage first. Offer up to two surplus units in a
  provisional one-for-one exchange with a peer advertising compatible interests.
- When all reserves are covered, offer up to one surplus unit as a gift to a
  peer advertising a need. Rotate peer ordering by tick.
- Keep at most one open outgoing offer per peer. Attempt at most one outgoing
  offer to each peer per tick in a session. A peer may decline or ignore a gift.
- Advertise surplus and shortages after available trades. Use short, two-tick
  expirations capped by server limits; do not repeatedly refresh in one tick.

Examples of policy configuration:

```sh
python client/run_live.py --automate --reserve-ticks 5 --trade-size 1 --gift-size 1
python client/run_live.py --automate --gift-size 0
```

`--reserve-ticks` and `--trade-size` must be positive. `--gift-size 0` disables
outgoing gifts. The trade-size cap applies to total units paid when accepting
an incoming offer and the units paid in a proposed exchange. Free incoming
gifts are not subject to that payment cap. These settings apply to advisory
and automated modes, not manual trades.

## Execution behavior

The executor sends one command, matches its result, and waits for a new
snapshot before deciding again. It uses unique IDs, limits repeated decisions
within a tick, tracks current-tick commands including stored results from
earlier connections, and respects payload, open-offer, and request-record limits.
Rate-limit responses delay further commands until the reported retry tick (or
the next tick if no retry tick is supplied). No rejected command is blindly
retried. Request-capacity exhaustion switches execution to observation.

Readiness, running phase, and station eligibility are required. Paused and
ended games produce no trading actions. An unexpected run change, fatal
protocol error, or disconnected socket stops the client. Reconnection currently
requires restarting the process; pending commands are not persisted or replayed.

## Local verification

```sh
python -m unittest discover -s tests -v
python tests/check_automated_local.py
```

The unit scenarios cover shortages, reserves, gifts, outstanding commitments,
expiration, phase changes, readiness, command budgets, cooldowns, and errors.
The integration check runs a temporary local WebSocket simulation with a fake
token. It verifies automatic readiness, waiting for game start, barter to fill
a shortage, a surplus gift to a peer, and stopping trades at game end.

The supplied practice binary only accepts the starter README's fixed sequence.
`--automate --practice` is rejected to avoid confusing a strategy decision with
a protocol failure. Keep using `run_exercise.py` or the manual walkthrough for
that server. `--advisory --practice` can inspect its initial snapshot without
advancing the exercise.

## Limits of the strategy

Peer stock is private. Advertisements reveal interest, not verified shortages,
so cooperation cannot be targeted using actual peer inventory. The reserve
horizon, small gifts, and one-for-one rates are initial assumptions to evaluate.
The policy may refuse a risky emergency trade even when taking that risk could
improve survival. It does not forecast production, learn exchange rates, or
optimize a formal score. Open offers can settle before a withdrawal reaches the
server, so reserve checks do not guarantee that reserves are always preserved.

Local checks establish specific decision and protocol behavior, not performance
over a full multiplayer game. Evaluate health, shortage ticks, peer benefit,
rejected commands, and final outcomes in an administrator-run practice game
before drawing conclusions about effectiveness.
