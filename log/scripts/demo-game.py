"""A small simulated Bazaar game for trying the live dashboard with a moving game.

NOT the real game rules: four planets with made-up production, upkeep, and peer
behavior, a food shortage around ticks 20-32, and one deliberate stall at tick 40.
The world keeps running between connections, so you can test reconnecting.

    python scripts/demo-game.py            # then connect the client to ws://127.0.0.1:8765/ws
    python scripts/demo-game.py --hard     # harsher economy; our planet usually fails
"""
import argparse
import asyncio
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "client"), str(ROOT / "tests")]
from websockets.asyncio.server import serve
import bazaar
import bazaar_pb2 as pb
from helpers import fill_required

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--port", type=int, default=8765)
parser.add_argument("--tick", type=float, default=0.5, help="seconds per tick (default: 0.5)")
parser.add_argument("--duration", type=int, default=60, help="ticks in the game (default: 60)")
parser.add_argument("--hard", action="store_true", help="scarcer food and harsher health damage")
ARGS = parser.parse_args()
PORT, TICK, DURATION, EASY = ARGS.port, ARGS.tick, ARGS.duration, not ARGS.hard
R = (pb.RESOURCE_WATER, pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS)
NAMES = {"P01": "Aquila", "P02": "Pelagos", "P03": "Ferrum", "P04": "Verdant"}
SPEC = {"P01": 0, "P02": 1, "P03": 2, "P04": 1}
random.seed(7)


def mkad(**kw):
    a = pb.Advertisement(); fill_required(a)
    for k, v in kw.items():
        getattr(a, k).CopyFrom(v) if hasattr(v, "DESCRIPTOR") else setattr(a, k, v)
    return a


