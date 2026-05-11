#!/usr/bin/env python3
"""Run a single simulation and output JSON result. Used by parallel runner."""
import sys, json, time, traceback

def log(msg):
    print(msg, file=sys.stderr, flush=True)

try:
    nr_nodes = int(sys.argv[1])
    router = sys.argv[2]
    hop_limit = int(sys.argv[3])
    simtime_s = int(sys.argv[4]) if len(sys.argv) > 4 else 3600
    period_s = int(sys.argv[5]) if len(sys.argv) > 5 else 30

    log(f"[{nr_nodes}n/{hop_limit}h/{router}] Starting (sim={simtime_s}s, period={period_s}s)...")

    from lib.config import Config
    from lib.discrete_event_sim import DiscreteEventSim
    from lib.node import default_generate_node_list

    conf = Config()
    conf.NR_NODES = nr_nodes
    conf.SELECTED_ROUTER_TYPE = Config.ROUTER_TYPE(router)
    conf.SIMTIME = simtime_s * 1000
    conf.PERIOD = period_s * 1000
    conf.hopLimit = hop_limit
    conf.update_router_dependencies()

    t0 = time.time()
    node_configs = default_generate_node_list(conf)
    sim = DiscreteEventSim(conf, node_configs)

    log(f"[{nr_nodes}n/{hop_limit}h/{router}] Sim running...")
    sim.run_simulation()
    t_sim = time.time() - t0
    log(f"[{nr_nodes}n/{hop_limit}h/{router}] Sim done in {t_sim:.1f}s, analyzing...")

    pkts = sim.mutated_state.packets
    nodes = sim.mutated_state.nodes

    # Fast counting — sum booleans directly on lists instead of O(P*N) nested loops
    t1 = time.time()
    tx = len(pkts)
    col = sum(sum(p.collidedAtN) for p in pkts)
    useful = sum(n.usefulPackets for n in nodes)
    msgs = sim.mutated_state.messageSeq.peek()
    reach = useful / max(msgs * (nr_nodes - 1), 1)
    dropped = sum(n.droppedByDelay for n in nodes)

    # Time-series: TX per 2-min window
    WINDOW = 120_000
    n_win = int(conf.SIMTIME / WINDOW) + 1
    tx_win = [0] * n_win
    for p in pkts:
        w = int(p.startTime / WINDOW)
        if w < n_win:
            tx_win[w] += 1

    t_analyze = time.time() - t1
    elapsed = time.time() - t0
    log(f"[{nr_nodes}n/{hop_limit}h/{router}] Done! TX={tx} Reach={reach*100:.1f}% ({elapsed:.1f}s total, analysis={t_analyze:.1f}s)")

    print(json.dumps({
        'nr_nodes': nr_nodes, 'router': router, 'hop_limit': hop_limit,
        'tx': tx, 'collisions': col, 'useful': useful, 'reach': reach,
        'dropped': dropped, 'msgs': msgs, 'elapsed': round(elapsed, 1),
        'tx_per_window': tx_win,
    }))

except Exception as e:
    log(f"[ERROR] {sys.argv}: {e}")
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)
