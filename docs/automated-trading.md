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

Every launch prints the path of its JSONL history in `run/logs/`. Histories
include snapshots, sent messages, policy settings, and automated decision
reasons, including reasons for waiting. Tokens, connection headers, and `.env`
contents are not recorded. Run histories are ignored by Git. See the
[game-day guide](game-day.md) for startup and post-game review.

To inspect recommendations without sending readiness or trading commands:

```sh
python client/run_live.py --advisory
```

Advisory mode still opens an authenticated connection; a new connection can
replace another client for the same station. Recommendations describe one
candidate per update and do not simulate execution or future inventory.

## Initial policy

The existing policy remains the default. To use the separate, more generous
variant, start with `--generous-policy`:

```sh
python client/run_live.py --automate --generous-policy
```

This profile keeps five ticks of specialty upkeep and thirty ticks of each
imported resource, permits up to five units of payment in an exchange, and
advertises only the specialty as a product. Its specialty gifts are dynamic:
the client samples reported production once per tick, keeps the latest five
samples, and caps each gift by a share of available surplus and a production
budget. The share is 25% up to eight ticks of specialty stock, 50% up to
fifteen ticks, and 75% above that. To account for accumulated specialty,
stock above a fifteen-tick comfort level adds to the recent-production gift
budget at that same share. Available surplus is stock above the specialty
reserve and existing outgoing commitments. Gifts stop if stock is below any
resource reserve. If recent production is zero, only a stockpile above the
fifteen-tick comfort level can fund a gift. The gift-size option remains a
switch: zero disables gifts; any positive value enables them in this profile.
Explicit reserve and trade CLI options override the profile defaults.

The two October 5 histories informed the change. In the run using a five-tick
imported reserve, P02 (components specialty) ended with 406 components and no
water or food, after failing around tick 110. The run with a fifteen-tick
imported reserve and five-unit gifts ended with 328 food and no water or
components, failing around tick 58. Both runs show that increasing gifts alone
does not solve shortages in imported resources; the new profile protects a
larger imported stock and directs sales and gifts toward the production
specialty. These two runs are observations, not a guarantee that thirty ticks
is optimal. The dynamic gift share is a starting heuristic and should be
reviewed against later run histories.

## Fast policy for one-second ticks

The separate `--fast-policy` profile is intended for the nine-planet,
one-second-tick trial. It protects three upkeep ticks for each resource, starts
seeking imported resources at eight ticks, permits five-unit exchanges, makes
no automatic gifts, advertises only its production specialty, and uses offer
lifetimes of up to four ticks (subject to server caps). It evaluates the current
authoritative snapshot immediately on each update; it does not poll or sleep
between ticks. The executor still sends one command at a time and waits for
the result and next state, so actual action throughput depends on server update
and response latency.

Put the nine assigned tokens in the project `.env` using these independent
names; do not replace `BAZAAR_TOKEN`:

```dotenv
BAZAAR_FAST_TOKEN_1=token_for_client_1
BAZAAR_FAST_TOKEN_2=token_for_client_2
BAZAAR_FAST_TOKEN_3=token_for_client_3
BAZAAR_FAST_TOKEN_4=token_for_client_4
BAZAAR_FAST_TOKEN_5=token_for_client_5
BAZAAR_FAST_TOKEN_6=token_for_client_6
BAZAAR_FAST_TOKEN_7=token_for_client_7
BAZAAR_FAST_TOKEN_8=token_for_client_8
BAZAAR_FAST_TOKEN_9=token_for_client_9
```

Start nine independent processes and use each slot once:

```sh
python client/run_live.py --automate --fast-policy --fast-policy-key 1
python client/run_live.py --automate --fast-policy --fast-policy-key 2
python client/run_live.py --automate --fast-policy --fast-policy-key 3
python client/run_live.py --automate --fast-policy --fast-policy-key 4
python client/run_live.py --automate --fast-policy --fast-policy-key 5
python client/run_live.py --automate --fast-policy --fast-policy-key 6
python client/run_live.py --automate --fast-policy --fast-policy-key 7
python client/run_live.py --automate --fast-policy --fast-policy-key 8
python client/run_live.py --automate --fast-policy --fast-policy-key 9
```

Each process writes its own run history. A single generous-policy run remains
`python client/run_live.py --automate --generous-policy`; it continues using
`BAZAAR_TOKEN` and does not read or alter any fast-policy slot.



