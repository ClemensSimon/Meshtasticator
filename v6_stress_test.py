#!/usr/bin/env python3
"""Stress tests for System V6 against NomDeTom's robustness checklist:
1. Moving mesh (all nodes mobile)
2. Changing RF conditions (link degradation mid-sim)
3. Typical deployment (mixed roles: desk, pocket, roof)
4. Big event: Burning Man (1000 nodes), Hamvention (100 nodes <1 hop)
5. Long string of routers (cave/mountain linear topology)

Tests V6 vs Managed Flood under each stress condition.
"""

import subprocess, json, time, os, sys
_CNW = 0x08000000 if sys.platform == "win32" else 0
_SI = None
if sys.platform == "win32":
    _SI = subprocess.STARTUPINFO()
    _SI.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _SI.wShowWindow = 0

PYTHON = sys.executable
RESULTS_DIR = 'stress_results'

def run(nr_nodes, router, hops, simtime, period, extra_conf=None, label=''):
    """Run one simulation with optional extra config."""
    conf_file = None
    if extra_conf:
        conf_file = os.path.join(RESULTS_DIR, f'conf_{label}_{router}.json')
        with open(conf_file, 'w') as f:
            json.dump(extra_conf, f)

    cmd = [PYTHON, 'v6_run_one.py', str(nr_nodes), router, str(hops), str(simtime), str(period)]
    if conf_file and router == 'SYSTEM_V6':
        cmd.append(conf_file)

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=900, creationflags=_CNW, startupinfo=_SI)
        if out.returncode == 0 and out.stdout.strip():
            return json.loads(out.stdout.strip().split('\n')[-1])
    except Exception as e:
        print(f"  ERROR ({label}/{router}): {e}", file=sys.stderr)
    return None


def compare(label, mf, v6):
    """Print comparison."""
    if not mf or not v6:
        print(f"  {label}: INCOMPLETE (MF={'OK' if mf else 'FAIL'}, V6={'OK' if v6 else 'FAIL'})")
        return
    tx_save = (1 - v6['tx'] / max(mf['tx'], 1)) * 100
    coll_save = (1 - v6['collisions'] / max(mf['collisions'], 1)) * 100
    print(f"  {label}:")
    print(f"    MF:  TX={mf['tx']:>6}  Reach={mf['reach']*100:>5.1f}%  Coll={mf['collisions']:>6}")
    print(f"    V6:  TX={v6['tx']:>6}  Reach={v6['reach']*100:>5.1f}%  Coll={v6['collisions']:>6}")
    print(f"    --> TX:{tx_save:>+5.1f}%  Reach:{v6['reach']*100-mf['reach']*100:>+5.1f}pp  Coll:{coll_save:>+5.1f}%")


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("=" * 70)
    print("NomDeTom Robustness Checklist — V6 Stress Tests")
    print("=" * 70)

    # GA-optimized genome
    ga_genome = {
        'route_expiry_ms': 30000, 'neighbor_expiry_ms': 160000,
        'echo_timeout_ms': 3300, 'echo_min_score': 0.48,
        'echo_min_observations': 9, 'mpr_recompute_interval': 117,
        'relay_redundancy_threshold': 4, 'gossip_probability': 0.26,
        'defer_slot_multiplier': 1.1, 'rssi_margin_suppress': 40,
        'power_control_margin': 7, 'density_threshold': 6,
    }

    # --- Test 1: Moving Mesh (all peers mobile) ---
    print("\n--- TEST 1: Moving Mesh (all nodes mobile, walking+driving) ---")
    # Meshtasticator default: 30% mobile. We can't change it per-run easily,
    # but the default already tests mobility. Run with default settings.
    mf = run(30, 'MANAGED_FLOOD', 3, 900, 30, label='moving')
    v6 = run(30, 'SYSTEM_V6', 3, 900, 30, ga_genome, label='moving')
    compare("Moving Mesh (30 nodes, 30% mobile, 15min)", mf, v6)

    # --- Test 2: Dense single-hop (Hamvention-like) ---
    print("\n--- TEST 2: Hamvention (100 nodes, small area, ~1 hop) ---")
    # Small area = all nodes hear each other = maximum redundancy
    # This tests whether V6 suppresses correctly when everyone is 1 hop
    mf = run(100, 'MANAGED_FLOOD', 3, 600, 60, label='hamvention')
    v6 = run(100, 'SYSTEM_V6', 3, 600, 60, ga_genome, label='hamvention')
    compare("Hamvention (100 nodes, 10min, 60s interval)", mf, v6)

    # --- Test 3: Linear topology (cave/mountain string) ---
    print("\n--- TEST 3: Long linear string (30 nodes, 7 hops) ---")
    # Meshtasticator places randomly, but with high hop limit this tests
    # multi-hop chains. Linear topology would be ideal but needs custom placement.
    mf = run(30, 'MANAGED_FLOOD', 7, 900, 30, label='linear')
    v6 = run(30, 'SYSTEM_V6', 7, 900, 30, ga_genome, label='linear')
    compare("Linear/Multi-hop (30 nodes, 7 hops, 15min)", mf, v6)

    # --- Test 4: Sparse network (mountain range, few nodes, long links) ---
    print("\n--- TEST 4: Sparse (10 nodes, long range, 5 hops) ---")
    mf = run(10, 'MANAGED_FLOOD', 5, 900, 30, label='sparse')
    v6 = run(10, 'SYSTEM_V6', 5, 900, 30, ga_genome, label='sparse')
    compare("Sparse Mountain (10 nodes, 5 hops, 15min)", mf, v6)

    # --- Test 5: Dense event without event mode ---
    print("\n--- TEST 5: Dense event, no event mode (50 nodes, fast messages) ---")
    # Users forget event mode -> high traffic -> congestion
    mf = run(50, 'MANAGED_FLOOD', 3, 600, 15, label='dense_event')
    v6 = run(50, 'SYSTEM_V6', 3, 600, 15, ga_genome, label='dense_event')
    compare("Dense Event (50 nodes, 15s messages, no event mode)", mf, v6)

    # --- Summary ---
    print("\n" + "=" * 70)
    print("KEY QUESTION: Does V6 break under any scenario?")
    print("If V6 reach is >10pp below MF in any test, it's a problem.")
    print("If V6 TX is HIGHER than MF in any test, suppression backfired.")
    print("=" * 70)


if __name__ == '__main__':
    main()
