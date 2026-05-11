#!/usr/bin/env python3
"""System V6 vs Managed Flood -- Hop Limit comparison.

Key insight: V6 reduces TX, so you can safely increase the hop limit
without congestion collapse. More hops = more reach = more messages delivered.
"""

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from lib.config import Config
from lib.discrete_event_sim import DiscreteEventSim
from lib.node import default_generate_node_list


def run_sim(nr_nodes, router_type, hop_limit, simtime_s=3600, period_s=60):
    conf = Config()
    conf.NR_NODES = nr_nodes
    conf.SELECTED_ROUTER_TYPE = router_type
    conf.SIMTIME = simtime_s * 1000
    conf.PERIOD = period_s * 1000
    conf.hopLimit = hop_limit
    conf.update_router_dependencies()

    node_configs = default_generate_node_list(conf)
    sim = DiscreteEventSim(conf, node_configs)
    sim.run_simulation()

    packets = sim.mutated_state.packets
    nodes = sim.mutated_state.nodes

    total_tx = len(packets)
    collisions = sum(1 for p in packets for n in nodes if p.collidedAtN[n.nodeid])
    received = sum(1 for p in packets for n in nodes if p.receivedAtN[n.nodeid])
    useful = sum(n.usefulPackets for n in nodes)
    msg_count = sim.mutated_state.messageSeq.peek()
    reach = useful / max(msg_count * (nr_nodes - 1), 1)
    dropped = sum(n.droppedByDelay for n in nodes)

    return {
        'tx': total_tx, 'collisions': collisions, 'received': received,
        'useful': useful, 'reach': reach, 'dropped': dropped, 'messages': msg_count,
    }


def main():
    nr_nodes = 30
    hop_limits = [3, 5, 7]

    mf_results = []
    v6_results = []

    for hl in hop_limits:
        print(f"\n--- Hop Limit {hl} ({nr_nodes} nodes) ---")

        print(f"  Managed Flood...")
        mf = run_sim(nr_nodes, Config.ROUTER_TYPE.MANAGED_FLOOD, hl)
        mf_results.append(mf)
        print(f"    TX={mf['tx']}  Reach={mf['reach']*100:.1f}%  Collisions={mf['collisions']}  Dropped={mf['dropped']}")

        print(f"  System V6...")
        v6 = run_sim(nr_nodes, Config.ROUTER_TYPE.SYSTEM_V6, hl)
        v6_results.append(v6)
        print(f"    TX={v6['tx']}  Reach={v6['reach']*100:.1f}%  Collisions={v6['collisions']}  Dropped={v6['dropped']}")

    # Plot
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle(f'Hop Limit Impact: System V6 vs Managed Flood ({nr_nodes} Nodes, 1h sim)',
                 fontsize=13, fontweight='bold')

    x = np.arange(len(hop_limits))
    w = 0.35

    # 1. TX Count
    ax = axes[0]
    ax.bar(x - w/2, [r['tx'] for r in mf_results], w, label='Managed Flood', color='#fb923c')
    ax.bar(x + w/2, [r['tx'] for r in v6_results], w, label='System V6', color='#22d3ee')
    ax.set_xticks(x)
    ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
    ax.set_ylabel('Total Packets Sent')
    ax.set_title('Transmissions')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')
    # Add savings labels
    for i in range(len(hop_limits)):
        sav = (1 - v6_results[i]['tx'] / max(mf_results[i]['tx'], 1)) * 100
        ax.annotate(f'-{sav:.0f}%', xy=(x[i] + w/2, v6_results[i]['tx']),
                    ha='center', va='bottom', fontweight='bold', color='#22d3ee', fontsize=10)

    # 2. Reach
    ax = axes[1]
    ax.bar(x - w/2, [r['reach']*100 for r in mf_results], w, label='Managed Flood', color='#fb923c')
    ax.bar(x + w/2, [r['reach']*100 for r in v6_results], w, label='System V6', color='#22d3ee')
    ax.set_xticks(x)
    ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
    ax.set_ylabel('Node Reach (%)')
    ax.set_title('Message Delivery Reach')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')
    # Add reach values
    for i in range(len(hop_limits)):
        ax.annotate(f'{v6_results[i]["reach"]*100:.0f}%', xy=(x[i] + w/2, v6_results[i]['reach']*100),
                    ha='center', va='bottom', fontweight='bold', color='#22d3ee', fontsize=10)
        ax.annotate(f'{mf_results[i]["reach"]*100:.0f}%', xy=(x[i] - w/2, mf_results[i]['reach']*100),
                    ha='center', va='bottom', fontweight='bold', color='#fb923c', fontsize=10)

    # 3. Collisions
    ax = axes[2]
    ax.bar(x - w/2, [r['collisions'] for r in mf_results], w, label='Managed Flood', color='#fb923c')
    ax.bar(x + w/2, [r['collisions'] for r in v6_results], w, label='System V6', color='#22d3ee')
    ax.set_xticks(x)
    ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
    ax.set_ylabel('Collisions')
    ax.set_title('Packet Collisions')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    plt.savefig('v6_hoplimit_comparison.png', dpi=150, bbox_inches='tight')
    print(f"\nSaved: v6_hoplimit_comparison.png")

    # Print summary table
    print(f"\n{'='*70}")
    print(f"  SUMMARY: Hop Limit Impact ({nr_nodes} nodes, 1h simulation)")
    print(f"{'='*70}")
    print(f"  {'Hops':>4}  {'MF TX':>8}  {'V6 TX':>8}  {'Save':>6}  {'MF Reach':>9}  {'V6 Reach':>9}  {'MF Coll':>8}  {'V6 Coll':>8}")
    print(f"  {'-'*4}  {'-'*8}  {'-'*8}  {'-'*6}  {'-'*9}  {'-'*9}  {'-'*8}  {'-'*8}")
    for i, hl in enumerate(hop_limits):
        sav = (1 - v6_results[i]['tx'] / max(mf_results[i]['tx'], 1)) * 100
        print(f"  {hl:>4}  {mf_results[i]['tx']:>8}  {v6_results[i]['tx']:>8}  {sav:>+5.0f}%  {mf_results[i]['reach']*100:>8.1f}%  {v6_results[i]['reach']*100:>8.1f}%  {mf_results[i]['collisions']:>8}  {v6_results[i]['collisions']:>8}")

    # Key insight
    print(f"\n  KEY INSIGHT:")
    if len(hop_limits) >= 2:
        mf_reach_3 = mf_results[0]['reach'] * 100
        v6_reach_7 = v6_results[-1]['reach'] * 100
        v6_tx_7 = v6_results[-1]['tx']
        mf_tx_3 = mf_results[0]['tx']
        print(f"  V6 at {hop_limits[-1]} hops reaches {v6_reach_7:.0f}% with {v6_tx_7} TX")
        print(f"  MF at {hop_limits[0]} hops reaches {mf_reach_3:.0f}% with {mf_tx_3} TX")
        if v6_tx_7 <= mf_tx_3:
            print(f"  -> V6 can use {hop_limits[-1]} hops for LESS cost than MF at {hop_limits[0]} hops!")


if __name__ == '__main__':
    main()