- Identify our production resource from `self.specialty`. Retain three ticks
  of its reported upkeep and five ticks of upkeep for each other resource.
  Start seeking imported resources when they fall below that larger target.
  Do not assume last tick's production guarantees future production, or spend
  stock below the reserve just because it is our specialty.
- Subtract the full give amounts of open outgoing offers from spendable
  surplus, because those offers do not reserve stock on the server.
- Withdraw outgoing offers when their aggregate commitments threaten reserves.
- Accept free gifts and small incoming exchanges that improve shortages without
  paying from reserves or committed stock. When fully supplied, also accept
  small surplus-funded exchanges proposed by peers. Paid incoming exchanges
  normally require at least one unit received per unit paid; a premium of up
  to two paid per urgently needed unit is allowed only during an emergency.
- Seek the lowest-coverage shortage first. Offer up to two surplus units in a
  normally one-for-one exchange with a peer advertising compatible interests.
  When stock covers less than one turn of reported upkeep, offer up to two
  surplus units for one needed unit. Fall back to one-for-one if the payment
  cap or available surplus cannot cover two units.
  Prefer paying with our specialty; imported resources can only be spent from
  surplus above their larger reserves.
- When all reserves are covered, offer up to one surplus unit of our specialty
  as a gift to a peer advertising a need. Never automatically gift imported
  resources. Rotate peer ordering by tick.
- Keep at most one open outgoing offer per peer. Attempt at most one outgoing
  offer to each peer per tick in a session. A peer may decline or ignore a gift.
- Advertise surplus and shortages after available trades. Use short, two-tick
  expirations capped by server limits; do not repeatedly refresh in one tick.

Examples of policy configuration:

```sh
python client/run_live.py --automate --reserve-ticks 3 --imported-reserve-ticks 5 --trade-size 1 --gift-size 1
python client/run_live.py --automate --gift-size 0
python client/run_live.py --automate --reserve-ticks 5 --imported-reserve-ticks 30 --trade-size 3 --gift-size 5

```

`--reserve-ticks`, `--imported-reserve-ticks`, `--emergency-ticks`, and
`--trade-size` must be positive. `--emergency-ticks` defaults to 1: an emergency
means inventory is strictly below that many turns of reported upkeep.
Reserves and the payment cap remain binding even in an emergency. `--gift-size 0` disables
outgoing gifts. The trade-size cap applies to total units paid when accepting
an incoming offer and the units paid in a proposed exchange. Free incoming
gifts are not subject to that payment cap. These settings apply to advisory
and automated modes, not manual trades.

up the amount of reserve ticks and horde things we don't produce, give everything we produce away
only advertise product you produce

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

For the 120-tick comparison across different starting supplies, upkeep,
production interruptions, specialties, and peer response speeds:

```sh
python simulations/compare_p02.py
```

See [the comparison report](analysis/policy-comparison.md) for results and model
limitations. The simulator uses the class log as scenario input; it does not
assume that future games start with those supplies or replay classmates' choices.

To isolate the effect of longer-lived offers while keeping advertisements and
peer policies unchanged:

```sh
python simulations/compare_offer_lifetimes.py
```

See [the offer lifetime comparison](analysis/offer-lifetimes.md). The experiment
uses target offer lifetimes of 2, 4, 6, and 12 ticks, capped by server limits.
It does not change the live client's two-tick default.

Earlier seeking is now separate from the spending reserve. To experiment with
seeking imported resources below ten turns of supply while protecting five:

```sh
python client/run_live.py --advisory --imported-seek-ticks 10 --imported-reserve-ticks 5
```

Omitting `--imported-seek-ticks` keeps it equal to `--imported-reserve-ticks`.
The seeking horizon cannot be smaller than the reserve horizon. The reserve
still determines what we can afford to spend or gift; seeking sooner sets the
replenishment target. Live defaults remain five imported-resource ticks and a
two-unit payment cap. The comparison has not established a survival benefit
from increasing either parameter.

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
horizons, emergency threshold, small gifts, and exchange rates need evaluation.
The policy may refuse a risky emergency trade even when taking that risk could
improve survival. It does not forecast production, learn exchange rates, or
optimize a formal score. Open offers can settle before a withdrawal reaches the
server, so reserve checks do not guarantee that reserves are always preserved.

Local checks establish specific decision and protocol behavior, not performance
over a full multiplayer game. Evaluate health, shortage ticks, peer benefit,
rejected commands, and final outcomes in an administrator-run practice game
before drawing conclusions about effectiveness.
