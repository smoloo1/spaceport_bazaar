"""Shared synthetic Protobuf fixtures for unit and local integration tests."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "client"))
import bazaar
import bazaar_pb2 as pb


def fill_required(msg):
    """Populate required proto2 fields for a valid synthetic state."""
    for f in msg.DESCRIPTOR.fields:
        if not f.is_required:
            continue
        if f.message_type:
            child = getattr(msg, f.name)
            child.SetInParent()
            fill_required(child)
        elif f.enum_type:
            setattr(msg, f.name, f.enum_type.values[0].number)
        else:
            setattr(msg, f.name, f.default_value)


def state_message():
    msg = pb.ServerMessage()
    fill_required(msg.state)
    s = msg.state
    s.run_id = 'test-run'
    s.phase = pb.PHASE_RUNNING
    s.self_station_id = 'P01'
    s.self.health = 100
    s.snapshot_sequence = 1
    s.tick = 10
    s.rules.max_publication_ttl_ticks = 6
    s.rules.max_offer_ttl_ticks = 6
    s.rules.max_command_bytes = 16384
    s.directory.items.add(station_id='P02', display_name='Peer')
    return msg


def snapshot(inventory=(10, 4, 10)):
    msg = state_message()
    s = msg.state
    s.self.inventory.CopyFrom(bazaar.bundle(*inventory))
    s.self.upkeep_per_tick.CopyFrom(bazaar.bundle(1, 1, 1))
    s.rules.new_commands_per_station_per_tick = 5
    s.rules.max_request_records_per_station = 100
    s.rules.max_open_outgoing_offers = 3
    return msg


def ad(s, selling=(pb.RESOURCE_FOOD,), seeking=(pb.RESOURCE_WATER,), peer='P02'):
    a = s.advertisements.items.add()
    fill_required(a)
    a.advertisement_id = 'ad-' + peer
    a.station_id = peer
    a.status = pb.PUBLICATION_STATUS_ACTIVE
    a.expires_tick = s.tick + 5
    a.selling.items.extend(selling)
    a.seeking.items.extend(seeking)
    return a


def offer(s, give=(0, 1, 0), receive=(1, 0, 0), outgoing=False, oid='offer'):
    o = s.offers.items.add()
    fill_required(o)
    o.offer_id = oid
    o.proposer_id = 'P01' if outgoing else 'P02'
    o.recipient_id = 'P02' if outgoing else 'P01'
    o.give.CopyFrom(bazaar.bundle(*give))
    o.receive.CopyFrom(bazaar.bundle(*receive))
    o.status = pb.OFFER_STATUS_OPEN
    o.expires_tick = s.tick + 2
    return o


