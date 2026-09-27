"""Cooperative survival decisions. No network calls or hidden peer inventory assumptions."""
from dataclasses import dataclass
from fractions import Fraction

import bazaar
import bazaar_pb2 as pb

RESOURCES = (pb.RESOURCE_WATER, pb.RESOURCE_FOOD, pb.RESOURCE_COMPONENTS)
NAMES = ('water', 'food', 'components')


@dataclass(frozen=True)
class Policy:
    reserve_ticks: int = 3
    trade_size: int = 2
    gift_size: int = 1
    ttl: int = 2

    def __post_init__(self):
        if min(self.reserve_ticks, self.trade_size, self.ttl) < 1 or self.gift_size < 0:
            raise ValueError('Reserve ticks, trade size and TTL must be positive; gift size cannot be negative.')


@dataclass(frozen=True)
class Action:
    kind: str
    args: tuple
    reason: str
    # Stable within a tick even if sizes or expiry change after another action.
    key: tuple

    def message(self, state, request_id):
        args = self.args
        if self.kind == 'offer':
            peer, give, receive, expires = args
            return bazaar.offer(state.run_id, request_id, peer,
                                bazaar.bundle(*give), bazaar.bundle(*receive), expires)
        return getattr(bazaar, self.kind)(state.run_id, request_id, *args)


def candidates(s, policy=Policy()):
    """Return prioritized actions; the executor enforces readiness and command budgets."""
    if s.phase != pb.PHASE_RUNNING or s.self.failed_once or not s.self.health:
        return []
    inventory = bazaar.as_tuple(s.self.inventory)
    upkeep = bazaar.as_tuple(s.self.upkeep_per_tick)
    reserve = tuple(v * policy.reserve_ticks for v in upkeep)
    surplus = tuple(max(0, i - r) for i, r in zip(inventory, reserve))
    deficit = tuple(max(0, r - i) for i, r in zip(inventory, reserve))
    offers = [o for o in s.offers.items if o.status == pb.OFFER_STATUS_OPEN and o.expires_tick > s.tick]
    outgoing = [o for o in offers if o.proposer_id == s.self_station_id]
    committed = tuple(sum(bazaar.as_tuple(o.give)[i] for o in outgoing) for i in range(3))
    available = tuple(max(0, v - c) for v, c in zip(surplus, committed))
    actions = []

    # Withdraw commitments that could spend into the reserve after a tick or trade.
    endangered = [i for i in range(3) if committed[i] > surplus[i]]
    if endangered:
        return [Action('withdraw', (o.offer_id,), 'Release offers that now threaten the upkeep reserve.',
                       ('withdraw', o.offer_id)) for o in outgoing
                if any(bazaar.as_tuple(o.give)[i] for i in endangered)]

    needs = sorted((i for i in range(3) if deficit[i]),
                   key=lambda i: (Fraction(inventory[i], upkeep[i]), i))
    incoming = []
    for o in offers:
        if o.recipient_id != s.self_station_id:
            continue
        pay, get = bazaar.as_tuple(o.receive), bazaar.as_tuple(o.give)
        projected = tuple(a - b + c for a, b, c in zip(inventory, pay, get))
        # Do not spend promised stock, worsen an existing shortage, or overflow uint64.
        if any(p > a for p, a in zip(pay, available)) or any(v > 2**64 - 1 for v in projected):
            continue
        if not any(get) or sum(pay) > policy.trade_size:
            continue
        improvement = tuple(min(deficit[i], max(0, get[i] - pay[i])) for i in needs)
        if not any(pay) or any(improvement):
            reason = ('Accept a free gift.' if not any(pay) else
                      f'Improve shortages; projected inventory {projected}; reserve {reserve}.')
        elif all(a >= r for a, r in zip(inventory, reserve)):
            # A small trade requested by a peer is cooperative only when funded by surplus.
            reason = f'Help a peer through a small surplus-funded exchange; projected inventory {projected}.'
        else:
            continue
        incoming.append((improvement, not any(pay), o.offer_id,
                         Action('accept', (o.offer_id,), reason, ('accept', o.offer_id))))
    incoming.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
    actions.extend(entry[3] for entry in incoming)
    publications = []

    selling = tuple(RESOURCES[i] for i in range(3) if available[i])
    seeking = tuple(RESOURCES[i] for i in needs)
    own_ads = [a for a in s.advertisements.items if a.station_id == s.self_station_id
               and a.status == pb.PUBLICATION_STATUS_ACTIVE and a.expires_tick > s.tick]
    matching = any(set(a.selling.items) == set(selling) and set(a.seeking.items) == set(seeking)
                   and a.expires_tick > s.tick + 1 for a in own_ads)
    ad_ttl = min(policy.ttl, s.rules.max_publication_ttl_ticks, 2**64 - 1 - s.tick)
    if (selling or seeking) and not matching and ad_ttl:
        publications.append(Action('advertise', (selling, seeking, s.tick + ad_ttl),
                              f'Advertise surplus and shortages; inventory {inventory}, reserve {reserve}.',
                              ('advertise',)))
    elif not selling and not seeking:
        publications.extend(Action('withdraw', (a.advertisement_id,), 'Remove an outdated surplus advertisement.',
                              ('withdraw', a.advertisement_id)) for a in own_ads)

    ttl = min(policy.ttl, s.rules.max_offer_ttl_ticks, 2**64 - 1 - s.tick)
    if not ttl or len(outgoing) >= s.rules.max_open_outgoing_offers:
        return actions + publications
    occupied_peers = {o.recipient_id for o in outgoing}
    peers = {p.station_id for p in s.directory.items}
    ads = sorted((a for a in s.advertisements.items
                  if a.station_id != s.self_station_id and a.station_id in peers
                  and a.station_id not in occupied_peers
                  and a.status == pb.PUBLICATION_STATUS_ACTIVE and a.expires_tick > s.tick),
                 key=lambda a: a.station_id)
    # Rotate peer order by tick to avoid always favoring the same planet.
    if ads:
        offset = s.tick % len(ads)
        ads = ads[offset:] + ads[:offset]
    for need in needs:
        for ad in ads:
            if RESOURCES[need] not in ad.selling.items:
                continue
            for give_index in range(3):
                if RESOURCES[give_index] not in ad.seeking.items or not available[give_index]:
                    continue
                amount = min(policy.trade_size, available[give_index], deficit[need])
                give, get = [0, 0, 0], [0, 0, 0]
                give[give_index] = get[need] = amount
                actions.append(Action('offer', (ad.station_id, tuple(give), tuple(get), s.tick + ttl),
                                      f'Seek {NAMES[need]} from {ad.station_id} using surplus {NAMES[give_index]}; '
                                      'provisional 1:1 exchange.', ('offer', ad.station_id)))
    # Gifts are optional, small, and only made when all our upkeep reserves are covered.
    if policy.gift_size and not any(deficit):
        for ad in ads:
            for i in range(3):
                if RESOURCES[i] in ad.seeking.items and available[i]:
                    give = [0, 0, 0]
                    give[i] = min(policy.gift_size, available[i])
                    actions.append(Action('offer', (ad.station_id, tuple(give), (0, 0, 0), s.tick + ttl),
                                          f'Offer {give[i]} surplus {NAMES[i]} to {ad.station_id}, which advertises a need.',
                                          ('offer', ad.station_id)))
                    break
    return actions + publications
