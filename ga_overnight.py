#!/usr/bin/env python3
"""GA marathon: reach-focused, high mutation, robust error handling."""
import subprocess, json, time, os, sys, random, copy, traceback, uuid

PYTHON = sys.executable

GENOME_SPEC = [
    ('route_expiry_ms',       30000,  600000, 'int'),
    ('neighbor_expiry_ms',    30000,  600000, 'int'),
    ('echo_timeout_ms',       2000,   15000,  'int'),
    ('echo_min_score',        0.05,   0.5,    'float'),
    ('echo_min_observations', 3,      15,     'int'),
    ('mpr_recompute_interval',20,     200,    'int'),
    ('relay_redundancy_threshold', 2, 6,      'int'),
    ('gossip_probability',    0.0,    0.5,    'float'),
    ('defer_slot_multiplier', 0.3,    3.0,    'float'),
    ('rssi_margin_suppress',  5,      40,     'int'),
    ('power_control_margin',  3,      25,     'int'),
    ('density_threshold',     3,      12,     'int'),
]

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

def mutate(g, rate=0.5):
    g = copy.deepcopy(g)
    for name, lo, hi, typ in GENOME_SPEC:
        if random.random() < rate:
            if typ == 'int':
                if random.random() < 0.3:
                    g[name] = random.randint(lo, hi)
                else:
                    delta = max(1, int((hi-lo)*0.2))
                    g[name] = max(lo, min(hi, g[name] + random.randint(-delta, delta)))
            else:
                if random.random() < 0.3:
                    g[name] = round(random.uniform(lo, hi), 3)
                else:
                    delta = (hi-lo)*0.2
                    g[name] = round(max(lo, min(hi, g[name] + random.uniform(-delta, delta))), 3)
    return g

def crossover(a, b):
    child = {}
    for k in a:
        child[k] = a[k] if random.random() < 0.5 else b[k]
    return child

def run_one(nodes, router, hops, simtime, period, genome_file=None):
    cmd = [PYTHON, 'v6_run_one.py', str(nodes), router, str(hops), str(simtime), str(period)]
    if genome_file and router == 'SYSTEM_V6':
        cmd.append(genome_file)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if out.returncode == 0 and out.stdout.strip():
            lines = out.stdout.strip().split('\n')
            for line in reversed(lines):
                line = line.strip()
                if line.startswith('{'):
                    return json.loads(line)
    except subprocess.TimeoutExpired:
        pass
    except Exception as e:
        log(f"    run_one ERROR: {e}")
    return None

def evaluate(genome):
    """Multi-scenario fitness with robust error handling."""
    # Unique genome file per evaluation
    gf = os.path.join('ga_tmp', f'genome_{uuid.uuid4().hex[:8]}.json')
    os.makedirs('ga_tmp', exist_ok=True)
    try:
        with open(gf, 'w') as f:
            json.dump(genome, f)
    except:
        return -999, []

    scores = []
    for name, nodes, hops, simtime, period in SCENARIOS:
        try:
            mf = run_one(nodes, 'MANAGED_FLOOD', hops, simtime, period)
            v6 = run_one(nodes, 'SYSTEM_V6', hops, simtime, period, gf)

            if not mf or not v6 or mf.get('tx', 0) == 0 or mf.get('reach', 0) == 0:
                scores.append((name, -100, 0, 0, 0))
                continue

            tx_save = (1 - v6['tx']/mf['tx']) * 100
            reach_ratio = v6['reach'] / mf['reach']
            coll_save = (1 - v6['collisions']/max(mf['collisions'],1)) * 100

            # REACH-HEAVY scoring
            reach_score = min(reach_ratio, 1.2) * 100
            tx_score = max(tx_save, -30) * 1.5
            coll_score = coll_save * 0.3
            penalty = max(0, (0.8 - reach_ratio) * 500) if reach_ratio < 0.8 else 0

            score = reach_score + tx_score + coll_score - penalty
            scores.append((name, round(score, 1), round(tx_save, 1), round(v6['reach']*100, 1), round(mf['reach']*100, 1)))
        except Exception as e:
            log(f"    evaluate ERROR ({name}): {e}")
            scores.append((name, -100, 0, 0, 0))

    try:
        os.remove(gf)
    except:
        pass

    if not scores:
        return -999, []
    worst = min(s[1] for s in scores)
    return worst, scores

def log(msg):
    ts = time.strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open('ga_overnight_log.txt', 'a') as f:
            f.write(line + '\n')
    except:
        pass

