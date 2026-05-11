#!/usr/bin/env python3
"""System V6 vs Managed Flood — Time-series benchmark with learning curve visualization.

Shows how V6 improves over time as it learns routes from overheard packets.
Generates comparison plots: TX count, reach, collisions per time window.
"""

import sys
import os
import numpy as np
import matplotlib
matplotlib.use('Agg')  # non-interactive backend
import matplotlib.pyplot as plt
from lib.config import Config
from lib.discrete_event_sim import DiscreteEventSim
from lib.node import default_generate_node_list

WINDOW_MS = 120_000  # 2-minute windows for time-series

def run_sim(nr_nodes, router_type, simtime_s=3600, period_s=60):
    """Run a single simulation, return packets with timing data."""
    conf = Config()
    conf.NR_NODES = nr_nodes
    conf.SELECTED_ROUTER_TYPE = router_type
    conf.SIMTIME = simtime_s * 1000
    conf.PERIOD = period_s * 1000  # more frequent messages = faster learning
    conf.update_router_dependencies()

    node_configs = default_generate_node_list(conf)
    sim = DiscreteEventSim(conf, node_configs)
    sim.run_simulation()

    packets = sim.mutated_state.packets
    nodes = sim.mutated_state.nodes
    return packets, nodes, conf


def analyze_time_series(packets, nodes, conf):
    """Break down metrics per time window to show learning curve."""
    n_windows = int(conf.SIMTIME / WINDOW_MS) + 1
    windows = []

    for w in range(n_windows):
        t_start = w * WINDOW_MS
        t_end = (w + 1) * WINDOW_MS

        # Packets sent in this window
        w_pkts = [p for p in packets if t_start <= p.startTime < t_end]
        tx_count = len(w_pkts)

        # Collisions in this window
        collisions = sum(1 for p in w_pkts for n in nodes
                         if p.collidedAtN[n.nodeid] is True)

        # Useful receptions (new content delivered)
        received = sum(1 for p in w_pkts for n in nodes
                       if p.receivedAtN[n.nodeid] is True)

        # Node reach: how many unique (message, node) pairs delivered
        # Approximate: count receivedAtN True entries
        useful = 0
        seen_pairs = set()
        for p in w_pkts:
            for n in nodes:
                if p.receivedAtN[n.nodeid] and (p.seq, n.nodeid) not in seen_pairs:
                    seen_pairs.add((p.seq, n.nodeid))
                    useful += 1

        # Efficiency: useful / tx_count
        efficiency = useful / max(tx_count, 1)

        windows.append({
            'time_min': t_start / 60_000,
            'tx': tx_count,
            'collisions': collisions,
            'received': received,
            'useful': useful,
            'efficiency': efficiency,
        })

    return windows


