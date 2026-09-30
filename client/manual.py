"""Interactive controls; snapshots remain authoritative and trades are user initiated."""

import asyncio
import shlex
import sys
import uuid

from google.protobuf import text_format

import bazaar
import bazaar_pb2 as pb

HELP = """Commands:
  help | inventory | state | offers | ads | stations | rules | history
  ready                         declare readiness using the latest snapshot
  sync                          request a fresh snapshot
  advertise SELLING SEEKING TTL  resource lists: water,food,components or -
  offer STATION GIVE RECEIVE TTL bundles: water,food,components quantities
  accept OFFER_ID
  withdraw OBJECT_ID
  quit
Examples:
  advertise water food 6
  offer P02 2,0,0 0,1,0 6
TTL is a positive number of ticks from the current tick.
Offer GIVE is what you pay; RECEIVE is what you ask for.
Trading commands accept --request-id ID for the README's practice request IDs.
Otherwise a unique ID is generated. Wait for a result and state before continuing.
"""


def uint(text):
    if not text.isascii() or not text.isdecimal() or int(text) > 2**64 - 1:
        raise ValueError("Quantities must be whole numbers from 0 through 18446744073709551615.")
    return int(text)


def bundle(text):
    parts = text.split(",")
    if len(parts) != 3:
        raise ValueError("Use three quantities: water,food,components (for example 2,0,0).")
    return bazaar.bundle(*(uint(p) for p in parts))


def resources(text):
    if text == "-":
        return []
    names = {"water": pb.RESOURCE_WATER, "food": pb.RESOURCE_FOOD,
             "components": pb.RESOURCE_COMPONENTS}
    parts = text.split(",")
    if any(p not in names for p in parts) or len(set(parts)) != len(parts):
        raise ValueError("Use unique resource names water,food,components, or - for none.")
    return [names[p] for p in parts]


