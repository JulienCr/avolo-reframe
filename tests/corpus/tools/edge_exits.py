"""Mesure : une derniere boite touchant un bord du cadre predit-elle qu'un
sujet perdu est sorti (plutot que rate par le detecteur) ? Suivi greedy
delibrement independant de core/policy.py (dont les deux slots se
rematchent a n'importe quelle boite) ; lit tests/corpus/traces/*.jsonl.
"""

import json
import statistics
import sys

GATE_FRACS = [0.10, 0.15, 0.25]
PRIMARY_GATE_FRAC = 0.15
MAX_GAP_MS = 10_000.0
EDGE_FRACS = [0.01, 0.03, 0.05]
PRIMARY_EDGE_FRAC = 0.03
VELOCITY_WINDOW_MS = 500.0
VELOCITY_THRESHOLDS = [50.0, 150.0]
RETURN_BUCKETS_MS = [350, 700, 1000, 2000, 3000, 6000]


def load_trace(path):
    with open(path) as f:
        header = json.loads(f.readline())
        frames = [json.loads(line) for line in f]
    return header, frames


def center(box):
    return (box["x"] + box["w"] / 2.0, box["y"] + box["h"] / 2.0)


def dist(a, b):
    ax, ay = center(a)
    bx, by = center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def touches_side_edge(box, width, frac):
    e = frac * width
    return box["x"] < e or (box["x"] + box["w"]) > width - e


def touches_top_bottom_edge(box, height, frac):
    e = frac * height
    return box["y"] < e or (box["y"] + box["h"]) > height - e


class Track:
    def __init__(self, track_id, box, pts_ms):
        self.id = track_id
        self.box = box
        self.last_pts = pts_ms
        self.cx_history = [(pts_ms, center(box)[0])]
        self.gap_start_pts = None
        self.gap_last_box = None

    def note_cx(self, pts_ms, box):
        self.cx_history.append((pts_ms, center(box)[0]))
        cutoff = pts_ms - VELOCITY_WINDOW_MS
        self.cx_history = [(t, x) for t, x in self.cx_history if t >= cutoff]

    def velocity_toward_nearest_edge(self, width):
        if len(self.cx_history) < 2:
            return 0.0
        t0, x0 = self.cx_history[0]
        t1, x1 = self.cx_history[-1]
        if t1 == t0:
            return 0.0
        raw_vx = (x1 - x0) / ((t1 - t0) / 1000.0)
        on_right_side = x1 >= width / 2.0
        return raw_vx if on_right_side else -raw_vx


def run_association(frames, width, gate_frac):
    """Greedy nearest-neighbour association across frames; returns loss events."""
    gate = gate_frac * width
    tracks = {}
    next_id = 0
    loss_events = []

    for frame in frames:
        pts_ms = frame["pts_ms"]
        boxes = frame["boxes"]

        # Expire tracks stuck in a gap past MAX_GAP_MS: finalize as never returned.
        for tid in list(tracks.keys()):
            tr = tracks[tid]
            if tr.gap_start_pts is not None and pts_ms - tr.gap_start_pts > MAX_GAP_MS:
                loss_events.append(_finalize_event(tr, width, None))
                del tracks[tid]

        candidates = []
        for tid, tr in tracks.items():
            for bi, box in enumerate(boxes):
                d = dist(tr.box if tr.gap_start_pts is None else tr.gap_last_box, box)
                if d <= gate:
                    candidates.append((d, tid, bi))
        candidates.sort(key=lambda c: c[0])

        matched_tracks = set()
        matched_boxes = set()
        for d, tid, bi in candidates:
            if tid in matched_tracks or bi in matched_boxes:
                continue
            matched_tracks.add(tid)
            matched_boxes.add(bi)
            tr = tracks[tid]
            if tr.gap_start_pts is not None:
                gap_ms = pts_ms - tr.gap_start_pts
                loss_events.append(_finalize_event(tr, width, gap_ms))
                tr.gap_start_pts = None
                tr.gap_last_box = None
                tr.cx_history = []
            tr.box = boxes[bi]
            tr.note_cx(pts_ms, boxes[bi])
            tr.last_pts = pts_ms

        for tid, tr in tracks.items():
            if tid in matched_tracks:
                continue
            if tr.gap_start_pts is None:
                tr.gap_start_pts = tr.last_pts
                tr.gap_last_box = tr.box
                tr._loss_velocity = tr.velocity_toward_nearest_edge(width)

        for bi, box in enumerate(boxes):
            if bi in matched_boxes:
                continue
            tracks[next_id] = Track(next_id, box, pts_ms)
            next_id += 1

    for tid, tr in tracks.items():
        if tr.gap_start_pts is not None:
            loss_events.append(_finalize_event(tr, width, None))

    return loss_events


def _finalize_event(track, width, gap_ms):
    box = track.gap_last_box
    return {
        "box": box,
        "velocity": getattr(track, "_loss_velocity", 0.0),
        "score": box["score"],
        "height": box["h"],
        "gap_ms": gap_ms,
    }


