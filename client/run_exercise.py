"""Walk through the practice server exchange and check P01's trade choices.

Start the server first (scripts/start-server.sh), then:
    python client/run_exercise.py
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import bazaar
import bazaar_pb2 as pb
from bazaar import as_tuple, nullable_value
from strategy import candidates


class ExerciseFailed(Exception):
    pass


def check(condition, message):
    if not condition:
        raise ExerciseFailed(message)


async def expect(client, kind):
    msg = await client.recv()
    got = msg.WhichOneof("message")
    check(got == kind, f"expected {kind}, got {bazaar.describe(msg)}")
    return getattr(msg, kind)


async def expect_ok(client, request_id):
    result = await expect(client, "result")
    check(result.request_id == request_id,
          f"result is for {result.request_id!r}, expected {request_id!r}")
    check(result.ok and result.code == pb.RESULT_CODE_OK,
          f"{request_id} failed: {pb.ResultCode.Name(result.code)}")
    return result


async def expect_state(client, world_version, snapshot_sequence, inventory):
    state = await expect(client, "state")
    check(state.world_version == world_version,
          f"world_version {state.world_version}, expected {world_version}")
    check(state.snapshot_sequence == snapshot_sequence,
          f"snapshot_sequence {state.snapshot_sequence}, expected {snapshot_sequence}")
    check(state.tick == 0, f"tick {state.tick}, expected 0")
    check(state.phase == pb.PHASE_RUNNING, f"phase {pb.Phase.Name(state.phase)}")
    check(as_tuple(state.self.inventory) == inventory,
          f"inventory {as_tuple(state.self.inventory)}, expected {inventory}")
    return state


def find(items, **fields):
    """Items whose attributes equal all the given values."""
    return [i for i in items if all(getattr(i, k) == v for k, v in fields.items())]


def step(n, title):
    print(f"\n== Step {n}: {title}")


def check_ad_response(state, selling, seeking, expected_kind, expected_give=None,
                      expected_receive=None):
    """Check the strategy's action for one hypothetical current P02 listing."""
    scenario = pb.State()
    scenario.CopyFrom(state)
    scenario.advertisements.items.clear()
    ad = scenario.advertisements.items.add()
    ad.advertisement_id = "practice-p02-ad"
    ad.station_id = "P02"
    ad.selling.items.extend(selling)
    ad.seeking.items.extend(seeking)
    ad.created_tick = scenario.tick
    ad.expires_tick = scenario.tick + 2
    ad.created_version = scenario.world_version
    ad.status = pb.PUBLICATION_STATUS_ACTIVE
    actions = [a for a in candidates(scenario) if a.kind == "offer"]
    matching = [a for a in actions if a.args[0] == "P02"]
    check(len(matching) == (1 if expected_kind else 0),
          f"expected {1 if expected_kind else 0} P02 offers, got {len(matching)}")
    if expected_kind:
        action = matching[0]
        give, receive = action.args[1], action.args[2]
        check(action.kind == expected_kind, f"expected {expected_kind}, got {action.kind}")
        check(give == expected_give,
              f"offer gives {give}, expected {expected_give}")
        check(receive == expected_receive,
              f"offer receives {receive}, expected {expected_receive}")