def plot_comparison(mf_windows, v6_windows, nr_nodes, output_file):
    """Plot side-by-side time-series comparison."""
    times = [w['time_min'] for w in mf_windows]

    fig, axes = plt.subplots(2, 2, figsize=(14, 8))
    fig.suptitle(f'MeshRoute System V6 vs Managed Flood — {nr_nodes} Nodes (Learning Curve)',
                 fontsize=14, fontweight='bold')

    # 1. TX Count per window
    ax = axes[0][0]
    ax.plot(times, [w['tx'] for w in mf_windows], 'o-', color='#fb923c', label='Managed Flood', linewidth=2, markersize=4)
    ax.plot(times, [w['tx'] for w in v6_windows], 's-', color='#22d3ee', label='System V6', linewidth=2, markersize=4)
    ax.set_ylabel('Packets Sent')
    ax.set_xlabel('Time (min)')
    ax.set_title('Transmissions per Window')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 2. Collisions per window
    ax = axes[0][1]
    ax.plot(times, [w['collisions'] for w in mf_windows], 'o-', color='#fb923c', label='Managed Flood', linewidth=2, markersize=4)
    ax.plot(times, [w['collisions'] for w in v6_windows], 's-', color='#22d3ee', label='System V6', linewidth=2, markersize=4)
    ax.set_ylabel('Collisions')
    ax.set_xlabel('Time (min)')
    ax.set_title('Collisions per Window')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 3. Efficiency (useful/tx)
    ax = axes[1][0]
    ax.plot(times, [w['efficiency'] for w in mf_windows], 'o-', color='#fb923c', label='Managed Flood', linewidth=2, markersize=4)
    ax.plot(times, [w['efficiency'] for w in v6_windows], 's-', color='#22d3ee', label='System V6', linewidth=2, markersize=4)
    ax.set_ylabel('Useful Deliveries / TX')
    ax.set_xlabel('Time (min)')
    ax.set_title('Efficiency (Higher = Better)')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 4. Cumulative TX savings
    ax = axes[1][1]
    cum_mf = np.cumsum([w['tx'] for w in mf_windows])
    cum_v6 = np.cumsum([w['tx'] for w in v6_windows])
    savings_pct = (1 - cum_v6 / np.maximum(cum_mf, 1)) * 100
    ax.plot(times, savings_pct, 's-', color='#4ade80', linewidth=2, markersize=4)
    ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
    ax.set_ylabel('TX Savings (%)')
    ax.set_xlabel('Time (min)')
    ax.set_title('Cumulative TX Savings (V6 vs Flood)')
    ax.grid(True, alpha=0.3)
    # Annotate final value
    if len(savings_pct) > 0:
        ax.annotate(f'{savings_pct[-1]:.1f}%', xy=(times[-1], savings_pct[-1]),
                    fontsize=12, fontweight='bold', color='#4ade80',
                    textcoords="offset points", xytext=(-40, 10))

    plt.tight_layout()
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"Saved: {output_file}")
    plt.close()


def main():
    node_counts = [10, 20, 50]
    if len(sys.argv) > 1:
        node_counts = [int(x) for x in sys.argv[1:]]

    for nr in node_counts:
        print(f"\n{'='*60}")
        print(f"  Benchmarking {nr} nodes...")
        print(f"{'='*60}")

        print(f"  Running Managed Flood ({nr} nodes)...")
        mf_pkts, mf_nodes, mf_conf = run_sim(nr, Config.ROUTER_TYPE.MANAGED_FLOOD)
        print(f"    ->{len(mf_pkts)} packets sent")

        print(f"  Running System V6 ({nr} nodes)...")
        v6_pkts, v6_nodes, v6_conf = run_sim(nr, Config.ROUTER_TYPE.SYSTEM_V6)
        print(f"    ->{len(v6_pkts)} packets sent")

        print(f"  Analyzing time series...")
        mf_windows = analyze_time_series(mf_pkts, mf_nodes, mf_conf)
        v6_windows = analyze_time_series(v6_pkts, v6_nodes, v6_conf)

        output = f"v6_benchmark_{nr}nodes.png"
        plot_comparison(mf_windows, v6_windows, nr, output)

        # Print summary
        mf_total_tx = sum(w['tx'] for w in mf_windows)
        v6_total_tx = sum(w['tx'] for w in v6_windows)
        mf_total_col = sum(w['collisions'] for w in mf_windows)
        v6_total_col = sum(w['collisions'] for w in v6_windows)
        savings = (1 - v6_total_tx / max(mf_total_tx, 1)) * 100
        col_reduction = (1 - v6_total_col / max(mf_total_col, 1)) * 100

        print(f"\n  SUMMARY ({nr} nodes):")
        print(f"    TX:         MF={mf_total_tx}  V6={v6_total_tx}  ({savings:+.1f}%)")
        print(f"    Collisions: MF={mf_total_col}  V6={v6_total_col}  ({col_reduction:+.1f}%)")

        # Show learning effect: compare first half vs second half
        half = len(v6_windows) // 2
        if half > 0:
            first_half_tx = sum(w['tx'] for w in v6_windows[:half])
            second_half_tx = sum(w['tx'] for w in v6_windows[half:])
            mf_first = sum(w['tx'] for w in mf_windows[:half])
            mf_second = sum(w['tx'] for w in mf_windows[half:])
            sav_first = (1 - first_half_tx / max(mf_first, 1)) * 100
            sav_second = (1 - second_half_tx / max(mf_second, 1)) * 100
            print(f"    Learning:   First half: {sav_first:+.1f}%  Second half: {sav_second:+.1f}%")

    print("\nDone. Check PNG files for charts.")


if __name__ == '__main__':
    main()