class ManualSession:
    def __init__(self, client):
        self.client = client
        self.state = None
        self.ready = False
        self.pending_ready = None
        self.pending_command = None
        self.result_received = False
        self.used_ids = set()

    def receive(self, msg):
        kind = msg.WhichOneof("message")
        if kind == "state":
            if self.state is not None and msg.state.run_id != self.state.run_id:
                raise bazaar.ProtocolViolation("Run changed; reconnect to discard the previous session.")
            self.state = msg.state
            if self.result_received or any(
                    r.request_id == self.pending_command for r in self.state.request_results.items):
                self.pending_command = None
                self.result_received = False
            s = self.state
            print(f"Station {s.self_station_id} | {pb.Phase.Name(s.phase)} | "
                  f"health {s.self.health} | tick {s.tick}", flush=True)
        elif kind == "readiness":
            ack = msg.readiness
            if self.pending_ready != (ack.run_id, ack.snapshot_sequence) or not ack.ready:
                raise bazaar.ProtocolViolation("Unexpected readiness acknowledgment.")
            self.pending_ready = None
            self.ready = True
            print("Readiness confirmed.", flush=True)
        elif kind == "result":
            if msg.result.request_id == self.pending_command:
                self.result_received = True
            retry_tick = bazaar.nullable_value(msg.result.retry_after_tick)
            if retry_tick is not None:
                print(f"Server reported retry_after_tick={retry_tick}; no automatic retry.", flush=True)
        elif kind == "protocol_error":
            if msg.protocol_error.close_session:
                raise bazaar.ProtocolViolation("Server requested session closure.")
            request_id = bazaar.nullable_value(msg.protocol_error.request_id)
            if request_id == self.pending_command:
                self.pending_command = None
                self.result_received = False
            if self.pending_ready is not None:
                self.pending_ready = None
                self.ready = False
                print("Readiness was not confirmed. Check the error before trying ready again.", flush=True)

    def show(self, name):
        if name == "inventory":
            me = self.state.self
            print(f"Health: {me.health}; failed: {me.failed_once}", flush=True)
            print("Resource       Inventory   Upkeep/tick   Last production", flush=True)
            for resource in ("water", "food", "components"):
                print(f"{resource:14} {getattr(me.inventory, resource):9} "
                      f"{getattr(me.upkeep_per_tick, resource):13} "
                      f"{getattr(me.last_production, resource):17}", flush=True)
            return
        if name == "offers":
            print("Amounts are (water, food, components).", flush=True)
            for offer in self.state.offers.items:
                print(f"{offer.offer_id}: {offer.proposer_id} -> {offer.recipient_id} "
                      f"{pb.OfferStatus.Name(offer.status)} expires at tick {offer.expires_tick}", flush=True)
                if offer.recipient_id == self.state.self_station_id:
                    print(f"  You pay {bazaar.as_tuple(offer.receive)}; "
                          f"you get {bazaar.as_tuple(offer.give)}", flush=True)
                else:
                    print(f"  Proposer gives {bazaar.as_tuple(offer.give)}; "
                          f"asks for {bazaar.as_tuple(offer.receive)}", flush=True)
            if not self.state.offers.items:
                print("(no offers)", flush=True)
            return
        fields = {"offers": "offers", "ads": "advertisements", "stations": "directory",
                  "rules": "rules", "history": "transactions"}
        value = self.state if name == "state" else getattr(self.state, fields[name])
        print(text_format.MessageToString(value) or "(empty)", flush=True)

    async def command(self, line):
        words = shlex.split(line)
        if not words:
            return True
        name, *args = words
        if name in ("quit", "help") and not args:
            if name == "help":
                print(HELP, flush=True)
                return True
            return False
        if self.state is None:
            raise ValueError("Wait for the first server state.")
        s = self.state
        if name in ("inventory", "state", "offers", "ads", "stations", "rules", "history") and not args:
            self.show(name)
            return True
        if name == "sync" and not args:
            await self.client.send(bazaar.sync(s.run_id))
            return True
        if name == "ready" and not args:
            if self.ready or self.pending_ready:
                raise ValueError("Readiness is already confirmed or awaiting acknowledgment.")
            self.pending_ready = (s.run_id, s.snapshot_sequence)
            await self.client.send(bazaar.ready(*self.pending_ready))
            return True
        arities = {"advertise": 3, "offer": 4, "accept": 1, "withdraw": 1}
        request_id = "manual-" + uuid.uuid4().hex
        if len(args) >= 2 and args[-2] == "--request-id":
            request_id = args[-1]
            args = args[:-2]
        if name not in arities or len(args) != arities[name]:
            raise ValueError("Unknown command or wrong number of arguments. Type help.")
        if not self.ready:
            raise ValueError("Type ready and wait for confirmation before trading.")
        if s.phase != pb.PHASE_RUNNING:
            raise ValueError("Trading requires PHASE_RUNNING; the administrator controls the phase.")
        if s.self.failed_once or s.self.health == 0:
            raise ValueError("This station has failed and cannot trade.")
        if self.pending_command:
            raise ValueError("Wait for the previous command's result and state; use sync if needed.")
        if not (1 <= len(request_id) <= 64) or any(
                c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in request_id):
            raise ValueError("Request IDs must be 1–64 letters, digits, underscores, or hyphens.")
        if request_id in self.used_ids or any(r.request_id == request_id for r in s.request_results.items):
            raise ValueError("That request ID has already been used. Choose a new ID.")
        if name in ("advertise", "offer"):
            ttl = uint(args[-1])
            limit = (s.rules.max_publication_ttl_ticks if name == "advertise"
                     else s.rules.max_offer_ttl_ticks)
            if not 1 <= ttl <= limit or s.tick + ttl > 2**64 - 1:
                raise ValueError(f"TTL must be between 1 and {limit} ticks without overflowing the tick counter.")
            expires = s.tick + ttl
        if name == "advertise":
            msg = bazaar.advertise(s.run_id, request_id, resources(args[0]), resources(args[1]), expires)
        elif name == "offer":
            if args[0] == s.self_station_id or not any(p.station_id == args[0] for p in s.directory.items):
                raise ValueError("Choose another station from stations.")
            msg = bazaar.offer(s.run_id, request_id, args[0], bundle(args[1]), bundle(args[2]), expires)
        elif name == "accept":
            if not any(o.offer_id == args[0] and o.recipient_id == s.self_station_id
                       and o.status == pb.OFFER_STATUS_OPEN and o.expires_tick > s.tick
                       for o in s.offers.items):
                raise ValueError("Choose an unexpired open offer addressed to your station from offers.")
            msg = bazaar.accept(s.run_id, request_id, args[0])
        else:
            own_ad = any(a.advertisement_id == args[0] and a.station_id == s.self_station_id
                         and a.status == pb.PUBLICATION_STATUS_ACTIVE for a in s.advertisements.items)
            own_offer = any(o.offer_id == args[0] and o.proposer_id == s.self_station_id
                            and o.status == pb.OFFER_STATUS_OPEN for o in s.offers.items)
            if not (own_ad or own_offer):
                raise ValueError("Choose your own active advertisement or open outgoing offer.")
            msg = bazaar.withdraw(s.run_id, request_id, args[0])
        if len(msg.SerializeToString()) > min(s.rules.max_command_bytes, bazaar.MAX_COMMAND_BYTES):
            raise ValueError("Command exceeds the server's size limit.")
        self.pending_command = request_id
        self.used_ids.add(request_id)
        await self.client.send(msg)
        return True


async def interact(client, declare_ready=False):
    session = ManualSession(client)
    # Read state before accepting input or declaring readiness.
    first = await client.recv()
    if first.WhichOneof("message") != "state":
        raise bazaar.ProtocolViolation("Expected initial state.")
    session.receive(first)
    print(HELP, flush=True)
    if declare_ready:
        await session.command("ready")

    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    async def receive_updates():
        while True:
            session.receive(await client.recv(timeout=None))

    async def read_commands():
        while True:
            print("bazaar> ", end="", flush=True)
            line = await reader.readline()
            if not line:
                return
            try:
                if not await session.command(line.decode().strip()):
                    return
            except ValueError as exc:
                print(f"Cannot send: {exc}", flush=True)

    tasks = [asyncio.create_task(receive_updates()), asyncio.create_task(read_commands())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        transport.close()
