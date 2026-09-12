import json
import statistics
import sys

path = sys.argv[1]

with open(path) as f:
    lines = f.readlines()

header = json.loads(lines[0])
entries = [json.loads(line) for line in lines[1:]]

n = len(entries)
two_plus = [e for e in entries if e["n_detections"] >= 2]
frac_two_plus = len(two_plus) / n

union_widths_all = [e["union"]["w"] for e in entries if e["union"] is not None]
union_widths_two = [e["union"]["w"] for e in two_plus]


def percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def describe(label, values):
    if not values:
        print(f"{label}: no data")
        return
    print(
        f"{label}: n={len(values)} min={min(values):.0f} "
        f"median={percentile(values, 0.5):.0f} p90={percentile(values, 0.9):.0f} "
        f"max={max(values):.0f} mean={statistics.mean(values):.0f}"
    )


between = 0
on_subject = 0
overlapping = 0
gap_widths = []

for e in two_plus:
    boxes = sorted(e["boxes"], key=lambda b: b["x"] + b["w"] / 2)
    left, right = boxes[0], boxes[-1]
    left_edge = left["x"] + left["w"]
    right_edge = right["x"]
    crop_cx = e["state"]["current"]["x"] + e["state"]["current"]["w"] / 2

    if right_edge <= left_edge:
        overlapping += 1
        continue

    gap_widths.append(right_edge - left_edge)
    left_cx = left["x"] + left["w"] / 2
    right_cx = right["x"] + right["w"] / 2

    if left_edge <= crop_cx <= right_edge:
        between += 1
    elif (left["x"] <= crop_cx <= left["x"] + left["w"]) or (right["x"] <= crop_cx <= right["x"] + right["w"]):
        on_subject += 1
    # else: crop_cx outside both subjects and outside the gap (clamped to source edge)

print(f"Total frames: {n}")
print(f"Frames with n_detections >= 2: {len(two_plus)} ({100 * frac_two_plus:.1f}%)")
print(f"  of which overlapping boxes (no real gap): {overlapping} ({100 * overlapping / len(two_plus):.1f}%)")
non_overlap = len(two_plus) - overlapping
if non_overlap:
    print(f"  of which crop centre lands IN THE GAP (between subjects): {between} ({100 * between / non_overlap:.1f}% of non-overlapping)")
    print(f"  of which crop centre lands ON a subject's own box: {on_subject} ({100 * on_subject / non_overlap:.1f}% of non-overlapping)")
    elsewhere = non_overlap - between - on_subject
    print(f"  of which crop centre lands elsewhere (clamped/off both): {elsewhere} ({100 * elsewhere / non_overlap:.1f}%)")

print()
describe("Union width (all frames with >=1 detection, px)", union_widths_all)
describe("Union width (frames with >=2 detections, px)", union_widths_two)
describe("Gap width between the two subjects (non-overlapping 2+ frames, px)", gap_widths)

print()
cx_values = [e["state"]["current"]["x"] + e["state"]["current"]["w"] / 2 for e in entries]
cy_values = [e["state"]["current"]["y"] + e["state"]["current"]["h"] / 2 for e in entries]
w_values = [e["state"]["current"]["w"] for e in entries]
distinct_current = {(round(e["state"]["current"]["x"], 3), round(e["state"]["current"]["w"], 3)) for e in entries}
print(f"Crop centre x: min={min(cx_values):.1f} max={max(cx_values):.1f} range={max(cx_values) - min(cx_values):.1f} stdev={statistics.pstdev(cx_values):.1f}")
print(f"Crop centre y: min={min(cy_values):.1f} max={max(cy_values):.1f} range={max(cy_values) - min(cy_values):.1f} stdev={statistics.pstdev(cy_values):.1f}")
print(f"Crop width  w: min={min(w_values):.1f} max={max(w_values):.1f} range={max(w_values) - min(w_values):.1f} stdev={statistics.pstdev(w_values):.1f}")
print(f"Distinct (x, w) crop values ever held: {len(distinct_current)}")
print(f"Commands actually emitted (state.current changes): {sum(1 for e in entries if e['emitted'] is not None)}")

print()
print("First 3 distinct crop values seen (in order of first appearance):")
seen = set()
shown = 0
for e in entries:
    key = (round(e["state"]["current"]["x"], 1), round(e["state"]["current"]["y"], 1),
           round(e["state"]["current"]["w"], 1), round(e["state"]["current"]["h"], 1))
    if key not in seen:
        seen.add(key)
        print(f"  t={e['pts_ms']:.0f}ms idx={e['index']}: x={key[0]} y={key[1]} w={key[2]} h={key[3]}")
        shown += 1
        if shown >= 8:
            break