async def run(client):
    step(1, "read starting state and confirm readiness")
    state = await expect_state(client, 2, 1, (30, 30, 30))
    run_id = state.run_id
    check(state.self_station_id == "P01", f"station {state.self_station_id}, expected P01")
    check(state.self.specialty == pb.RESOURCE_WATER,
          f"specialty {pb.Resource.Name(state.self.specialty)}, expected RESOURCE_WATER")
    check(any(list(ad.selling.items) == [pb.RESOURCE_FOOD]
              and list(ad.seeking.items) == [pb.RESOURCE_WATER]
              for ad in find(state.advertisements.items, station_id="P02")),
          "P02 has no advertisement selling food and seeking water")

    await client.send(bazaar.ready(run_id, state.snapshot_sequence))
    readiness = await expect(client, "readiness")
    check(readiness.run_id == run_id and readiness.ready and readiness.snapshot_sequence == 1,
          "readiness does not match the declaration")

    step(2, "advertise water for food")
    await client.send(bazaar.advertise(run_id, "student-advertise-1",
                                       [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD], 6))
    await expect_ok(client, "student-advertise-1")
    state = await expect_state(client, 3, 2, (30, 30, 30))
    check(find(state.advertisements.items, station_id="P01",
               status=pb.PUBLICATION_STATUS_ACTIVE),
          "own advertisement missing")

    step(3, "replace advertisement with a request for components")
    await client.send(bazaar.advertise(run_id, "student-advertise-seeking-1",
                                       [], [pb.RESOURCE_COMPONENTS], 6))
    result = await expect_ok(client, "student-advertise-seeking-1")
    advertisement_id = nullable_value(result.object_id)
    check(advertisement_id, "result has no advertisement ID")
    state = await expect_state(client, 4, 3, (30, 30, 30))
    active = find(state.advertisements.items, station_id="P01",
                  status=pb.PUBLICATION_STATUS_ACTIVE)
    check(len(active) == 1 and active[0].advertisement_id == advertisement_id,
          "expected exactly one active advertisement, the new one")
    check(not active[0].selling.items and
          list(active[0].seeking.items) == [pb.RESOURCE_COMPONENTS],
          "new advertisement should sell nothing and seek components")

    step(4, "offer two water for one food")
    await client.send(bazaar.offer(run_id, "student-offer-1", "P02",
                                   bazaar.bundle(water=2), bazaar.bundle(food=1), 6))
    result = await expect_ok(client, "student-offer-1")
    offer_id = nullable_value(result.object_id)
    check(offer_id, "result has no offer ID")
    state = await expect_state(client, 5, 4, (30, 30, 30))
    check(find(state.offers.items, offer_id=offer_id, status=pb.OFFER_STATUS_OPEN),
          f"offer {offer_id} is not open")

    step(5, "observe P02 accept the offer")
    state = await expect_state(client, 6, 5, (28, 31, 30))
    check(find(state.offers.items, offer_id=offer_id, status=pb.OFFER_STATUS_ACCEPTED),
          f"offer {offer_id} was not accepted")
    check(len(state.transactions.items) == 1, "expected one transaction")

    step(6, "observe P02 offer a gift")
    state = await expect_state(client, 7, 6, (28, 31, 30))
    gifts = [o for o in find(state.offers.items, proposer_id="P02", recipient_id="P01",
                             status=pb.OFFER_STATUS_OPEN)
             if as_tuple(o.give) == (0, 0, 1) and as_tuple(o.receive) == (0, 0, 0)]
    check(len(gifts) == 1, "expected one open zero-price offer from P02")
    gift_id = gifts[0].offer_id

    step(7, "accept the gift")
    await client.send(bazaar.accept(run_id, "student-accept-1", gift_id))
    result = await expect_ok(client, "student-accept-1")
    check(nullable_value(result.object_id) == gift_id, "result does not name the gift offer")
    check(nullable_value(result.transaction_id), "result has no transaction ID")
    state = await expect_state(client, 8, 7, (28, 31, 31))
    check(len(state.transactions.items) == 2, "expected two transactions")
    check(find(state.advertisements.items, advertisement_id=advertisement_id,
               status=pb.PUBLICATION_STATUS_ACTIVE),
          "advertisement should still be active")

    step(8, "remove the advertisement")
    await client.send(bazaar.withdraw(run_id, "student-withdraw-1", advertisement_id))
    await expect_ok(client, "student-withdraw-1")
    state = await expect_state(client, 9, 8, (28, 31, 31))
    check(not find(state.advertisements.items, advertisement_id=advertisement_id),
          "advertisement still listed")
    check(len(state.transactions.items) == 2, "expected two transactions")

    step(9, "observe the intentional request-limit error")
    await client.send(bazaar.advertise(run_id, "student-advertise-2",
                                       [pb.RESOURCE_WATER], [pb.RESOURCE_FOOD], 6))
    error = await expect(client, "protocol_error")
    check(error.code == pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED,
          f"error code {pb.ControlCode.Name(error.code)}")
    check(not error.close_session, "server asked to close the session")
    check(nullable_value(error.request_id) == "student-advertise-2",
          "error names the wrong request")

    step(10, "ask for the final state")
    await client.send(bazaar.sync(run_id))
    state = await expect_state(client, 9, 9, (28, 31, 31))
    me = state.self
    check(len(state.transactions.items) == 2, "expected two transactions")
    check(len(state.request_results.items) == 5, "expected five stored results")
    check(as_tuple(me.imported_total) == (0, 1, 1),
          f"imported_total {as_tuple(me.imported_total)}")
    check(as_tuple(me.exported_total) == (2, 0, 0),
          f"exported_total {as_tuple(me.exported_total)}")
    for name in ("produced_total", "consumed_total", "unmet_total"):
        check(as_tuple(getattr(me, name)) == (0, 0, 0), f"{name} should be zero")
    check(me.shortage_ticks == 0, "shortage_ticks should be zero")

    step(11, "retry the completed withdrawal with the same request ID")
    # The server retains command results so a client can safely recover after
    # losing a response. Repeating the exact request must not withdraw twice
    # or create another result; the server returns the cached result and the
    # current state.
    await client.send(bazaar.withdraw(run_id, "student-withdraw-1", advertisement_id))
    retry_result = await expect_ok(client, "student-withdraw-1")
    check(nullable_value(retry_result.object_id) == advertisement_id,
          "cached result does not name the withdrawn advertisement")
    state = await expect_state(client, 9, 10, (28, 31, 31))
    check(not find(state.advertisements.items, advertisement_id=advertisement_id),
          "retry recreated or retained the withdrawn advertisement")
    check(len(state.transactions.items) == 2, "retry changed transaction history")
    check(len(state.request_results.items) == 5,
          "exact retry should not consume another stored-result slot")

    step(12, "choose offers from P02's advertised needs and stock")
    # Use synthetic successive P02 listings because the practice server's
    # scripted exchange only publishes its fixed sample advertisements.
    # First, with all upkeep reserves covered, answer P02's request for our
    # water specialty with a one-unit gift.
    gift_state = pb.State()
    gift_state.CopyFrom(state)
    gift_state.self.inventory.CopyFrom(bazaar.bundle(water=10, food=10, components=10))
    check_ad_response(gift_state, [pb.RESOURCE_FOOD], [pb.RESOURCE_WATER],
                      "offer", (1, 0, 0), (0, 0, 0))

    # Then test a need funded by specialty surplus. Food is below its five-unit
    # reserve; offer at most the policy's two-unit trade budget in water.
    trade_state = pb.State()
    trade_state.CopyFrom(state)
    trade_state.self.inventory.CopyFrom(bazaar.bundle(water=10, food=3, components=10))
    check_ad_response(trade_state, [pb.RESOURCE_FOOD], [pb.RESOURCE_WATER],
                      "offer", (2, 0, 0), (0, 2, 0))

    # P02 asking for our food (which is already below reserve) must not prompt
    # P01 to send it away.
    check_ad_response(trade_state, [], [pb.RESOURCE_FOOD], None)

    check((client.sent, client.received) == (9, 18), "expected 9 sent and 18 received")
    print(f"\nAll steps passed. Sent {client.sent} messages, received {client.received}.")


