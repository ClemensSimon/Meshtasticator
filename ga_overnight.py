#!/usr/bin/env python3
"""Overnight GA marathon: large population, many generations, multi-scenario fitness."""
import subprocess, json, time, os, sys, random, copy

PYTHON = sys.executable

GENOME_SPEC = [
    ('route_expiry_ms',       30000,  600000, 'int'),
    ('neighbor_expiry_ms',    30000,  600000, 'int'),
    ('echo_timeout_ms',       2000,   15000,  'int'),
    ('echo_min_score',        0.05,   0.5,    'float'),
    ('echo_min_observations', 3,      15,     'int'),
    ('mpr_recompute_interval',20,     200,    'int'),
    ('relay_redundancy_threshold', 2, 5,      'int'),
    ('gossip_probability',    0.0,    0.3,    'float'),
    ('defer_slot_multiplier', 0.5,    3.0,    'float'),
    ('rssi_margin_suppress',  10,     40,     'int'),
    ('power_control_margin',  5,      20,     'int'),
    ('density_threshold',     3,      10,     'int'),
]

# Multi-scenario fitness: test on MULTIPLE scenarios, worst-case determines fitness
SCENARIOS = [
    ('standard', 30, 3, 600, 30),
    ('dense',    40, 3, 600, 30),
    ('sparse',   10, 5, 600, 30),
]

def random_genome():
    g = {}
    for name, lo, hi, typ in GENOME_SPEC:
        g[name] = random.randint(lo, hi) if typ == 'int' else round(random.uniform(lo, hi), 3)
    return g

def mutate(g, rate=0.3):
    g = copy.deepcopy(g)
    for name, lo, hi, typ in GENOME_SPEC:
        if random.random() < rate:
            if typ == 'int':
                delta = max(1, int((hi-lo)*0.15))
                g[name] = max(lo, min(hi, g[name] + random.randint(-delta, delta)))
            else:
                delta = (hi-lo)*0.15
                g[name] = round(max(lo, min(hi, g[name] + random.uniform(-delta, delta))), 3)
    return g

def crossover(a, b):
    child = {}
    keys = list(a.keys())
    point = random.randint(1, len(keys)-1)
    for i, k in enumerate(keys):
        child[k] = a[k] if i < point else b[k]
    return child

def run_one(nodes, router, hops, simtime, period, genome_file=None):
    cmd = [PYTHON, 'v6_run_one.py', str(nodes), router, str(hops), str(simtime), str(period)]
    if genome_file and router == 'SYSTEM_V6':
        cmd.append(genome_file)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if out.returncode == 0 and out.stdout.strip():
            return json.loads(out.stdout.strip().split('\n')[-1])
    except:
        pass
    return None

def evaluate(genome):
    """Multi-scenario fitness: run all scenarios, return worst-case score."""
    gf = f'/tmp/ga_genome_{os.getpid()}_{random.randint(0,9999)}.json'
    with open(gf, 'w') as f:
        json.dump(genome, f)

    scores = []
    for name, nodes, hops, simtime, period in SCENARIOS:
        mf = run_one(nodes, 'MANAGED_FLOOD', hops, simtime, period)
        v6 = run_one(nodes, 'SYSTEM_V6', hops, simtime, period, gf)
        if mf and v6 and mf['tx'] > 0:
            tx_save = (1 - v6['tx']/mf['tx']) * 100
            reach_ratio = v6['reach'] / max(mf['reach'], 0.001)
            reach_penalty = max(0, (0.7 - reach_ratio) * 200) if reach_ratio < 0.7 else 0
            coll_save = (1 - v6['collisions']/max(mf['collisions'],1)) * 100
            score = tx_save * 2.0 + min(reach_ratio, 1.0) * 50 + coll_save * 0.5 - reach_penalty
            scores.append((name, score, tx_save, v6['reach']*100))
        else:
            scores.append((name, -999, 0, 0))

    try: os.remove(gf)
    except: pass

    # Fitness = WORST scenario score (robust optimization)
    worst = min(s[1] for s in scores)
    return worst, scores

def log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open('ga_overnight_log.txt', 'a') as f:
        f.write(line + '\n')

def main():
    POP = 10
    GENS = 30
    log(f"=== GA OVERNIGHT: {GENS} gens, pop={POP}, {len(SCENARIOS)} scenarios ===")

    # Seed with known good genomes
    pop = [random_genome() for _ in range(POP)]
    pop[0] = {'route_expiry_ms': 30000, 'neighbor_expiry_ms': 160000, 'echo_timeout_ms': 3300,
              'echo_min_score': 0.48, 'echo_min_observations': 9, 'mpr_recompute_interval': 117,
              'relay_redundancy_threshold': 4, 'gossip_probability': 0.26, 'defer_slot_multiplier': 1.1,
              'rssi_margin_suppress': 40, 'power_control_margin': 7, 'density_threshold': 6}

    best_ever = None
    best_score = -999

    for gen in range(GENS):
        log(f"\n--- Gen {gen+1}/{GENS} ---")
        scored = []
        for i, genome in enumerate(pop):
            worst, details = evaluate(genome)
            scored.append((genome, worst, details))
            detail_str = ' | '.join(f'{n}:{s:.0f}/{tx:+.0f}%/{r:.0f}%' for n,s,tx,r in details)
            log(f"  #{i+1}: worst={worst:.0f} [{detail_str}]")
            if worst > best_score:
                best_score = worst
                best_ever = (copy.deepcopy(genome), worst, details)
                log(f"  *** NEW BEST: {worst:.0f} ***")
                json.dump({'genome': genome, 'score': worst, 'details': details},
                          open('ga_overnight_best.json', 'w'), indent=2)

        scored.sort(key=lambda x: -x[1])
        survivors = scored[:max(2, len(scored)*4//10)]
        log(f"  Survivors: {len(survivors)} (scores: {[s[1] for s in survivors[:3]]})")

        next_pop = [s[0] for s in survivors]
        while len(next_pop) < POP:
            if random.random() < 0.7 and len(survivors) >= 2:
                a, b = random.sample(survivors, 2)
                child = mutate(crossover(a[0], b[0]), 0.2)
            else:
                child = mutate(random.choice(survivors)[0], 0.4)
            next_pop.append(child)
        pop = next_pop

    log(f"\n{'='*60}")
    log(f"FINAL BEST (worst-case score={best_score:.0f}):")
    if best_ever:
        g, s, d = best_ever
        log(f"  Genome: {json.dumps(g)}")
        for name, score, tx, reach in d:
            log(f"  {name}: score={score:.0f} TX={tx:+.1f}% Reach={reach:.1f}%")
    log(f"{'='*60}")

if __name__ == '__main__':
    main()