class World:
    def __init__(self):
        self.tick, self.version, self.seq, self.n = 0, 1, 0, 0
        self.phase = pb.PHASE_READY
        self.inv = {s: [12, 12, 12] for s in NAMES}
        self.health = {s: 100 for s in NAMES}
        self.failed = {s: None for s in NAMES}
        self.offers, self.ads, self.txs, self.results = {}, {}, [], []

    def nid(self, p):
        self.n += 1
        return f"{p}-{self.n}"

    def settle(self, o):
        a, b = o.proposer_id, o.recipient_id
        give, recv = bazaar.as_tuple(o.give), bazaar.as_tuple(o.receive)
        if any(self.inv[a][i] < give[i] for i in range(3)) or any(self.inv[b][i] < recv[i] for i in range(3)):
            return False
        for i in range(3):
            self.inv[a][i] += recv[i] - give[i]
            self.inv[b][i] += give[i] - recv[i]
        o.status = pb.OFFER_STATUS_ACCEPTED
        o.closed_tick.value = self.tick
        t = pb.Transaction(transaction_id=self.nid("tx"), offer_id=o.offer_id, proposer_id=a, recipient_id=b,
                           give=o.give, receive=o.receive, settled_tick=self.tick, settled_version=self.version)
        o.transaction_id.value = t.transaction_id
        self.txs.append(t)
        return True

    def offer(self, frm, to, give, recv, ttl=3):
        o = pb.Offer()
        fill_required(o)
        o.offer_id, o.proposer_id, o.recipient_id = self.nid("offer"), frm, to
        o.give.CopyFrom(bazaar.bundle(*give)); o.receive.CopyFrom(bazaar.bundle(*recv))
        o.created_tick, o.expires_tick, o.status = self.tick, self.tick + ttl, pb.OFFER_STATUS_OPEN
        o.closed_tick.null = True; o.transaction_id.null = True
        self.offers[o.offer_id] = o
        return o

    def advance(self):
        self.tick += 1
        drought = 20 <= self.tick <= 32  # food producers struggle mid-game
        for s in NAMES:
            if self.failed[s] is not None:
                continue
            prod = [0, 0, 0]
            prod[SPEC[s]] = (2 if EASY else 1) if (drought and SPEC[s] == 1) else 3
            unmet = 0
            for i in range(3):
                self.inv[s][i] += prod[i]
                use = 1
                unmet += max(0, use - self.inv[s][i])
                self.inv[s][i] = max(0, self.inv[s][i] - use)
            self.health[s] = max(0, min(100, self.health[s] - (2 if EASY else 8) * unmet + (2 if not unmet else 0)))
            if self.health[s] == 0:
                self.failed[s] = self.tick
        for o in self.offers.values():
            if o.status == pb.OFFER_STATUS_OPEN and self.tick >= o.expires_tick:
                o.status = pb.OFFER_STATUS_EXPIRED; o.closed_tick.value = self.tick
        for a in self.ads.values():
            if a.status == pb.PUBLICATION_STATUS_ACTIVE and self.tick >= a.expires_tick:
                a.status = pb.PUBLICATION_STATUS_EXPIRED
        # Peers: accept some offers addressed to them, occasionally offer to us.
        for o in list(self.offers.values()):
            if o.status == pb.OFFER_STATUS_OPEN and o.recipient_id != "P01" and random.random() < 0.45:
                self.settle(o)
        for peer in ("P02", "P03", "P04"):
            if self.failed[peer] is None and random.random() < (0.7 if EASY else 0.22):
                mine, want = SPEC[peer], random.choice([i for i in range(3) if i != SPEC[peer]])
                give = [0, 0, 0]; give[mine] = random.randint(1, 2)
                recv = [0, 0, 0]
                if random.random() < 0.7:
                    recv[want] = random.randint(1, 2)
                self.offer(peer, "P01", give, recv)
            if random.random() < 0.15:
                a = mkad(advertisement_id=self.nid("ad"), station_id=peer, created_tick=self.tick,
                                     expires_tick=self.tick + 4, status=pb.PUBLICATION_STATUS_ACTIVE)
                a.selling.items.append(R[SPEC[peer]]); a.seeking.items.append(R[(SPEC[peer] + 1) % 3])
                self.ads[a.advertisement_id] = a
        self.version += 1

    def state(self):
        self.seq += 1
        m = pb.ServerMessage(); fill_required(m.state); s = m.state
        s.run_id, s.self_station_id, s.phase = "mini-run", "P01", self.phase
        s.tick, s.snapshot_sequence, s.world_version = self.tick, self.seq, self.version
        s.rules.duration_ticks, s.rules.tick_duration_ms, s.rules.max_health = DURATION, int(TICK * 1000), 100
        s.rules.max_publication_ttl_ticks = s.rules.max_offer_ttl_ticks = 6
        s.rules.new_commands_per_station_per_tick, s.rules.max_request_records_per_station = 3, 10000
        s.rules.max_open_outgoing_offers, s.rules.max_command_bytes = 3, 16384
        for sid, name in NAMES.items():
            s.directory.items.add(station_id=sid, display_name=name)
        me = s.self
        me.station_id, me.health, me.specialty = "P01", self.health["P01"], R[SPEC["P01"]]
        me.inventory.CopyFrom(bazaar.bundle(*self.inv["P01"]))
        me.upkeep_per_tick.CopyFrom(bazaar.bundle(1, 1, 1))
        me.failed_once = self.failed["P01"] is not None
        if self.failed["P01"] is None: me.first_failure_tick.null = True
        else: me.first_failure_tick.value = self.failed["P01"]
        s.offers.items.extend(o for o in self.offers.values() if "P01" in (o.proposer_id, o.recipient_id))
        s.advertisements.items.extend(a for a in self.ads.values() if a.status == pb.PUBLICATION_STATUS_ACTIVE)
        s.transactions.items.extend(t for t in self.txs if "P01" in (t.proposer_id, t.recipient_id))
        s.request_results.items.extend(self.results[-50:])
        if self.phase == pb.PHASE_FINISHED:
            s.outcome.value.self_failed = me.failed_once; s.outcome.value.aborted = False
            s.outcome.value.collective_success.value = True
        else:
            s.outcome.null = True
        return m

    def handle(self, cmd):
        kind = cmd.WhichOneof("message"); c = getattr(cmd, kind)
        r = pb.Result(); fill_required(r)
        r.run_id, r.request_id, r.processed_tick, r.processed_version = "mini-run", c.request_id, self.tick, self.version
        r.transaction_id.null = True; r.retry_after_tick.null = True
        r.ok, r.code = True, pb.RESULT_CODE_OK
        if kind == "advertise":
            a = mkad(advertisement_id=self.nid("ad"), station_id="P01", selling=c.body.selling,
                                 seeking=c.body.seeking, created_tick=self.tick, expires_tick=c.body.expires_tick,
                                 status=pb.PUBLICATION_STATUS_ACTIVE)
            self.ads[a.advertisement_id] = a; r.object_id.value = a.advertisement_id
        elif kind == "offer":
            give = bazaar.as_tuple(c.body.give)
            if any(self.inv["P01"][i] < give[i] for i in range(3)):
                r.ok, r.code = False, pb.RESULT_CODE_INSUFFICIENT_RESOURCES; r.object_id.null = True
            else:
                o = self.offer("P01", c.body.recipient_id, give, bazaar.as_tuple(c.body.receive),
                               c.body.expires_tick - self.tick)
                r.object_id.value = o.offer_id
        elif kind == "accept":
            o = self.offers.get(c.body.offer_id)
            if o is None or o.status != pb.OFFER_STATUS_OPEN:
                r.ok, r.code = False, pb.RESULT_CODE_NOT_OPEN; r.object_id.null = True
            elif not self.settle(o):
                r.ok, r.code = False, pb.RESULT_CODE_INSUFFICIENT_RESOURCES; r.object_id.null = True
            else:
                r.object_id.value = o.offer_id; r.transaction_id.value = o.transaction_id.value
        elif kind == "withdraw":
            r.object_id.value = c.body.object_id
            if c.body.object_id in self.offers: self.offers[c.body.object_id].status = pb.OFFER_STATUS_WITHDRAWN
            if c.body.object_id in self.ads: self.ads[c.body.object_id].status = pb.PUBLICATION_STATUS_WITHDRAWN
        self.results.append(r)
        return pb.ServerMessage(result=r)