def main():
    POP = 12
    GENS = 30

    log(f"=== GA MARATHON: {GENS} gens, pop={POP}, {len(SCENARIOS)} scenarios, REACH-HEAVY ===")

    pop = [random_genome() for _ in range(POP)]
    # Seed known good genomes
    pop[0] = {'route_expiry_ms': 30000, 'neighbor_expiry_ms': 160000, 'echo_timeout_ms': 3300,
              'echo_min_score': 0.48, 'echo_min_observations': 9, 'mpr_recompute_interval': 117,
              'relay_redundancy_threshold': 4, 'gossip_probability': 0.26, 'defer_slot_multiplier': 1.1,
              'rssi_margin_suppress': 40, 'power_control_margin': 7, 'density_threshold': 6}
    pop[1] = {'route_expiry_ms': 60000, 'neighbor_expiry_ms': 120000, 'echo_timeout_ms': 5000,
              'echo_min_score': 0.3, 'echo_min_observations': 7, 'mpr_recompute_interval': 80,
              'relay_redundancy_threshold': 5, 'gossip_probability': 0.4, 'defer_slot_multiplier': 0.8,
              'rssi_margin_suppress': 15, 'power_control_margin': 5, 'density_threshold': 8}
    pop[2] = {'route_expiry_ms': 300000, 'neighbor_expiry_ms': 300000, 'echo_timeout_ms': 8000,
              'echo_min_score': 0.1, 'echo_min_observations': 10, 'mpr_recompute_interval': 150,
              'relay_redundancy_threshold': 5, 'gossip_probability': 0.35, 'defer_slot_multiplier': 2.0,
              'rssi_margin_suppress': 10, 'power_control_margin': 5, 'density_threshold': 10}

    best_ever = None
    best_score = -999
    all_results = []

    for gen in range(GENS):
        log(f"\n--- Gen {gen+1}/{GENS} ---")
        scored = []

        for i, genome in enumerate(pop):
            try:
                worst, details = evaluate(genome)
            except Exception as e:
                log(f"  #{i+1}: EXCEPTION: {e}")
                worst, details = -100, []

            scored.append((genome, worst, details))
            detail_str = ' | '.join(f'{n}:{s}/{tx:+.0f}%/{r:.0f}%' for n,s,tx,r,_ in details) if details else 'NO DATA'
            log(f"  #{i+1:>2}: worst={worst:>6.0f} [{detail_str}] gossip={genome.get('gossip_probability',0):.2f} relay={genome.get('relay_redundancy_threshold',0)} dens={genome.get('density_threshold',0)}")

            if worst > best_score:
                best_score = worst
                best_ever = (copy.deepcopy(genome), worst, details)
                log(f"  *** NEW BEST: {worst:.0f} ***")
                try:
                    json.dump({'genome': genome, 'score': worst, 'details': details, 'generation': gen+1},
                              open('ga_overnight_best.json', 'w'), indent=2)
                    json.dump({'genome': genome, 'score': worst, 'result': {}},
                              open('ga_results/best_genome.json', 'w'), indent=2)
                except:
                    pass

            all_results.append({'gen': gen+1, 'ind': i+1, 'worst': worst,
                                'details': details, 'genome': {k: genome[k] for k in ['gossip_probability', 'relay_redundancy_threshold', 'density_threshold', 'route_expiry_ms', 'defer_slot_multiplier']}})

        # Save all results
        try:
            json.dump(all_results, open('ga_overnight_all.json', 'w'), indent=1)
        except:
            pass

        # Selection
        scored.sort(key=lambda x: -x[1])
        n_survivors = max(3, len(scored)*4//10)
        survivors = scored[:n_survivors]
        log(f"  Survivors: {len(survivors)} best: {survivors[0][1]:.0f}")

        # Next generation
        next_pop = [s[0] for s in survivors[:2]]  # elitism
        while len(next_pop) < POP:
            r = random.random()
            if r < 0.5 and len(survivors) >= 2:
                a, b = random.sample(survivors[:max(4, len(survivors))], 2)
                child = mutate(crossover(a[0], b[0]), 0.4)
            elif r < 0.8:
                parent = random.choice(survivors)
                child = mutate(parent[0], 0.5)
            else:
                child = random_genome()
            next_pop.append(child)
        pop = next_pop

    log(f"\n{'='*70}")
    log(f"FINAL BEST (worst-case={best_score:.0f}):")
    if best_ever:
        g, s, d = best_ever
        log(f"  Genome: {json.dumps(g)}")
        for entry in d:
            if len(entry) >= 5:
                name, score, tx, reach, mf_reach = entry
                log(f"  {name}: score={score:.0f} TX={tx:+.1f}% V6reach={reach:.1f}% MFreach={mf_reach:.1f}%")
    log(f"{'='*70}")
    json.dump(all_results, open('ga_overnight_all.json', 'w'), indent=1)

if __name__ == '__main__':
    main()
