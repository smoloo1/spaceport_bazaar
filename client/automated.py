"""Execute cooperative decisions one at a time using authoritative snapshots."""
import uuid
from collections import deque
from dataclasses import asdict

import bazaar
import bazaar_pb2 as pb
from strategy import Policy, candidates


class AutomatedSession:
    def __init__(self, client, policy=Policy(), advisory=False):
        self.client = client
        self.policy = policy
        self.advisory = advisory
        self.state = None
        self.ready = False
        self.readiness = None
        self.pending = None
        self.result_received = False
        self.tick = None
        self.commands_this_tick = set()
        self.attempted = set()
        self.cooldown_until = 0
        self.capacity_exhausted = False
        self.production_samples = deque(maxlen=5)
        self.production_sample_tick = None
        self.client.record('policy', mode='advisory' if advisory else 'automated', settings=asdict(policy))

    def record_decision(self, reason, action=None, request_id=None):
        s = self.state
        self.client.record('decision', run_id=s.run_id, tick=s.tick,
                           snapshot_sequence=s.snapshot_sequence, world_version=s.world_version,
                           station_id=s.self_station_id, reason=reason,
                           action=action.kind if action else None,
                           arguments=action.args if action else None, request_id=request_id)

    async def receive(self, msg):
        kind = msg.WhichOneof('message')
        if kind == 'state':
            s = msg.state
            if self.state is not None and s.run_id != self.state.run_id:
                raise bazaar.ProtocolViolation('Run changed; reconnect before continuing automation.')
            self.state = s
            if s.tick != self.production_sample_tick:
                self.production_samples.append(getattr(
                    s.self.last_production,
                    {pb.RESOURCE_WATER: 'water', pb.RESOURCE_FOOD: 'food',
                     pb.RESOURCE_COMPONENTS: 'components'}[s.self.specialty]))
                self.production_sample_tick = s.tick
            if s.tick != self.tick:
                self.tick = s.tick
                self.commands_this_tick.clear()
                self.attempted.clear()
            self.commands_this_tick.update(r.request_id for r in s.request_results.items
                                           if r.processed_tick == s.tick)
            if self.pending and (self.result_received or any(
                    r.request_id == self.pending for r in s.request_results.items)):
                self.pending = None
                self.result_received = False
            if not self.advisory and not self.ready and self.readiness is None:
                self.readiness = (s.run_id, s.snapshot_sequence)
                await self.client.send(bazaar.ready(*self.readiness))
        elif kind == 'readiness':
            ack = msg.readiness
            if self.readiness != (ack.run_id, ack.snapshot_sequence) or not ack.ready:
                raise bazaar.ProtocolViolation('Automation readiness was not confirmed.')
            self.ready = True
            self.readiness = None
            print('Automation readiness confirmed.', flush=True)
        elif kind == 'result':
            if msg.result.request_id != self.pending:
                raise bazaar.ProtocolViolation('Unexpected command result; stopping automation.')
            self.result_received = True
            retry_tick = bazaar.nullable_value(msg.result.retry_after_tick)
            if retry_tick is not None:
                self.cooldown_until = max(self.cooldown_until, retry_tick)
            if msg.result.code == pb.RESULT_CODE_RATE_LIMITED and retry_tick is None:
                self.cooldown_until = max(self.cooldown_until, self.state.tick + 1)
            # Wait for the accompanying state before deciding anything else.
            return
        elif kind == 'protocol_error':
            err = msg.protocol_error
            if err.close_session or self.readiness is not None:
                raise bazaar.ProtocolViolation('Server rejected the automated session.')
            if err.code == pb.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED:
                self.capacity_exhausted = True
                print('Request capacity exhausted; automation will only observe.', flush=True)
            if bazaar.nullable_value(err.request_id) == self.pending:
                self.pending = None
                self.result_received = False
            # Never immediately retry after a protocol error. Wait for another state.
            return
        await self.advance()

    async def advance(self):
        s = self.state
        if s is None:
            return
        actions = candidates(s, self.policy, sum(self.production_samples))
        if self.advisory:
            action = next((a for a in actions if a.key not in self.attempted), None)
            if action:
                self.attempted.add(action.key)
                self.record_decision(action.reason, action)
                print(f'Advice: {action.kind} {action.args}: {action.reason}', flush=True)
            else:
                self.record_decision('no_new_advisory_action')
            return
        if not self.ready or self.pending or self.capacity_exhausted or s.tick < self.cooldown_until:
            reason = ('waiting_for_readiness' if not self.ready else
                      'waiting_for_command_result_and_state' if self.pending else
                      'request_capacity_exhausted' if self.capacity_exhausted else 'rate_limit_cooldown')
            self.record_decision(reason)
            return
        # Count recorded commands as well as commands sent locally. This also
        # respects records left by an earlier connection in the same tick.
        if len(self.commands_this_tick) >= s.rules.new_commands_per_station_per_tick:
            self.record_decision('tick_command_budget_exhausted')
            return
        if len(s.request_results.items) >= s.rules.max_request_records_per_station:
            self.record_decision('request_record_limit_reached')
            return
        for action in actions:
            if action.key in self.attempted:
                continue
            self.attempted.add(action.key)
            request_id = 'auto-' + uuid.uuid4().hex
            msg = action.message(s, request_id)
            if len(msg.SerializeToString()) > min(bazaar.MAX_COMMAND_BYTES, s.rules.max_command_bytes):
                self.record_decision('command_exceeds_payload_limit', action, request_id)
                continue
            self.record_decision(action.reason, action, request_id)
            print(f'Auto: {action.kind} {action.args}: {action.reason}', flush=True)
            self.pending = request_id
            self.result_received = False
            self.commands_this_tick.add(request_id)
            await self.client.send(msg)
            return
        self.record_decision('game_not_running' if s.phase != pb.PHASE_RUNNING else
                             'station_failed' if s.self.failed_once or not s.self.health else
                             'no_new_eligible_action')


async def run_automated(client, policy=Policy(), advisory=False):
    session = AutomatedSession(client, policy, advisory)
    while True:
        await session.receive(await client.recv(timeout=None))
