import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'client'))
import bazaar
import bazaar_pb2 as pb
from manual import ManualSession


from helpers import state_message


class ManualTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.session = ManualSession(self.client)
        with contextlib.redirect_stdout(io.StringIO()):
            self.session.receive(state_message())

    async def make_ready(self):
        await self.session.command('ready')
        ack = pb.ServerMessage()
        ack.readiness.run_id = 'test-run'
        ack.readiness.snapshot_sequence = 1
        ack.readiness.ready = True
        with contextlib.redirect_stdout(io.StringIO()):
            self.session.receive(ack)

    async def test_readiness_and_phase_gates(self):
        with self.assertRaisesRegex(ValueError, 'ready'):
            await self.session.command('offer P02 2,0,0 0,1,0 6')
        await self.make_ready()
        self.session.state.phase = pb.PHASE_PAUSED
        with self.assertRaisesRegex(ValueError, 'PHASE_RUNNING'):
            await self.session.command('offer P02 2,0,0 0,1,0 6')
        self.assertEqual(self.client.send.await_count, 1)

    async def test_offer_uses_current_run_and_relative_expiry(self):
        await self.make_ready()
        await self.session.command('offer P02 2,0,0 0,1,0 6 --request-id example')
        msg = self.client.send.call_args.args[0]
        self.assertEqual(msg.offer.run_id, 'test-run')
        self.assertEqual(msg.offer.body.expires_tick, 16)
        self.assertEqual(bazaar.as_tuple(msg.offer.body.give), (2, 0, 0))
        self.assertTrue(msg.IsInitialized())
        with self.assertRaisesRegex(ValueError, 'previous command'):
            await self.session.command('advertise water food 6')
        result = pb.ServerMessage()
        result.result.request_id = 'example'
        with contextlib.redirect_stdout(io.StringIO()):
            self.session.receive(result)
            self.session.receive(state_message())
        with self.assertRaisesRegex(ValueError, 'already been used'):
            await self.session.command('offer P02 2,0,0 0,1,0 6 --request-id example')

    async def test_invalid_inputs_never_send(self):
        await self.make_ready()
        self.client.send.reset_mock()
        for command in ('offer P02 -1,0,0 0,1,0 6', 'offer P02 2,0 0,1,0 6',
                        'offer P02 2,0,0 0,1,0 7', 'advertise water food 0',
                        'advertise water,water food 6', 'accept missing',
                        'withdraw missing', 'offer P99 2,0,0 0,1,0 6'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                await self.session.command(command)
        self.client.send.assert_not_awaited()

    async def test_nonclosing_error_releases_pending_command(self):
        await self.make_ready()
        await self.session.command('advertise - components 6 --request-id ad1')
        msg = self.client.send.call_args.args[0]
        self.assertTrue(msg.advertise.body.HasField('selling'))
        error = pb.ServerMessage()
        error.protocol_error.request_id.value = 'ad1'
        error.protocol_error.close_session = False
        self.session.receive(error)
        self.assertIsNone(self.session.pending_command)
        error.protocol_error.close_session = True
        with self.assertRaises(bazaar.ProtocolViolation):
            self.session.receive(error)

    async def test_changed_run_requires_reconnect(self):
        msg = state_message()
        msg.state.run_id = 'new-run'
        with self.assertRaises(bazaar.ProtocolViolation):
            self.session.receive(msg)

    async def test_snapshot_recovers_recorded_pending_command(self):
        await self.make_ready()
        await self.session.command('advertise water food 6 --request-id recorded')
        snapshot = state_message()
        snapshot.state.request_results.items.add(request_id='recorded')
        with contextlib.redirect_stdout(io.StringIO()):
            self.session.receive(snapshot)
        self.assertIsNone(self.session.pending_command)

    async def test_incoming_offer_display_uses_our_perspective(self):
        offer = self.session.state.offers.items.add(
            offer_id='gift', proposer_id='P02', recipient_id='P01',
            status=pb.OFFER_STATUS_OPEN, expires_tick=16)
        offer.give.CopyFrom(bazaar.bundle(components=1))
        offer.receive.CopyFrom(bazaar.bundle())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            await self.session.command('offers')
        self.assertIn('You pay (0, 0, 0); you get (0, 0, 1)', output.getvalue())
        self.client.send.assert_not_awaited()

    async def test_failed_station_and_ended_phases_block_trades(self):
        await self.make_ready()
        self.client.send.reset_mock()
        for phase in (pb.PHASE_READY, pb.PHASE_PAUSED, pb.PHASE_FINISHED, pb.PHASE_ABORTED):
            self.session.state.phase = phase
            with self.assertRaisesRegex(ValueError, 'PHASE_RUNNING'):
                await self.session.command('advertise water food 6')
        self.session.state.phase = pb.PHASE_RUNNING
        self.session.state.self.failed_once = True
        with self.assertRaisesRegex(ValueError, 'failed'):
            await self.session.command('advertise water food 6')
        self.client.send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
