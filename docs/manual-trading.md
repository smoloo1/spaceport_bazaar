# Manual trading

Run commands in the Linux devcontainer. The client keeps reading server updates
while waiting for your input. Commands that trade are sent immediately when you
press Enter; there is no automated trading strategy.

## Connect locally

In one terminal, start a fresh practice server from the project root:

```sh
sh scripts/start-server.sh
```

In another terminal in the same container:

```sh
python client/run_live.py --practice --interactive
```

`--practice` selects localhost and reads P01's generated credentials from
`run/validation-credentials.json`. It ignores the live token in `.env` and the
environment. Use `--url` and `--credentials` to override practice connection
settings if necessary.

The supplied practice server expects the README's exact scripted sequence.
It is not a free-form trading simulation. Restart it before repeating the
walkthrough below; do not run the exercise runner at the same time.

## Commands

| Command | Purpose |
| --- | --- |
| `help` | Show syntax and examples. |
| `inventory` | Show health, inventory, upkeep, and last production. |
| `state` | Show the full latest snapshot. |
| `offers` | Show offer IDs, status, expiration, and exchange amounts. Incoming offers show what you pay and get. |
| `ads` | Show advertisements, including their IDs. |
| `stations` | Show station IDs and display names. |
| `rules` | Show the server's game and command limits. |
| `history` | Show completed transactions. |
| `ready` | Declare readiness using the latest snapshot. Wait for confirmation. |
| `sync` | Request another snapshot. |
| `advertise SELLING SEEKING TTL` | Publish resource interests, for example `advertise water food 6`. |
| `offer STATION GIVE RECEIVE TTL` | Propose a trade, for example `offer P02 2,0,0 0,1,0 6`. |
| `accept OFFER_ID` | Accept an open, unexpired offer addressed to your station. |
| `withdraw OBJECT_ID` | Withdraw your active advertisement or open outgoing offer. |
| `quit` | Disconnect. Ctrl+C also stops the client. |

Resource lists use comma-separated names `water,food,components`, or `-` for
an empty list. Bundles use three whole-number quantities in that same order.
For an outgoing offer, GIVE is what you pay and RECEIVE is what you ask for.
TTL is a positive number of ticks from the current tick, bounded by the server's
limits. It is not seconds or an absolute expiration tick.

Trading commands generate a unique request ID unless you supply
`--request-id ID`. IDs already used in this session or recorded in the latest
snapshot cannot be reused through this interface. There is no automatic retry.
Wait for the result and resulting snapshot before sending the next trade.
The server still validates stock, rate limits, and other game rules: locally
valid commands can be rejected if conditions change.

## Complete the local walkthrough manually

Enter one command at a time and wait for its responses. Inspection commands
such as `offers` and `ads` read cached state and do not send anything.

1. Enter `ready` and wait for `Readiness confirmed.`
2. Enter `advertise water food 6 --request-id student-advertise-1`.
3. Enter `advertise - components 6 --request-id student-advertise-seeking-1`.
   Use `ads` to record your new advertisement ID.
4. Enter `offer P02 2,0,0 0,1,0 6 --request-id student-offer-1`.
5. Wait for the automatic acceptance and gift updates, ending at snapshot 6.
   Enter `offers` and find the open gift from P02: you pay `(0,0,0)` and get
   `(0,0,1)`. Copy its offer ID.
6. Enter `accept GIFT_ID --request-id student-accept-1`, replacing `GIFT_ID`.
7. Enter `withdraw ADVERTISEMENT_ID --request-id student-withdraw-1`, replacing
   `ADVERTISEMENT_ID` with the ID recorded in step 3.
8. Enter `advertise water food 6 --request-id student-advertise-2`.
   The request-capacity error is expected; the session stays open.
9. Enter `sync`. Then use `inventory` and `history` to check inventory
   `(28,31,31)` and two completed trades. The server's report in `run/` should
   show `sample exchange completed` and `last_completed_step: 10`.
10. Enter `quit`.

## Connect to the live game

```sh
python client/run_live.py --interactive
```

This uses the provided remote endpoint and client token from `.env` (or an
exported `BAZAAR_TOKEN`, which takes precedence). The observatory token is not
used. Use `ready` after inspecting the initial state, or pass `--ready` at
startup. The administrator controls when the game runs; trading commands are
blocked locally in other phases.

Use actual station and object IDs from the live snapshots. The local
walkthrough is specific to the scripted practice server.

A new connection replaces the previous connection for your station. After a
disconnect, rerun the client, inspect current state and recorded results, and
declare readiness again. Do not blindly resubmit an uncertain trade with a new
request ID. The interface does not persist pending commands across restarts.
