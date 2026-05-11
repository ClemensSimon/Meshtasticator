#!/usr/bin/env python3
"""Test advanced V6 concepts:
1. Multi-path parallel routing (SF diversity)
2. Container aggregation with compression

These are theoretical calculations based on Meshtasticator's
radio model, showing the potential improvement.
"""

from lib.config import Config
from lib.phy import airtime

conf = Config()

print("=" * 70)
print("CONCEPT 1: Multi-Path Parallel Routing with SF Diversity")
print("=" * 70)

print("\nLoRa SF orthogonality: different SFs on same frequency don't collide.")
print("V6 can split data across multiple paths, each using a different SF.\n")

payload = 40  # bytes
bw = 250000
cr = 5

# Single path (current)
single_at = airtime(conf, 11, cr, payload, bw)
print(f"Single path (SF11): {single_at:.0f}ms for {payload} bytes = {payload*8/single_at*1000:.0f} bps")

# Multi-path: split payload across SFs
# Path A: SF7 (nearby, strong link) - 13 bytes
# Path B: SF9 (medium distance) - 13 bytes
# Path C: SF11 (far, weak link) - 14 bytes
splits = [(7, 13), (9, 13), (11, 14)]
max_at = 0
total_bytes = 0
print(f"\nMulti-path (3 parallel SFs):")
for sf, plen in splits:
    at = airtime(conf, sf, cr, plen, bw)
    max_at = max(max_at, at)
    total_bytes += plen
    print(f"  Path SF{sf}: {plen} bytes in {at:.0f}ms")
print(f"  Total: {total_bytes} bytes in {max_at:.0f}ms (limited by slowest path)")
print(f"  Effective: {total_bytes*8/max_at*1000:.0f} bps")
print(f"  Speedup: {single_at/max_at:.1f}x faster than single path")

# Even better: 2 paths
splits2 = [(7, 20), (11, 20)]
max_at2 = max(airtime(conf, sf, cr, pl, bw) for sf, pl in splits2)
print(f"\nDual-path (SF7 + SF11):")
for sf, plen in splits2:
    at = airtime(conf, sf, cr, plen, bw)
    print(f"  Path SF{sf}: {plen} bytes in {at:.0f}ms")
print(f"  Total: 40 bytes in {max_at2:.0f}ms")
print(f"  Speedup: {single_at/max_at2:.1f}x")

print("\n" + "=" * 70)
print("CONCEPT 2: Container Aggregation with Compression")
print("=" * 70)

print("\nPosition packets dominate Meshtastic traffic (~98%).")
print("Instead of each node sending individually, a cluster head aggregates.\n")

# Position packet sizes
pos_full = 32  # bytes per node (lat/lon/alt/time/hdop/sats)
pos_delta = 3   # bytes per node (delta from cluster center: 12-bit lat + 12-bit lon)
header_overhead = 16  # Meshtastic header + protobuf framing

nodes_in_cluster = [5, 10, 20, 30]
print(f"{'Nodes':>5} | {'Individual':>12} | {'Aggregated':>12} | {'Delta-Compressed':>16} | {'TX Saved':>9}")
print("-" * 70)
for n in nodes_in_cluster:
    individual_bytes = n * (pos_full + header_overhead)
    individual_tx = n
    individual_at = n * airtime(conf, 11, cr, pos_full + header_overhead, bw)

    # Aggregated: 1 packet with all positions
    agg_bytes = header_overhead + n * pos_full
    agg_tx = 1 + (agg_bytes // 237)  # max LoRa payload ~237 bytes, split if needed
    agg_at = agg_tx * airtime(conf, 11, cr, min(agg_bytes, 237 + header_overhead), bw)

    # Delta-compressed: cluster center (8 bytes) + deltas (3 bytes each)
    delta_bytes = header_overhead + 8 + n * pos_delta
    delta_tx = 1
    delta_at = airtime(conf, 11, cr, delta_bytes, bw)

    saved = (1 - delta_tx / individual_tx) * 100
    print(f"{n:>5} | {individual_tx:>3} TX {individual_at/1000:>5.1f}s | {agg_tx:>3} TX {agg_at/1000:>5.1f}s | {delta_tx:>3} TX {delta_at/1000:>5.1f}s | {saved:>7.0f}%")

print(f"\nKey insight: Delta compression of 30 positions fits in ONE LoRa packet (106 bytes).")
print(f"That's 30 TX -> 1 TX = 97% reduction for position traffic alone.")

print("\n" + "=" * 70)
print("CONCEPT 3: Combined — Multi-Path + Aggregation")
print("=" * 70)

# 30-node cluster: aggregate positions with delta compression
delta_30 = header_overhead + 8 + 30 * pos_delta  # 106 bytes
# Split across 2 parallel paths
split_a = delta_30 // 2  # 53 bytes via SF7 (nearby strong link)
split_b = delta_30 - split_a  # 53 bytes via SF11 (far weak link)
at_a = airtime(conf, 7, cr, split_a, bw)
at_b = airtime(conf, 11, cr, split_b, bw)
combined_at = max(at_a, at_b)
individual_at = 30 * airtime(conf, 11, cr, pos_full + header_overhead, bw)

print(f"\n30 nodes, position update:")
print(f"  Current (individual flooding): 30 TX, {individual_at/1000:.1f}s total airtime")
print(f"  V6 aggregated + delta:          1 TX, {airtime(conf, 11, cr, delta_30, bw)/1000:.1f}s airtime")
print(f"  V6 agg + delta + dual-path:     1 TX window, {combined_at/1000:.3f}s airtime")
print(f"  Improvement: {individual_at/combined_at:.0f}x less airtime")
