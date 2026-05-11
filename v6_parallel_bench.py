#!/usr/bin/env python3
"""Parallel System V6 vs Managed Flood benchmark.

Launches each simulation as a subprocess for true parallelism on Windows.
Logs progress to stderr, results to JSON files in results/.
Generates comparison plots after all runs complete.
"""

import subprocess, json, time, os, sys
_CNW = 0x08000000 if sys.platform == "win32" else 0
_SI = None
if sys.platform == "win32":
    _SI = subprocess.STARTUPINFO()
    _SI.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _SI.wShowWindow = 0
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

RESULTS_DIR = 'results'
PYTHON = sys.executable  # use same python as this script


def run_all(matrix, simtime_s=3600, period_s=30):
    """Launch all simulations as parallel subprocesses. Returns when all done."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    procs = []

    for nr, hl, rt in matrix:
        tag = f'{nr}n_{hl}h_{rt}'
        outfile = os.path.join(RESULTS_DIR, f'{tag}.json')
        errfile = os.path.join(RESULTS_DIR, f'{tag}.log')

        cmd = [PYTHON, 'v6_run_one.py', str(nr), rt, str(hl), str(simtime_s), str(period_s)]
        fout = open(outfile, 'w')
        ferr = open(errfile, 'w')
        p = subprocess.Popen(cmd, stdout=fout, stderr=ferr, creationflags=_CNW, startupinfo=_SI)
        procs.append({'proc': p, 'tag': tag, 'fout': fout, 'ferr': ferr, 'outfile': outfile, 'errfile': errfile})
        print(f'  Launched: {tag} (PID {p.pid})')

    print(f'\n{len(procs)} simulations running in parallel...\n')

    # Poll until all done, report progress
    while True:
        done = sum(1 for p in procs if p['proc'].poll() is not None)
        running = len(procs) - done
        if running == 0:
            break
        # Show which are still running
        still = [p['tag'] for p in procs if p['proc'].poll() is None]
        print(f'  [{done}/{len(procs)} done] Still running: {", ".join(still[:5])}{"..." if len(still)>5 else ""}', flush=True)
        time.sleep(15)

    # Close file handles and check for errors
    results = []
    errors = []
    for p in procs:
        p['fout'].close()
        p['ferr'].close()
        rc = p['proc'].returncode
        if rc != 0:
            with open(p['errfile']) as f:
                err = f.read().strip()
            errors.append(f"  FAIL: {p['tag']} (exit {rc}): {err[-200:]}")
        else:
            try:
                with open(p['outfile']) as f:
                    data = json.load(f)
                results.append(data)
            except Exception as e:
                errors.append(f"  FAIL: {p['tag']} (bad JSON): {e}")

    if errors:
        print(f'\n--- ERRORS ({len(errors)}) ---')
        for e in errors:
            print(e)

    return results


def print_table(results):
    """Print formatted results table."""
    print(f"\n{'Nodes':>5} {'Hops':>4} {'Router':>15} {'TX':>8} {'Coll':>8} {'Reach':>7} {'Drop':>8} {'Msgs':>6} {'Time':>5}")
    print('-' * 78)
    for r in sorted(results, key=lambda x: (x['nr_nodes'], x['hop_limit'], x['router'])):
        print(f"{r['nr_nodes']:>5} {r['hop_limit']:>4} {r['router']:>15} {r['tx']:>8} {r['collisions']:>8} "
              f"{r['reach']*100:>6.1f}% {r['dropped']:>8} {r['msgs']:>6} {r['elapsed']:>4.0f}s")


def plot_hoplimit(results, node_counts, hop_limits):
    """Bar charts: TX, Reach, Collisions per hop limit."""
    for nr in node_counts:
        mf = sorted([r for r in results if r['nr_nodes']==nr and r['router']=='MANAGED_FLOOD'], key=lambda x: x['hop_limit'])
        v6 = sorted([r for r in results if r['nr_nodes']==nr and r['router']=='SYSTEM_V6'], key=lambda x: x['hop_limit'])
        if len(mf) != len(hop_limits) or len(v6) != len(hop_limits):
            print(f'  Skipping {nr}-node plot (incomplete data)')
            continue

        fig, axes = plt.subplots(1, 3, figsize=(16, 5))
        fig.suptitle(f'System V6 vs Managed Flood -- {nr} Nodes, 1h sim, 30s msg interval', fontsize=13, fontweight='bold')
        x = np.arange(len(hop_limits))
        w = 0.35

        # TX
        ax = axes[0]
        bars_mf = ax.bar(x-w/2, [r['tx'] for r in mf], w, label='Managed Flood', color='#fb923c')
        bars_v6 = ax.bar(x+w/2, [r['tx'] for r in v6], w, label='System V6', color='#22d3ee')
        ax.set_xticks(x); ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
        ax.set_ylabel('Packets Sent'); ax.set_title('Transmissions'); ax.legend(); ax.grid(True, alpha=0.3, axis='y')
        for i in range(len(hop_limits)):
            sav = (1 - v6[i]['tx']/max(mf[i]['tx'],1)) * 100
            ax.annotate(f'-{sav:.0f}%', xy=(x[i]+w/2, v6[i]['tx']), ha='center', va='bottom', fontweight='bold', color='#22d3ee')

        # Reach
        ax = axes[1]
        ax.bar(x-w/2, [r['reach']*100 for r in mf], w, label='Managed Flood', color='#fb923c')
        ax.bar(x+w/2, [r['reach']*100 for r in v6], w, label='System V6', color='#22d3ee')
        ax.set_xticks(x); ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
        ax.set_ylabel('Reach (%)'); ax.set_title('Message Delivery'); ax.legend(); ax.grid(True, alpha=0.3, axis='y')
        for i in range(len(hop_limits)):
            ax.annotate(f'{v6[i]["reach"]*100:.0f}%', xy=(x[i]+w/2, v6[i]['reach']*100), ha='center', va='bottom', fontweight='bold', color='#22d3ee', fontsize=9)
            ax.annotate(f'{mf[i]["reach"]*100:.0f}%', xy=(x[i]-w/2, mf[i]['reach']*100), ha='center', va='bottom', fontweight='bold', color='#fb923c', fontsize=9)

        # Collisions
        ax = axes[2]
        ax.bar(x-w/2, [r['collisions'] for r in mf], w, label='Managed Flood', color='#fb923c')
        ax.bar(x+w/2, [r['collisions'] for r in v6], w, label='System V6', color='#22d3ee')
        ax.set_xticks(x); ax.set_xticklabels([f'{hl} hops' for hl in hop_limits])
        ax.set_ylabel('Collisions'); ax.set_title('Packet Collisions'); ax.legend(); ax.grid(True, alpha=0.3, axis='y')

        plt.tight_layout()
        fname = f'v6_hoplimit_{nr}nodes.png'
        plt.savefig(fname, dpi=150, bbox_inches='tight'); plt.close()
        print(f'  Saved: {fname}')


def plot_learning(results, node_counts):
    """Time-series learning curves at hop_limit=3."""
    for nr in node_counts:
        mf = [r for r in results if r['nr_nodes']==nr and r['router']=='MANAGED_FLOOD' and r['hop_limit']==3]
        v6 = [r for r in results if r['nr_nodes']==nr and r['router']=='SYSTEM_V6' and r['hop_limit']==3]
        if not mf or not v6:
            continue
        mf, v6 = mf[0], v6[0]

        n_win = min(len(mf['tx_per_window']), len(v6['tx_per_window']))
        times = [i*2 for i in range(n_win)]

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        fig.suptitle(f'Learning Curve -- {nr} Nodes, hop limit 3', fontsize=13, fontweight='bold')

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
        ax.set_title('V6 Savings (Learning Effect)'); ax.grid(True, alpha=0.3)
        if len(savings) > 0:
            ax.annotate(f'{savings[-1]:.1f}%', xy=(times[-1], savings[-1]),
                        fontsize=14, fontweight='bold', color='#4ade80', xytext=(-50, 10), textcoords='offset points')

        plt.tight_layout()
        fname = f'v6_learning_{nr}nodes.png'
        plt.savefig(fname, dpi=150, bbox_inches='tight'); plt.close()
        print(f'  Saved: {fname}')


def print_key_insight(results, node_counts, hop_limits):
    """The killer argument: V6@7hops vs MF@3hops."""
    print(f"\n{'='*70}")
    print("KEY INSIGHT: V6 enables higher hop limits at lower cost")
    print(f"{'='*70}")
    for nr in node_counts:
        mf3 = [r for r in results if r['nr_nodes']==nr and r['router']=='MANAGED_FLOOD' and r['hop_limit']==hop_limits[0]]
        v6max = [r for r in results if r['nr_nodes']==nr and r['router']=='SYSTEM_V6' and r['hop_limit']==hop_limits[-1]]
        if not mf3 or not v6max:
            continue
        mf3, v6max = mf3[0], v6max[0]
        print(f"\n  {nr} nodes:")
        print(f"    MF  @ {hop_limits[0]} hops: {mf3['tx']:>6} TX, {mf3['reach']*100:>5.1f}% reach, {mf3['collisions']:>6} collisions")
        print(f"    V6  @ {hop_limits[-1]} hops: {v6max['tx']:>6} TX, {v6max['reach']*100:>5.1f}% reach, {v6max['collisions']:>6} collisions")
        if v6max['tx'] <= mf3['tx']:
            print(f"    --> V6 at {hop_limits[-1]} hops uses FEWER TX than MF at {hop_limits[0]} hops!")
            print(f"        Reach improvement: {mf3['reach']*100:.1f}% -> {v6max['reach']*100:.1f}%")
        else:
            extra = (v6max['tx']/max(mf3['tx'],1) - 1) * 100
            print(f"    --> V6 at {hop_limits[-1]} hops uses {extra:.0f}% more TX but reaches {v6max['reach']*100:.1f}% vs {mf3['reach']*100:.1f}%")


def main():
    node_counts = [20, 50, 80]
    hop_limits = [3, 5, 7]
    simtime_s = 3600
    period_s = 30

    # Build job matrix
    matrix = [(nr, hl, rt) for nr in node_counts for hl in hop_limits for rt in ['MANAGED_FLOOD', 'SYSTEM_V6']]

    print(f"MeshRoute System V6 Benchmark")
    print(f"  {len(matrix)} simulations | Nodes: {node_counts} | Hops: {hop_limits}")
    print(f"  Sim: {simtime_s}s | Period: {period_s}s | Python: {PYTHON}")
    print()

    t0 = time.time()
    results = run_all(matrix, simtime_s, period_s)
    total = time.time() - t0

    print(f"\nCompleted {len(results)}/{len(matrix)} in {total:.0f}s (wall clock)")
    sequential = sum(r['elapsed'] for r in results)
    print(f"Sequential would have taken {sequential:.0f}s ({sequential/max(total,1):.1f}x speedup)")

    print_table(results)
    print('\nGenerating plots...')
    plot_hoplimit(results, node_counts, hop_limits)
    plot_learning(results, node_counts)
    print_key_insight(results, node_counts, hop_limits)
    print('\nDone.')


if __name__ == '__main__':
    main()