def share_returned_within(events, ms):
    returned = [e for e in events if e["gap_ms"] is not None and e["gap_ms"] <= ms]
    return len(returned), (100.0 * len(returned) / len(events)) if events else 0.0


def median_p90(values):
    if not values:
        return None, None
    return statistics.median(values), statistics.quantiles(values, n=100)[89] if len(values) > 1 else values[0]


def print_category_table(title, categories, width):
    print(f"\n=== {title} ===")
    for name, events in categories:
        n = len(events)
        never = sum(1 for e in events if e["gap_ms"] is None)
        print(f"\n-- {name} : {n} pertes --")
        if n == 0:
            continue
        for ms in RETURN_BUCKETS_MS:
            count, pct = share_returned_within(events, ms)
            print(f"  retour <= {ms:>5} ms : {count:4d} ({pct:5.1f}%)")
        print(f"  jamais revenu (<= {int(MAX_GAP_MS)} ms) : {never:4d} ({100.0 * never / n:5.1f}%)")
        returned_gaps = [e["gap_ms"] for e in events if e["gap_ms"] is not None]
        med, p90 = median_p90(returned_gaps)
        if med is not None:
            print(f"  gap médian (revenus) : {med:7.1f} ms, p90 : {p90:7.1f} ms")


def main():
    path = sys.argv[1]
    header, frames = load_trace(path)
    width = header["params"]["source_w"]
    height = header["params"]["source_h"]
    fps = header["fps"]
    print(f"trace : {path}")
    print(f"source : {width}x{height}, {fps} im/s, {len(frames)} frames")

    print("\n=== Sensibilité au gate d'association ===")
    for gate_frac in GATE_FRACS:
        events = run_association(frames, width, gate_frac)
        never = sum(1 for e in events if e["gap_ms"] is None)
        edge_events = [e for e in events if touches_side_edge(e["box"], width, PRIMARY_EDGE_FRAC)]
        tag = " (primaire)" if gate_frac == PRIMARY_GATE_FRAC else ""
        print(
            f"  gate {gate_frac * 100:4.0f}% largeur{tag} : {len(events):4d} pertes, "
            f"{never:4d} jamais revenues, {len(edge_events):4d} en bord de côté (E={PRIMARY_EDGE_FRAC * 100:.0f}%)"
        )

    events = run_association(frames, width, PRIMARY_GATE_FRAC)
    print(f"\ngate retenu pour la suite : {PRIMARY_GATE_FRAC * 100:.0f}% largeur, {len(events)} pertes au total")

    for e_frac in EDGE_FRACS:
        mid = [e for e in events if not touches_side_edge(e["box"], width, e_frac)]
        edge = [e for e in events if touches_side_edge(e["box"], width, e_frac)]
        print_category_table(f"Bord de côté (E={e_frac * 100:.0f}% largeur)", [("mi-cadre", mid), ("bord", edge)], width)

    for e_frac in EDGE_FRACS:
        mid = [e for e in events if not touches_top_bottom_edge(e["box"], height, e_frac)]
        edge = [e for e in events if touches_top_bottom_edge(e["box"], height, e_frac)]
        print_category_table(
            f"Bord haut/bas (E={e_frac * 100:.0f}% hauteur)", [("mi-cadre", mid), ("bord", edge)], width
        )

    edge_events = [e for e in events if touches_side_edge(e["box"], width, PRIMARY_EDGE_FRAC)]
    print(f"\n=== Bord de côté (E={PRIMARY_EDGE_FRAC * 100:.0f}%) x vitesse vers le bord ===")
    for x in VELOCITY_THRESHOLDS:
        slow = [e for e in edge_events if e["velocity"] <= x]
        fast = [e for e in edge_events if e["velocity"] > x]
        print_category_table(f"seuil {x:.0f} px/s", [(f"<= {x:.0f} px/s", slow), (f"> {x:.0f} px/s", fast)], width)

    print("\n=== Distribution globale des gaps (tous événements, revenus uniquement) ===")
    all_returned = [e["gap_ms"] for e in events if e["gap_ms"] is not None]
    if all_returned:
        q = statistics.quantiles(all_returned, n=100)
        print(f"  n = {len(all_returned)} (sur {len(events)} pertes)")
        print(f"  médiane : {statistics.median(all_returned):7.1f} ms")
        print(f"  p90    : {q[89]:7.1f} ms")
        print(f"  p95    : {q[94]:7.1f} ms")
        print(f"  p99    : {q[98]:7.1f} ms")
        print("  à comparer aux chiffres de l'issue pour les décrochages YOLO (définition différente :")
        print("  images avec < 2 boîtes entre 2+ boîtes) : médiane 333 ms, p90 3000, p95 5417, p99 9250")

    print("\n=== Avertissement ===")
    print("Sans annotation, \"jamais revenu\" mélange de vraies sorties de cadre et de longs")
    print("décrochages du détecteur ; et un retour peut être une autre personne entrant au même endroit.")


if __name__ == "__main__":
    main()