async def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=bazaar.PRACTICE_URL,
                        help="practice server WebSocket URL (default: %(default)s)")
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument("--credentials", type=Path,
                      default=Path(__file__).resolve().parents[1] / "run/validation-credentials.json",
                      help="P01 credentials file (default: project run/validation-credentials.json)")
    auth.add_argument("--token-env", metavar="VARIABLE",
                      help="explicitly read a token from this environment variable instead")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every message in full")
    args = parser.parse_args()

    if args.token_env:
        token = os.environ.get(args.token_env)
        if not token:
            parser.error(f"environment variable {args.token_env!r} is empty or unset")
    else:
        try:
            token = bazaar.load_token(args.credentials, "P01")
        except FileNotFoundError:
            parser.error(f"credentials file not found: {args.credentials}; start scripts/start-server.sh first")

    try:
        async with bazaar.BazaarClient(args.url, token, verbose=args.verbose) as client:
            await run(client)
    except ExerciseFailed as e:
        print(f"\nFAILED: {e}\nThis runner needs a fresh practice exercise; restart the practice server before retrying.",
              file=sys.stderr)
        return 1
    except (ConnectionRefusedError, TimeoutError):
        print(f"Could not connect to or receive a response from {args.url}. Check that the practice server is running in the same container and the URL is correct.",
              file=sys.stderr)
        return 1
    print("Check the practice server's validation-report.json for its record (normally in run/).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
