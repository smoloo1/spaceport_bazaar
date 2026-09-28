"""Local WebSocket simulation of automated barter, gifts, readiness, and game phases.

Run: python tests/check_automated_local.py. Does not contact the live game.
"""
import asyncio
import contextlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
from websockets.asyncio.server import serve
import bazaar
import bazaar_pb2 as pb
from automated import run_automated
from journal import Journal
from helpers import fill_required
from helpers import snapshot, ad


async def scenario(ws):
    assert ws.request.headers['Authorization'] == 'Bearer local-test-token'
    assert ws.subprotocol == bazaar.SUBPROTOCOL
    msg = snapshot()
    s = msg.state
    s.phase = pb.PHASE_READY
    ad(s)
    async def send_state():
        s.snapshot_sequence += 1
        s.world_version += 1
        await ws.send(msg.SerializeToString())
    async def command(kind):
        raw = await asyncio.wait_for(ws.recv(), 3)
        assert isinstance(raw, bytes)
        cmd = pb.ClientMessage.FromString(raw)
        assert cmd.IsInitialized() and cmd.WhichOneof('message') == kind, cmd
        return getattr(cmd, kind)
    async def result(cmd, object_id):
        response = pb.ServerMessage()
        fill_required(response.result)
        r = response.result
        r.run_id = s.run_id
        r.request_id = cmd.request_id
        r.ok = True
        r.code = pb.RESULT_CODE_OK
        r.processed_tick = s.tick
        r.object_id.value = object_id
        r.transaction_id.null = True
        r.retry_after_tick.null = True
        s.request_results.items.add().CopyFrom(r)
        await ws.send(response.SerializeToString())
    await send_state()
    ready = await command('ready')
    ack = pb.ServerMessage()
    fill_required(ack.readiness)
    ack.readiness.run_id = ready.run_id
    ack.readiness.snapshot_sequence = ready.snapshot_sequence
    ack.readiness.ready = True
    await ws.send(ack.SerializeToString())
    try:
        await asyncio.wait_for(ws.recv(), .1)
        raise AssertionError('Trading happened before the game started')
    except asyncio.TimeoutError:
        pass
    s.phase = pb.PHASE_RUNNING
    await send_state()
    trade = await command('offer')
    assert bazaar.as_tuple(trade.body.give) == (1, 0, 0)
    assert bazaar.as_tuple(trade.body.receive) == (0, 1, 0)
    # Simulate immediate peer acceptance; use a new authoritative inventory.
    s.self.inventory.CopyFrom(bazaar.bundle(9, 5, 10))
    await result(trade, 'barter')
    await send_state()
    advert = await command('advertise')
    own = ad(s, peer='P01', selling=tuple(advert.body.selling.items), seeking=tuple(advert.body.seeking.items))
    own.expires_tick = advert.body.expires_tick
    await result(advert, own.advertisement_id)
    await send_state()
    # Next tick: a supplied station can help a peer without manual input.
    s.tick += 1
    s.self.inventory.CopyFrom(bazaar.bundle(8, 5, 9))
    await send_state()
    gift = await command('offer')
    assert bazaar.as_tuple(gift.body.give) == (1, 0, 0)
    assert bazaar.as_tuple(gift.body.receive) == (0, 0, 0)
    s.self.inventory.water -= 1
    await result(gift, 'gift')
    s.phase = pb.PHASE_FINISHED
    await send_state()
    try:
        await asyncio.wait_for(ws.recv(), .1)
        raise AssertionError('Trading happened after game ended')
    except asyncio.TimeoutError:
        pass
    assert all(v >= reserve for v, reserve in zip(bazaar.as_tuple(s.self.inventory), (3, 5, 5)))


async def main():
    outcome = asyncio.get_running_loop().create_future()
    async def handler(ws):
        try:
            await scenario(ws)
        except Exception as exc:
            outcome.set_exception(exc)
        else:
            outcome.set_result(None)
    async with serve(handler, '127.0.0.1', 0, subprotocols=[bazaar.SUBPROTOCOL]) as server:
        port = server.sockets[0].getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory, 'local-test-token')
            try:
                async with bazaar.BazaarClient(f'ws://127.0.0.1:{port}/ws', 'local-test-token', journal=journal) as client:
                    task = asyncio.create_task(run_automated(client))
                    try:
                        await asyncio.wait_for(outcome, 10)
                    finally:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
            finally:
                journal.close()
            import json
            raw = journal.path.read_text()
            assert 'local-test-token' not in raw
            entries = [json.loads(line) for line in raw.splitlines()]
            decisions = {e['request_id'] for e in entries if e['event'] == 'decision' and e.get('request_id')}
            results = {e['message']['result']['request_id'] for e in entries
                       if e['event'] == 'message' and 'result' in e['message']}
            assert len(results) == 3 and results <= decisions
            assert entries[-1]['event'] == 'session_end'
            print('PASS: decision reasons correlate with all trade results; token absent from saved history')
    print('PASS: automated readiness, pause gate, shortage barter, peer gift, reserve preservation, and finished-game gate over local WebSockets')


if __name__ == '__main__':
    asyncio.run(main())
