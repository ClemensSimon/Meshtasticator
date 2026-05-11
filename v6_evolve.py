#!/usr/bin/env python3
"""Genetic Algorithm optimizer for System V6 parameters.

Evolves V6 configuration to maximize a fitness function that balances
TX reduction, reach, and collision reduction. Runs simulations in
parallel and logs intermediate results continuously.

Usage:
    python v6_evolve.py [--generations 20] [--population 12] [--nodes 50]
"""

import subprocess, json, time, os, sys, random, copy, math, argparse

PYTHON = sys.executable
RESULTS_DIR = 'ga_results'
LOG_FILE = 'ga_log.txt'


# ---- V6 Parameters (the genome) ----
# Each parameter has: name, min, max, type (float/int)
GENOME_SPEC = [
    ('route_expiry_ms',       30000,  600000, 'int'),    # Route table TTL (30s - 10min)
    ('neighbor_expiry_ms',    30000,  600000, 'int'),    # Neighbor table TTL
    ('echo_timeout_ms',       2000,   15000,  'int'),    # ECHO detection timeout
    ('echo_min_score',        0.05,   0.5,    'float'),  # Min echo score to forward
    ('echo_min_observations', 3,      15,     'int'),    # Min observations for echo suppression
    ('mpr_recompute_interval',20,     200,    'int'),    # Recompute MPR every N observations
    ('relay_redundancy_threshold', 2, 5,      'int'),    # Suppress if N+ relayers heard
    ('gossip_probability',    0.0,    0.3,    'float'),  # Non-MPR forward probability (0=off)
    ('defer_slot_multiplier', 0.5,    3.0,    'float'),  # Deferred rebroadcast wait time
    ('rssi_margin_suppress',  10,     40,     'int'),    # RSSI margin for "sender covers me"
    ('power_control_margin',  5,      20,     'int'),    # TX power control safety margin (dB)
    ('density_threshold',     3,      10,     'int'),    # Active neighbors for density suppression
]


def random_genome():
    """Create a random individual."""
    g = {}
    for name, lo, hi, typ in GENOME_SPEC:
        if typ == 'int':
            g[name] = random.randint(lo, hi)
        else:
            g[name] = round(random.uniform(lo, hi), 3)
    return g


def mutate(genome, rate=0.3):
    """Mutate a genome — each parameter has `rate` chance of changing."""
    g = copy.deepcopy(genome)
    for name, lo, hi, typ in GENOME_SPEC:
        if random.random() < rate:
            if typ == 'int':
                delta = max(1, int((hi - lo) * 0.2))
                g[name] = max(lo, min(hi, g[name] + random.randint(-delta, delta)))
            else:
                delta = (hi - lo) * 0.2
                g[name] = round(max(lo, min(hi, g[name] + random.uniform(-delta, delta))), 3)
    return g


def crossover(a, b):
    """Single-point crossover."""
    child = {}
    keys = list(a.keys())
    point = random.randint(1, len(keys) - 1)
    for i, k in enumerate(keys):
        child[k] = a[k] if i < point else b[k]
    return child


def fitness(result):
    """Fitness function: maximize TX reduction while keeping reach close to MF.

    Score = TX_saved_pct * 2 + reach_ratio * 1 - collision_ratio * 0.5
    Higher is better.
    """
    if not result or result.get('v6_tx', 0) == 0:
        return -999

    mf_tx = result['mf_tx']
    v6_tx = result['v6_tx']
    mf_reach = result['mf_reach']
    v6_reach = result['v6_reach']
    mf_coll = result['mf_collisions']
    v6_coll = result['v6_collisions']

    # TX reduction (0-100, higher = better)
    tx_saved = (1 - v6_tx / max(mf_tx, 1)) * 100

    # Reach ratio (1.0 = same as MF, <1 = worse)
    reach_ratio = v6_reach / max(mf_reach, 0.001)
    # Penalize heavily if reach drops below 70% of MF
    if reach_ratio < 0.7:
        reach_penalty = (0.7 - reach_ratio) * 200
    else:
        reach_penalty = 0

    # Collision reduction bonus
    coll_saved = (1 - v6_coll / max(mf_coll, 1)) * 100

    score = tx_saved * 2.0 + min(reach_ratio, 1.0) * 50 + coll_saved * 0.5 - reach_penalty
    return round(score, 2)


def run_simulation(genome, nr_nodes, simtime_s=1800, period_s=30, hop_limit=3):
    """Run MF and V6 simulations, return comparison results."""
    # Write genome to temp file for v6_run_one.py to read
    genome_file = os.path.join(RESULTS_DIR, f'genome_{os.getpid()}_{random.randint(0,9999)}.json')
    with open(genome_file, 'w') as f:
        json.dump(genome, f)

    results = {}
    for rt in ['MANAGED_FLOOD', 'SYSTEM_V6']:
        cmd = [PYTHON, 'v6_run_one.py', str(nr_nodes), rt, str(hop_limit),
               str(simtime_s), str(period_s)]
        if rt == 'SYSTEM_V6':
            cmd.append(genome_file)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if out.returncode == 0 and out.stdout.strip():
                data = json.loads(out.stdout.strip().split('\n')[-1])
                results[rt] = data
        except Exception as e:
            log(f"  SIM ERROR ({rt}): {e}")

    try:
        os.remove(genome_file)
    except:
        pass

    if 'MANAGED_FLOOD' in results and 'SYSTEM_V6' in results:
        mf = results['MANAGED_FLOOD']
        v6 = results['SYSTEM_V6']
        return {
            'mf_tx': mf['tx'], 'v6_tx': v6['tx'],
            'mf_reach': mf['reach'], 'v6_reach': v6['reach'],
            'mf_collisions': mf['collisions'], 'v6_collisions': v6['collisions'],
            'mf_dropped': mf['dropped'], 'v6_dropped': v6['dropped'],
        }
    return None


