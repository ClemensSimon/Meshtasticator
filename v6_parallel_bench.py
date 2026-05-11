#!/usr/bin/env python3
"""Parallel System V6 vs Managed Flood benchmark.

Runs all simulation configurations in parallel using multiprocessing.
With 48 cores, a full matrix of node counts x hop limits x routers
finishes in the time of the single slowest run.
"""

import multiprocessing as mp
import time
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from lib.config import Config
from lib.discrete_event_sim import DiscreteEventSim
from lib.node import default_generate_node_list


def run_single(args):
    """Worker function — runs one simulation, returns metrics dict."""
    nr_nodes, router_type_str, hop_limit, simtime_s, period_s, run_id = args

    conf = Config()
    conf.NR_NODES = nr_nodes
    conf.SELECTED_ROUTER_TYPE = Config.ROUTER_TYPE(router_type_str)
    conf.SIMTIME = simtime_s * 1000
    conf.PERIOD = period_s * 1000
    conf.hopLimit = hop_limit
    conf.update_router_dependencies()

    t0 = time.time()
    node_configs = default_generate_node_list(conf)
    sim = DiscreteEventSim(conf, node_configs)
    sim.run_simulation()
    elapsed = time.time() - t0

    pkts = sim.mutated_state.packets
    nodes = sim.mutated_state.nodes

    tx = len(pkts)
    collisions = sum(1 for p in pkts for n in nodes if p.collidedAtN[n.nodeid])
    useful = sum(n.usefulPackets for n in nodes)
    msgs = sim.mutated_state.messageSeq.peek()
    reach = useful / max(msgs * (nr_nodes - 1), 1)
    dropped = sum(n.droppedByDelay for n in nodes)

    # Time-series: TX per 2-min window
    WINDOW = 120_000
    n_win = int(conf.SIMTIME / WINDOW) + 1
    tx_per_window = [0] * n_win
    for p in pkts:
        w = int(p.startTime / WINDOW)
        if w < n_win:
            tx_per_window[w] += 1

    return {
        'nr_nodes': nr_nodes, 'router': router_type_str, 'hop_limit': hop_limit,
        'run_id': run_id, 'tx': tx, 'collisions': collisions, 'useful': useful,
        'reach': reach, 'dropped': dropped, 'msgs': msgs, 'elapsed': elapsed,
        'tx_per_window': tx_per_window,
    }