WORLD = World()
CLIENTS = set()
TICKER = None


async def broadcast(m):
    for ws in list(CLIENTS):
        try:
            await ws.send(m.SerializeToString())
        except Exception:
            CLIENTS.discard(ws)


async def ticker():
    w = WORLD
    await asyncio.sleep(1.5)
    w.phase = pb.PHASE_RUNNING
    await broadcast(w.state())
    while w.tick < DURATION and w.failed["P01"] is None:
        await asyncio.sleep(TICK * (20 if w.tick == 40 else 1))  # one deliberate stall
        w.advance()
        await broadcast(w.state())
    w.phase = pb.PHASE_FINISHED
    await broadcast(w.state())
    await asyncio.sleep(1)
    for ws in list(CLIENTS):
        await ws.close()


async def handler(ws):
    global TICKER
    w = WORLD
    send = lambda m: ws.send(m.SerializeToString())
    CLIENTS.add(ws)
    await send(w.state())
    if TICKER is None:
        TICKER = asyncio.create_task(ticker())
    try:
        async for raw in ws:
            cmd = pb.ClientMessage.FromString(raw)
            kind = cmd.WhichOneof("message")
            if kind == "ready":
                ack = pb.ServerMessage(); fill_required(ack.readiness)
                ack.readiness.run_id, ack.readiness.ready = "mini-run", True
                ack.readiness.snapshot_sequence = cmd.ready.snapshot_sequence
                await send(ack)
            elif kind == "sync":
                await send(w.state())
            else:
                await send(w.handle(cmd)); w.version += 1
                await send(w.state())
    finally:
        CLIENTS.discard(ws)


async def main():
    async with serve(handler, "127.0.0.1", PORT, subprotocols=[bazaar.SUBPROTOCOL]):
        print(f"Demo game listening on ws://127.0.0.1:{PORT}/ws — it starts when the first client connects. Ctrl+C to stop.", flush=True)
        await asyncio.Future()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