def log(msg):
    """Log to file and stderr."""
    ts = time.strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr, flush=True)
    with open(os.path.join(RESULTS_DIR, LOG_FILE), 'a') as f:
        f.write(line + '\n')


def log_result(gen, idx, genome, result, score):
    """Log a detailed result."""
    if result:
        tx_save = (1 - result['v6_tx'] / max(result['mf_tx'], 1)) * 100
        reach_pct = result['v6_reach'] * 100
        coll_save = (1 - result['v6_collisions'] / max(result['mf_collisions'], 1)) * 100
        log(f"  Gen {gen:>2} #{idx:>2}: score={score:>7.1f} | TX:{tx_save:>+5.1f}% Reach:{reach_pct:>5.1f}% Coll:{coll_save:>+5.1f}% | "
            f"expire={genome['route_expiry_ms']//1000}s echo={genome['echo_timeout_ms']//1000}s "
            f"gossip={genome['gossip_probability']:.2f} defer={genome['defer_slot_multiplier']:.1f} "
            f"mpr_int={genome['mpr_recompute_interval']} relay_thr={genome['relay_redundancy_threshold']} "
            f"density={genome['density_threshold']}")
    else:
        log(f"  Gen {gen:>2} #{idx:>2}: FAILED")


def main():
    parser = argparse.ArgumentParser(description='GA optimizer for System V6')
    parser.add_argument('--generations', type=int, default=15)
    parser.add_argument('--population', type=int, default=10)
    parser.add_argument('--nodes', type=int, default=50)
    parser.add_argument('--simtime', type=int, default=1800)
    parser.add_argument('--hops', type=int, default=3)
    args = parser.parse_args()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    log(f"=== GA Optimizer: {args.generations} generations, pop={args.population}, {args.nodes} nodes, {args.simtime}s sim ===")

    # Initialize population
    population = [random_genome() for _ in range(args.population)]

    # Also include the current V6 defaults as one individual
    defaults = {
        'route_expiry_ms': 300000, 'neighbor_expiry_ms': 300000,
        'echo_timeout_ms': 5000, 'echo_min_score': 0.2, 'echo_min_observations': 5,
        'mpr_recompute_interval': 50, 'relay_redundancy_threshold': 2,
        'gossip_probability': 0.0, 'defer_slot_multiplier': 1.5,
        'rssi_margin_suppress': 20, 'power_control_margin': 10, 'density_threshold': 4,
    }
    population[0] = defaults

    best_ever = None
    best_score = -999
    all_results = []

    for gen in range(args.generations):
        log(f"\n--- Generation {gen+1}/{args.generations} ---")
        scored = []

        for i, genome in enumerate(population):
            result = run_simulation(genome, args.nodes, args.simtime, hop_limit=args.hops)
            score = fitness(result)
            scored.append((genome, result, score))
            log_result(gen + 1, i + 1, genome, result, score)

            if score > best_score:
                best_score = score
                best_ever = (copy.deepcopy(genome), result, score)
                log(f"  *** NEW BEST: score={score:.1f} ***")

            all_results.append({
                'generation': gen + 1, 'individual': i + 1,
                'genome': genome, 'result': result, 'score': score
            })

        # Save intermediate results
        with open(os.path.join(RESULTS_DIR, 'all_results.json'), 'w') as f:
            json.dump(all_results, f, indent=2)

        # Save current best
        if best_ever:
            with open(os.path.join(RESULTS_DIR, 'best_genome.json'), 'w') as f:
                json.dump({'genome': best_ever[0], 'result': best_ever[1], 'score': best_ever[2]}, f, indent=2)

        # Selection: top 40% survive
        scored.sort(key=lambda x: -x[2])
        survivors = scored[:max(2, len(scored) * 4 // 10)]
        log(f"  Survivors: {len(survivors)} (scores: {[s[2] for s in survivors[:5]]})")

        # Create next generation
        next_pop = [s[0] for s in survivors]  # elitism: keep survivors
        while len(next_pop) < args.population:
            if random.random() < 0.7 and len(survivors) >= 2:
                # Crossover
                a, b = random.sample(survivors, 2)
                child = crossover(a[0], b[0])
                child = mutate(child, rate=0.2)
            else:
                # Mutation of random survivor
                parent = random.choice(survivors)
                child = mutate(parent[0], rate=0.4)
            next_pop.append(child)

        population = next_pop

    # Final report
    log(f"\n{'='*70}")
    log(f"FINAL BEST (score={best_score:.1f}):")
    if best_ever:
        genome, result, score = best_ever
        log(f"  Genome: {json.dumps(genome)}")
        if result:
            tx_save = (1 - result['v6_tx'] / max(result['mf_tx'], 1)) * 100
            coll_save = (1 - result['v6_collisions'] / max(result['mf_collisions'], 1)) * 100
            log(f"  TX reduction: {tx_save:.1f}%")
            log(f"  Reach: MF={result['mf_reach']*100:.1f}% V6={result['v6_reach']*100:.1f}%")
            log(f"  Collision reduction: {coll_save:.1f}%")
    log(f"{'='*70}")


if __name__ == '__main__':
    main()
