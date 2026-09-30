import contextlib
import io
import unittest
from unittest.mock import AsyncMock, Mock

from helpers import fill_required, snapshot, ad, offer
import bazaar
import bazaar_pb2 as pb
from strategy import Policy, candidates
from automated import AutomatedSession


class StrategyTests(unittest.TestCase):
    def test_offer_lifetime_is_independent_of_advertisement_lifetime(self):
        s = snapshot().state
        ad(s)
        actions = candidates(s, Policy(offer_ttl=4))
        self.assertEqual(next(a for a in actions if a.kind == 'offer').args[-1], s.tick + 4)
        self.assertEqual(next(a for a in actions if a.kind == 'advertise').args[-1], s.tick + 2)
        actions = candidates(s, Policy(offer_ttl=12))
        self.assertEqual(next(a for a in actions if a.kind == 'offer').args[-1],
                         s.tick + s.rules.max_offer_ttl_ticks)
        self.assertEqual(candidates(s), candidates(s, Policy(offer_ttl=2)))
        with self.assertRaises(ValueError):
            Policy(offer_ttl=0)

    def test_earlier_seeking_does_not_increase_spending_floor(self):
        s = snapshot((10, 6, 10)).state
        policy = Policy(imported_seek_ticks=10)
        publication = next(a for a in candidates(s, policy) if a.kind == 'advertise')
        self.assertIn(pb.RESOURCE_FOOD, publication.args[1])
        self.assertNotIn(pb.RESOURCE_FOOD, publication.args[0])
        offer(s, give=(1, 0, 0), receive=(0, 1, 0))
        # Food can be spent down to five; seeking earlier is not a ten-unit floor.
        self.assertTrue(any(a.kind == 'accept' for a in candidates(s, policy)))
        s.self.inventory.food = 5
        self.assertFalse(any(a.kind == 'accept' for a in candidates(s, policy)))

    def test_default_seek_horizon_matches_existing_policy(self):
        s = snapshot((10, 6, 10)).state
        ad(s)
        self.assertEqual(candidates(s), candidates(s, Policy(imported_seek_ticks=5)))
        with self.assertRaises(ValueError):
            Policy(imported_seek_ticks=4)

    def test_emergency_offer_pays_two_for_one(self):
        s = snapshot((10, 0, 10)).state
        ad(s)
        action = next(a for a in candidates(s) if a.kind == 'offer')
        self.assertEqual(action.args[1:3], ((2, 0, 0), (0, 1, 0)))

    def test_emergency_never_spends_reserve(self):
        s = snapshot((4, 0, 10)).state
        ad(s)
        action = next(a for a in candidates(s) if a.kind == 'offer')
        self.assertEqual(action.args[1:3], ((1, 0, 0), (0, 1, 0)))
        s.self.inventory.water = 3
        self.assertFalse(any(a.kind == 'offer' for a in candidates(s)))

    def test_incoming_premium_only_for_emergency_and_capped(self):
        s = snapshot((10, 0, 10)).state
        incoming = offer(s, receive=(2, 0, 0))
        self.assertEqual(candidates(s)[0].kind, 'accept')
        s.self.inventory.food = 1  # Exactly one turn is outside the default emergency.
        self.assertFalse(any(a.kind == 'accept' for a in candidates(s)))
        s.self.inventory.food = 0
        incoming.receive.water = 3
        self.assertFalse(any(a.kind == 'accept' for a in candidates(s, Policy(trade_size=5))))

    def test_premium_must_address_actual_emergency(self):
        s = snapshot((10, 0, 10)).state
        offer(s, give=(0, 0, 1), receive=(2, 0, 0))
        self.assertFalse(any(a.kind == 'accept' for a in candidates(s)))

    def test_emergency_uses_reported_upkeep_and_configurable_horizon(self):
        s = snapshot((10, 1, 10)).state
        s.self.upkeep_per_tick.food = 2
        ad(s)
        action = next(a for a in candidates(s) if a.kind == 'offer')
        self.assertEqual(action.args[1], (2, 0, 0))
        s.self.upkeep_per_tick.food = 1
        action = next(a for a in candidates(s, Policy(emergency_ticks=2)) if a.kind == 'offer')
        self.assertEqual(action.args[1], (2, 0, 0))
        with self.assertRaises(ValueError):
            Policy(emergency_ticks=0)

    def test_specialty_sets_reserve_for_each_planet(self):
        for specialty in (pb.RESOURCE_WATER, pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS):
            s = snapshot((4, 4, 4)).state
            s.self.specialty = specialty
            publication = next(a for a in candidates(s) if a.kind == 'advertise')
            self.assertEqual(publication.args[0], (specialty,))
            self.assertEqual(set(publication.args[1]), {1, 2, 3} - {specialty})

    def test_imported_reserve_is_configurable(self):
        s = snapshot((4, 4, 4)).state
        publication = next(a for a in candidates(s, Policy(imported_reserve_ticks=3)) if a.kind == 'advertise')
        self.assertEqual(publication.args[1], ())
        with self.assertRaises(ValueError):
            Policy(imported_reserve_ticks=0)

    def test_no_imported_gifts_even_with_large_surplus(self):
        s = snapshot((10, 100, 100)).state
        ad(s, seeking=(pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS))
        self.assertFalse(any(a.kind == 'offer' for a in candidates(s)))

    def test_incoming_trade_cannot_spend_imported_reserve(self):
        s = snapshot((1, 5, 10)).state
        offer(s, give=(1, 0, 0), receive=(0, 1, 0))
        self.assertFalse(any(a.kind == 'accept' for a in candidates(s)))
        s.self.inventory.food = 6
        self.assertEqual(candidates(s)[0].kind, 'accept')

    def test_specialty_is_preferred_for_outgoing_payment(self):
        s = snapshot((10, 10, 4)).state
        s.self.specialty = pb.RESOURCE_FOOD
        ad(s, selling=(pb.RESOURCE_COMPONENTS,), seeking=(pb.RESOURCE_WATER, pb.RESOURCE_FOOD))
        action = next(a for a in candidates(s) if a.kind == 'offer')
        self.assertEqual(action.args[1], (0, 1, 0))

    def test_large_last_production_does_not_replace_reserve_stock(self):
        s = snapshot((3, 5, 5)).state
        s.self.last_production.water = 100
        ad(s)
        self.assertFalse(any(a.kind == 'offer' for a in candidates(s)))

    def test_shortage_trade_and_serialization(self):
        s = snapshot().state
        ad(s)
        action = next(a for a in candidates(s) if a.kind == 'offer')
        self.assertEqual(action.args, ('P02', (1, 0, 0), (0, 1, 0), 12))
        self.assertTrue(action.message(s, 'test-request').IsInitialized())

    def test_surplus_gift_and_disable(self):
        s = snapshot((10, 10, 10)).state
        ad(s)
        action = next(a for a in candidates(s) if a.kind == 'offer')
        self.assertEqual(action.args[1:3], ((1, 0, 0), (0, 0, 0)))
        self.assertFalse(any(a.kind == 'offer' for a in candidates(s, Policy(gift_size=0))))

    def test_no_gifts_while_short(self):
        s = snapshot().state
        ad(s, selling=())
        self.assertFalse(any(a.kind == 'offer' for a in candidates(s)))

    def test_incoming_trade_preserves_reserve(self):
        s = snapshot().state
        offer(s)
        self.assertEqual(candidates(s)[0].kind, 'accept')
        s.self.inventory.water = 3
        self.assertFalse(any(a.kind == 'accept' for a in candidates(s)))

    def test_gift_accepted_even_when_short(self):
        s = snapshot((0, 0, 0)).state
        offer(s, receive=(0, 0, 0))
        self.assertEqual(candidates(s)[0].kind, 'accept')

    def test_outgoing_commitments_reserved(self):
        s = snapshot((4, 10, 10)).state
        ad(s)
        offer(s, give=(1, 0, 0), receive=(0, 1, 0), outgoing=True)
        self.assertFalse(any(a.kind == 'offer' for a in candidates(s)))
        # A tick consumed the only surplus unit: withdraw the commitment.
        s.self.inventory.water = 3
        self.assertEqual(candidates(s)[0].kind, 'withdraw')

    def test_expired_offer_is_not_accepted(self):
        s = snapshot().state
        offer(s).expires_tick = s.tick
        self.assertFalse(any(a.kind == 'accept' for a in candidates(s)))

    def test_zero_upkeep_no_division_and_peer_rotation(self):
        s = snapshot((10, 10, 10)).state
        s.self.upkeep_per_tick.CopyFrom(bazaar.bundle())
        ad(s)
        s.directory.items.add(station_id='P03', display_name='Third')
        ad(s, peer='P03')
        first = next(a for a in candidates(s) if a.kind == 'offer').args[0]
        s.tick += 1
        second = next(a for a in candidates(s) if a.kind == 'offer').args[0]
        self.assertNotEqual(first, second)

    def test_stopped_phases_and_failed_station(self):
        s = snapshot().state
        for phase in (pb.PHASE_READY, pb.PHASE_PAUSED, pb.PHASE_FINISHED, pb.PHASE_ABORTED):
            s.phase = phase
            self.assertEqual(candidates(s), [])
        s.phase = pb.PHASE_RUNNING
        s.self.failed_once = True
        self.assertEqual(candidates(s), [])

    def test_does_not_repeat_matching_ad_or_exceed_open_offer_limit(self):
        s = snapshot().state
        own = ad(s, selling=(pb.RESOURCE_WATER, pb.RESOURCE_COMPONENTS), peer='P01', seeking=(pb.RESOURCE_FOOD,))
        ad(s)
        s.rules.max_open_outgoing_offers = 0
        self.assertEqual(candidates(s), [])

    def test_ttl_and_trade_size_limits(self):
        s = snapshot((10, 0, 10)).state
        ad(s)
        s.rules.max_offer_ttl_ticks = 1
        action = next(a for a in candidates(s, Policy(trade_size=1)) if a.kind == 'offer')
        self.assertEqual(action.args, ('P02', (1, 0, 0), (0, 1, 0), 11))


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.record = Mock()
        self.session = AutomatedSession(self.client)
        self.msg = snapshot()

    async def feed(self, msg):
        with contextlib.redirect_stdout(io.StringIO()):
            await self.session.receive(msg)

    async def ready(self):
        await self.feed(self.msg)
        ack = pb.ServerMessage()
        fill_required(ack.readiness)
        ack.readiness.run_id = self.msg.state.run_id
        ack.readiness.snapshot_sequence = self.msg.state.snapshot_sequence
        ack.readiness.ready = True
        await self.feed(ack)

    def result(self, code=pb.RESULT_CODE_OK, retry=None):
        msg = pb.ServerMessage()
        fill_required(msg.result)
        msg.result.run_id = self.msg.state.run_id
        msg.result.request_id = self.session.pending
        msg.result.code = code
        msg.result.ok = code == pb.RESULT_CODE_OK
        msg.result.processed_tick = self.msg.state.tick
        if retry is not None:
            msg.result.retry_after_tick.value = retry
        return msg

    async def test_waits_for_readiness_result_and_state(self):
        await self.feed(self.msg)
        self.assertEqual(self.client.send.await_count, 1)
        self.assertEqual(self.client.send.call_args.args[0].WhichOneof('message'), 'ready')
        # State updates before acknowledgment must not trigger trades.
        await self.feed(self.msg)
        self.assertEqual(self.client.send.await_count, 1)
        await self.ready()
        self.assertEqual(self.client.send.await_count, 2)
        await self.feed(self.msg)
        self.assertEqual(self.client.send.await_count, 2)
        await self.feed(self.result())
        self.assertEqual(self.client.send.await_count, 2)
        await self.feed(self.msg)
        self.assertIsNone(self.session.pending)
        self.assertEqual(self.client.send.await_count, 2)  # Same ad is attempted only once per tick.

    async def test_advisory_sends_nothing(self):
        self.session.advisory = True
        await self.feed(self.msg)
        self.client.send.assert_not_awaited()

    async def test_command_budget_includes_previous_connection(self):
        self.msg.state.rules.new_commands_per_station_per_tick = 1
        r = self.msg.state.request_results.items.add(request_id='old', processed_tick=self.msg.state.tick)
        await self.ready()
        self.assertEqual(self.client.send.await_count, 1)  # Readiness only.

    async def test_request_capacity_stops_trading(self):
        self.msg.state.rules.max_request_records_per_station = 0
        await self.ready()
        self.assertEqual(self.client.send.await_count, 1)

    async def test_rate_limit_cooldown(self):
        await self.ready()
        await self.feed(self.result(pb.RESULT_CODE_RATE_LIMITED, 15))
        self.msg.state.tick = 11
        await self.feed(self.msg)
        self.assertEqual(self.client.send.await_count, 2)
        self.msg.state.tick = 15
        await self.feed(self.msg)
        self.assertEqual(self.client.send.await_count, 3)

    async def test_protocol_capacity_error_and_close(self):
        await self.ready()
        msg = pb.ServerMessage()
        fill_required(msg.protocol_error)
        msg.protocol_error.request_id.value = self.session.pending
        msg.protocol_error.code = pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED
        msg.protocol_error.close_session = False
        await self.feed(msg)
        self.msg.state.tick += 1
        await self.feed(self.msg)
        self.assertEqual(self.client.send.await_count, 2)
        msg.protocol_error.close_session = True
        with self.assertRaises(bazaar.ProtocolViolation):
            await self.feed(msg)

    async def test_run_change_stops_automation(self):
        await self.ready()
        changed = snapshot()
        changed.state.run_id = 'different'
        with self.assertRaises(bazaar.ProtocolViolation):
            await self.feed(changed)


if __name__ == '__main__':
    unittest.main()