def main():
    # ---- Configuration matrix ----
    node_counts = [20, 50, 80]
    hop_limits = [3, 5, 7]
    routers = ['MANAGED_FLOOD', 'SYSTEM_V6']
    simtime_s = 7200    # 2 hours
    period_s = 30       # message every 30s — fast learning

    # Build job list
    jobs = []
    for nr in node_counts:
        for hl in hop_limits:
            for rt in routers:
                jobs.append((nr, rt, hl, simtime_s, period_s, f'{nr}n_{hl}h_{rt}'))

    total = len(jobs)
    print(f"Running {total} simulations in parallel on {mp.cpu_count()} cores...")
    print(f"  Nodes: {node_counts}")
    print(f"  Hop limits: {hop_limits}")
    print(f"  Routers: {routers}")
    print(f"  Sim time: {simtime_s}s, Period: {period_s}s")
    print()

    t0 = time.time()
    with mp.Pool(processes=min(total, mp.cpu_count())) as pool:
        results = pool.map(run_single, jobs)
    total_elapsed = time.time() - t0

    print(f"\nAll {total} simulations done in {total_elapsed:.1f}s")
    print(f"(Sequential would have taken ~{sum(r['elapsed'] for r in results):.0f}s)")
    print()

    # ---- Print results table ----
    print(f"{'Nodes':>5} {'Hops':>4} {'Router':>15} {'TX':>8} {'Coll':>8} {'Reach':>7} {'Dropped':>8} {'Msgs':>6} {'Time':>5}")
    print('-' * 75)
    for r in sorted(results, key=lambda x: (x['nr_nodes'], x['hop_limit'], x['router'])):
        print(f"{r['nr_nodes']:>5} {r['hop_limit']:>4} {r['router']:>15} {r['tx']:>8} {r['collisions']:>8} {r['reach']*100:>6.1f}% {r['dropped']:>8} {r['msgs']:>6} {r['elapsed']:>4.0f}s")

    # ---- Plot 1: Hop limit comparison (bar charts) ----
    for nr in node_counts:
        fig, axes = plt.subplots(1, 3, figsize=(16, 5))
        fig.suptitle(f'Hop Limit Impact: System V6 vs Managed Flood -- {nr} Nodes', fontsize=13, fontweight='bold')

        mf = [r for r in results if r['nr_nodes'] == nr and r['router'] == 'MANAGED_FLOOD']
        v6 = [r for r in results if r['nr_nodes'] == nr and r['router'] == 'SYSTEM_V6']
        mf.sort(key=lambda x: x['hop_limit'])
        v6.sort(key=lambda x: x['hop_limit'])

        x = np.arange(len(hop_limits))
        w = 0.35

        # TX
        ax = axes[0]
        ax.bar(x - w/2, [r['tx'] for r in mf], w, label='Managed Flood', color='#fb923c')
        ax.bar(x + w/2, [r['tx'] for r in v6], w, label='System V6', color='#22d3ee')
        ax.set_xticks(x); ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
        ax.set_ylabel('Packets Sent'); ax.set_title('Transmissions'); ax.legend(); ax.grid(True, alpha=0.3, axis='y')
        for i in range(len(hop_limits)):
            sav = (1 - v6[i]['tx'] / max(mf[i]['tx'], 1)) * 100
            ax.annotate(f'-{sav:.0f}%', xy=(x[i]+w/2, v6[i]['tx']), ha='center', va='bottom', fontweight='bold', color='#22d3ee')

        # Reach
        ax = axes[1]
        ax.bar(x - w/2, [r['reach']*100 for r in mf], w, label='Managed Flood', color='#fb923c')
        ax.bar(x + w/2, [r['reach']*100 for r in v6], w, label='System V6', color='#22d3ee')
        ax.set_xticks(x); ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
        ax.set_ylabel('Reach (%)'); ax.set_title('Message Delivery'); ax.legend(); ax.grid(True, alpha=0.3, axis='y')
        for i in range(len(hop_limits)):
            ax.annotate(f'{v6[i]["reach"]*100:.0f}%', xy=(x[i]+w/2, v6[i]['reach']*100), ha='center', va='bottom', fontweight='bold', color='#22d3ee', fontsize=9)
            ax.annotate(f'{mf[i]["reach"]*100:.0f}%', xy=(x[i]-w/2, mf[i]['reach']*100), ha='center', va='bottom', fontweight='bold', color='#fb923c', fontsize=9)

        # Collisions
        ax = axes[2]
        ax.bar(x - w/2, [r['collisions'] for r in mf], w, label='Managed Flood', color='#fb923c')
        ax.bar(x + w/2, [r['collisions'] for r in v6], w, label='System V6', color='#22d3ee')
        ax.set_xticks(x); ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
        ax.set_ylabel('Collisions'); ax.set_title('Packet Collisions'); ax.legend(); ax.grid(True, alpha=0.3, axis='y')

        plt.tight_layout()
        fname = f'v6_hoplimit_{nr}nodes.png'
        plt.savefig(fname, dpi=150, bbox_inches='tight')
        print(f"Saved: {fname}")
        plt.close()

    # ---- Plot 2: Learning curves (time series) ----
    for nr in node_counts:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        fig.suptitle(f'Learning Curve: V6 vs Managed Flood -- {nr} Nodes, hop limit 3', fontsize=13, fontweight='bold')

        mf = [r for r in results if r['nr_nodes'] == nr and r['router'] == 'MANAGED_FLOOD' and r['hop_limit'] == 3][0]
        v6 = [r for r in results if r['nr_nodes'] == nr and r['router'] == 'SYSTEM_V6' and r['hop_limit'] == 3][0]

        n_win = min(len(mf['tx_per_window']), len(v6['tx_per_window']))
        times = [i * 2 for i in range(n_win)]  # minutes

        ax = axes[0]
        ax.plot(times, mf['tx_per_window'][:n_win], '-', color='#fb923c', label='Managed Flood', linewidth=1.5, alpha=0.7)
        ax.plot(times, v6['tx_per_window'][:n_win], '-', color='#22d3ee', label='System V6', linewidth=1.5, alpha=0.7)
        ax.set_xlabel('Time (min)'); ax.set_ylabel('TX per 2-min window'); ax.set_title('Transmissions Over Time')
        ax.legend(); ax.grid(True, alpha=0.3)

        ax = axes[1]
        cum_mf = np.cumsum(mf['tx_per_window'][:n_win])
        cum_v6 = np.cumsum(v6['tx_per_window'][:n_win])
        savings = (1 - cum_v6 / np.maximum(cum_mf, 1)) * 100
        ax.plot(times, savings, '-', color='#4ade80', linewidth=2)
        ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
        ax.set_xlabel('Time (min)'); ax.set_ylabel('Cumulative TX Savings (%)')
        ax.set_title('V6 Savings Over Time (Learning Curve)')
        ax.grid(True, alpha=0.3)
        if len(savings) > 0:
            ax.annotate(f'{savings[-1]:.1f}%', xy=(times[-1], savings[-1]),
                        fontsize=14, fontweight='bold', color='#4ade80', xytext=(-50, 10), textcoords='offset points')

        plt.tight_layout()
        fname = f'v6_learning_{nr}nodes.png'
        plt.savefig(fname, dpi=150, bbox_inches='tight')
        print(f"Saved: {fname}")
        plt.close()

    # ---- Key insight ----
    print(f"\n{'='*70}")
    print("KEY INSIGHT: V6 enables higher hop limits at lower cost")
    print(f"{'='*70}")
    for nr in node_counts:
        mf3 = [r for r in results if r['nr_nodes'] == nr and r['router'] == 'MANAGED_FLOOD' and r['hop_limit'] == 3][0]
        v67 = [r for r in results if r['nr_nodes'] == nr and r['router'] == 'SYSTEM_V6' and r['hop_limit'] == 7][0]
        print(f"  {nr} nodes: MF@3hops: {mf3['tx']} TX, {mf3['reach']*100:.0f}% reach")
        print(f"  {nr} nodes: V6@7hops: {v67['tx']} TX, {v67['reach']*100:.0f}% reach")
        if v67['tx'] <= mf3['tx']:
            print(f"  --> V6 at 7 hops uses LESS TX than MF at 3 hops, with {v67['reach']*100:.0f}% vs {mf3['reach']*100:.0f}% reach!")
        else:
            extra = (v67['tx'] / max(mf3['tx'], 1) - 1) * 100
            print(f"  --> V6 at 7 hops uses {extra:.0f}% more TX but reaches {v67['reach']*100:.0f}% vs {mf3['reach']*100:.0f}%")
        print()


if __name__ == '__main__':
    mp.freeze_support()  # needed on Windows
    main()
