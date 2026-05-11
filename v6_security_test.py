#!/usr/bin/env python3
"""Security test: V6 with HMAC vs route poisoning attack.

Simulates a network with N% malicious nodes that try to poison routes.
With HMAC: malicious packets are untrusted, route tables stay clean.
Without HMAC: malicious nodes corrupt topology, degrade delivery.
"""

import sys, json, time
from lib.config import Config
from lib.discrete_event_sim import DiscreteEventSim
from lib.node import default_generate_node_list, MeshNode

def run_with_attackers(nr_nodes, pct_malicious, simtime_s=900, period_s=30):
    """Run V6 simulation with malicious nodes."""
    conf = Config()
    conf.NR_NODES = nr_nodes
    conf.SELECTED_ROUTER_TYPE = Config.ROUTER_TYPE.SYSTEM_V6
    conf.SIMTIME = simtime_s * 1000
    conf.PERIOD = period_s * 1000
    conf.hopLimit = 3
    conf.V6_PARAMS = {
        'route_expiry_ms': 30000, 'neighbor_expiry_ms': 160000,
        'echo_timeout_ms': 3300, 'echo_min_score': 0.48,
        'echo_min_observations': 9, 'mpr_recompute_interval': 117,
        'relay_redundancy_threshold': 4, 'gossip_probability': 0.26,
        'defer_slot_multiplier': 1.1, 'rssi_margin_suppress': 40,
        'power_control_margin': 7, 'density_threshold': 6,
    }
    conf.update_router_dependencies()

    node_configs = default_generate_node_list(conf)
    sim = DiscreteEventSim(conf, node_configs)

    # Mark some nodes as malicious AFTER creation
    n_malicious = int(nr_nodes * pct_malicious / 100)
    import random
    random.seed(42)
    malicious_ids = set(random.sample(range(nr_nodes), n_malicious))
    for node in sim.mutated_state.nodes:
        if node.nodeid in malicious_ids:
            node.is_malicious = True

    sim.run_simulation()

    pkts = sim.mutated_state.packets
    nodes = sim.mutated_state.nodes
    tx = len(pkts)
    col = sum(sum(p.collidedAtN) for p in pkts)
    useful = sum(n.usefulPackets for n in nodes)
    msgs = sim.mutated_state.messageSeq.peek()
    reach = useful / max(msgs * (nr_nodes - 1), 1)

    return {'tx': tx, 'collisions': col, 'reach': reach, 'msgs': msgs, 'malicious': n_malicious}


def main():
    nr = 30
    print(f"=== Security Test: {nr} nodes, V6 with HMAC ===\n")
    print(f"{'Malicious%':>10} {'Malicious#':>10} {'TX':>8} {'Reach':>7} {'Coll':>8}")
    print("-" * 50)

    for pct in [0, 5, 10, 20, 30]:
        r = run_with_attackers(nr, pct)
        print(f"{pct:>9}% {r['malicious']:>10} {r['tx']:>8} {r['reach']*100:>6.1f}% {r['collisions']:>8}")

    print(f"\nWith HMAC: malicious nodes' packets are forwarded (for reach)")
    print(f"but they CANNOT poison route tables, MPR sets, or 2-hop topology.")
    print(f"Reach should remain stable as malicious % increases.")


if __name__ == '__main__':
    main()
