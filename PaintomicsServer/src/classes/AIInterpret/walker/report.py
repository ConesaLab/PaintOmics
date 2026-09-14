"""A self-contained HTML page for one sealed walk: the legs drawn on the KEGG
map (or on a layout of the walked region), and beside it the card, the plan,
the chain with readings, the statements with their checks, the Results
section, the seen ledger and the notes. No external assets."""
from __future__ import annotations

import base64
import html
import json
import math
import os
import struct
import xml.etree.ElementTree as ET

import numpy as np

TEAL, AMBER, GREY, RED = "#2E7D6E", "#C8642F", "#8a8f98", "#A33B2B"
DBC = {"KEGG": TEAL, "Reactome": "#1F6FB2", "OmniPath": "#8A5FB2", "job": AMBER}


def esc(text):
    return html.escape(str(text if text is not None else ""))


# ---------------------------------------------------------------- geometry
def kgml_boxes(kgml_path):
    """node id -> [{x, y, w, h, cx, cy}] from a KGML's graphics, in the map's
    own pixel units (the same units the raster PNG is drawn in)."""
    boxes = {}
    try:
        root = ET.parse(kgml_path).getroot()
    except (ET.ParseError, OSError):
        return boxes
    for entry in root.findall("entry"):
        kind = entry.get("type")
        if kind not in ("gene", "compound"):
            continue
        g = entry.find("graphics")
        if g is None or g.get("x") is None:
            continue
        cx, cy = float(g.get("x")), float(g.get("y"))
        w, h = float(g.get("width") or 46), float(g.get("height") or 17)
        box = {"x": cx - w / 2, "y": cy - h / 2, "w": w, "h": h, "cx": cx, "cy": cy}
        for token in (entry.get("name") or "").split():
            raw = token.split(":", 1)[-1]
            node_id = ("g:" if kind == "gene" else "c:") + raw
            boxes.setdefault(node_id, []).append(box)
    return boxes


def png_size(path):
    with open(path, "rb") as handle:
        head = handle.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def _perimeter_point(box, toward):
    """Where the segment from the box centre toward ``toward`` leaves the box."""
    dx, dy = toward[0] - box["cx"], toward[1] - box["cy"]
    if dx == 0 and dy == 0:
        return box["cx"], box["cy"]
    sx = (box["w"] / 2) / abs(dx) if dx else float("inf")
    sy = (box["h"] / 2) / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return box["cx"] + dx * s, box["cy"] + dy * s


def layout_region(node_ids, edges, width=900, height=560, seed=7, iterations=300):
    """A small Fruchterman-Reingold layout in numpy; deterministic."""
    n = len(node_ids)
    if n == 0:
        return {}
    index = {v: i for i, v in enumerate(node_ids)}
    rng = np.random.default_rng(seed)
    pos = rng.uniform(-1, 1, size=(n, 2))
    k = 1.0 / math.sqrt(n)
    pairs = [(index[a], index[b]) for a, b in edges if a in index and b in index and a != b]
    t = 0.1
    for _ in range(iterations):
        delta = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(delta, axis=2) + 1e-9
        rep = (k * k / dist)[:, :, None] * delta / dist[:, :, None]
        disp = rep.sum(axis=1)
        for a, b in pairs:
            d = pos[a] - pos[b]
            length = np.linalg.norm(d) + 1e-9
            f = (length * length / k) * d / length
            disp[a] -= f
            disp[b] += f
        norms = np.linalg.norm(disp, axis=1) + 1e-9
        pos += disp / norms[:, None] * np.minimum(norms, t)[:, None]
        t = max(0.005, t * 0.97)
    lo, hi = pos.min(axis=0), pos.max(axis=0)
    span = np.maximum(hi - lo, 1e-9)
    margin = 36
    out = {}
    for v, i in index.items():
        x = margin + (pos[i, 0] - lo[0]) / span[0] * (width - 2 * margin)
        y = margin + (pos[i, 1] - lo[1]) / span[1] * (height - 2 * margin)
        out[v] = (round(float(x), 1), round(float(y), 1))
    return out


# ---------------------------------------------------------------- scenes
def _badge(x, y, text, fill, square=False):
    shape = ('<rect x="%.1f" y="%.1f" width="16" height="16" rx="2" fill="#fff" stroke="%s" stroke-width="1.4"/>'
             % (x - 8, y - 8, fill)) if square else \
            '<circle cx="%.1f" cy="%.1f" r="8" fill="%s" stroke="#fff" stroke-width="1.4"/>' % (x, y, fill)
    color = fill if square else "#fff"
    return shape + ('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="9.5" font-weight="700" '
                    'fill="%s" font-family="sans-serif">%s</text>' % (x, y + 3.4, color, esc(text)))


def map_scene(record, kgml_path, png_path):
    boxes = kgml_boxes(kgml_path)
    size = png_size(png_path) or (1356, 775)
    with open(png_path, "rb") as handle:
        raster = base64.b64encode(handle.read()).decode()
    walk = record["walk"]
    seeds = (walk.get("plan") or {}).get("seeds") or []
    nodes = record["nodes"]
    # pills for nodes without a box (miRNAs): stacked near their first neighbour on the chain
    pills, stack = {}, {}
    for leg in walk["chain"]:
        for a, b in ((leg["from"], leg["to"]), (leg["to"], leg["from"])):
            if a in boxes or a in pills or b not in boxes:
                continue
            anchor = boxes[b][0]
            n = stack.get(b, 0)
            stack[b] = n + 1
            w = 8 + 5.5 * len(nodes.get(a, {}).get("label", a))
            pills[a] = {"x": anchor["cx"] - w / 2, "y": anchor["y"] + anchor["h"] + 26 + 24 * n,
                        "w": w, "h": 17, "cx": anchor["cx"], "cy": anchor["y"] + anchor["h"] + 34.5 + 24 * n}

    def box_of(v, toward=None):
        if v in pills:
            return pills[v]
        options = boxes.get(v) or []
        if not options:
            return None
        if toward is None or len(options) == 1:
            return options[0]
        return min(options, key=lambda b: (b["cx"] - toward[0]) ** 2 + (b["cy"] - toward[1]) ** 2)

    parts = ['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" style="width:100%%;height:auto;display:block">' % size,
             '<image href="data:image/png;base64,%s" x="0" y="0" width="%d" height="%d"/>' % (raster, size[0], size[1]),
             '<defs><marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
             '<path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker></defs>' % TEAL]
    for v, p in pills.items():
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="8.5" fill="#fff" stroke="%s" stroke-width="1.4"/>'
                     '<text x="%.1f" y="%.1f" text-anchor="middle" font-size="9" font-weight="600" fill="%s" font-family="sans-serif">%s</text>'
                     % (p["x"], p["y"], p["w"], p["h"], TEAL, p["cx"], p["cy"] + 3, TEAL, esc(nodes.get(v, {}).get("label", v))))
    for i, s in enumerate(seeds, 1):
        b = box_of(s)
        if b:
            parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" fill="none" stroke="%s" stroke-width="1.8" stroke-dasharray="4 3"/>'
                         % (b["x"] - 4, b["y"] - 4, b["w"] + 8, b["h"] + 8, AMBER))
            parts.append(_badge(b["x"] - 4, b["y"] - 4, str(i), AMBER))
    for leg in walk["chain"]:
        a = box_of(leg["from"])
        b = box_of(leg["to"], toward=(a["cx"], a["cy"]) if a else None)
        if a and b:
            a = box_of(leg["from"], toward=(b["cx"], b["cy"]))
        if not (a and b):
            continue
        p0 = _perimeter_point(a, (b["cx"], b["cy"]))
        p1 = _perimeter_point(b, (a["cx"], a["cy"]))
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy) or 1.0
        bow = 0.22 * length if leg["kind"] == "step" else 0.35 * length
        cx, cy = mx - dy / length * bow, my + dx / length * bow
        d = "M%.1f,%.1f Q%.1f,%.1f %.1f,%.1f" % (p0[0], p0[1], cx, cy, p1[0], p1[1])
        if leg["kind"] == "step":
            parts.append('<path d="%s" fill="none" stroke="#fff" stroke-width="6" stroke-opacity=".85" stroke-linecap="round"/>' % d)
            parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.8" stroke-linecap="round" marker-end="url(#arr)"/>' % (d, TEAL))
        else:
            parts.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.8" stroke-dasharray="2 5" stroke-linecap="round"/>' % (d, TEAL))
        qx, qy = 0.25 * p0[0] + 0.5 * cx + 0.25 * p1[0], 0.25 * p0[1] + 0.5 * cy + 0.25 * p1[1]
        parts.append(_badge(qx, qy, str(leg["n"]) if leg["kind"] == "step" else "J%d" % leg["n"], TEAL,
                            square=leg["kind"] != "step"))
    parts.append("</svg>")
    return "".join(parts), size


def region_scene(record, network=None, width=900, height=560):
    walk = record["walk"]
    nodes = record["nodes"]
    chain_ids = []
    for leg in walk["chain"]:
        for v in (leg["from"], leg["to"]):
            if v not in chain_ids:
                chain_ids.append(v)
    region = list(chain_ids)
    per_stop = {}
    for row in walk.get("seen", []):
        per_stop.setdefault(row["after_legs"][0] if row["after_legs"] else 0, []).append(row)
    for rows in per_stop.values():
        rows.sort(key=lambda r: (-(r["r"] or 0), -r["heat"]))
        for row in rows[:3]:
            if row["id"] not in region:
                region.append(row["id"])
    labels = {v: nodes.get(v, {}).get("label") for v in region}
    for row in walk.get("seen", []):
        labels.setdefault(row["id"], row["label"])
        if labels.get(row["id"]) is None:
            labels[row["id"]] = row["label"]
    edges = set()
    if network is not None:
        rset = set(region)
        for (a, b), e in network.edges.items():
            if a in rset and b in rset:
                edges.add((a, b, e["tags"][0].split(":")[0] if e["tags"] else "?"))
    for leg in walk["chain"]:
        if leg["kind"] == "step":
            edges.add((leg["from"], leg["to"], leg["edge"]["db"]))
    pos = layout_region(region, [(a, b) for a, b, _ in edges], width, height)
    walked = set(chain_ids)
    seeds = (walk.get("plan") or {}).get("seeds") or []
    rvals = {v: nodes.get(v, {}).get("r") for v in region}
    for row in walk.get("seen", []):
        rvals.setdefault(row["id"], row["r"])
    parts = ['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" style="width:100%%;height:auto;display:block;background:#fbfcfc">' % (width, height),
             '<defs><marker id="arr2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
             '<path d="M0,0 L10,5 L0,10 z" fill="%s"/></marker></defs>' % TEAL]
    for a, b, db in edges:
        if a in pos and b in pos:
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-opacity=".2" stroke-width="1"/>'
                         % (pos[a][0], pos[a][1], pos[b][0], pos[b][1], DBC.get(db, GREY)))
    for leg in walk["chain"]:
        a, b = pos.get(leg["from"]), pos.get(leg["to"])
        if not (a and b):
            continue
        if leg["kind"] == "step":
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#fff" stroke-width="6" stroke-opacity=".8"/>' % (a[0], a[1], b[0], b[1]))
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="2.6" marker-end="url(#arr2)"/>' % (a[0], a[1], b[0], b[1], TEAL))
        else:
            parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1.6" stroke-dasharray="2 5"/>' % (a[0], a[1], b[0], b[1], AMBER))
        parts.append(_badge((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, str(leg["n"]) if leg["kind"] == "step" else "J%d" % leg["n"],
                            TEAL if leg["kind"] == "step" else AMBER, square=leg["kind"] != "step"))
    for v in region:
        if v not in pos:
            continue
        x, y = pos[v]
        r = rvals.get(v)
        fill = TEAL if (r == 1 and v in walked) else ("#fff" if r is not None else "#e6e8eb")
        stroke = TEAL if r == 1 else (GREY if r == 0 else "#c8ccd2")
        if v.startswith("mir:"):
            parts.append('<polygon points="%.1f,%.1f %.1f,%.1f %.1f,%.1f %.1f,%.1f" fill="%s" stroke="%s" stroke-width="1.4"/>'
                         % (x, y - 6, x + 6, y, x, y + 6, x - 6, y, fill, stroke))
        elif v.startswith("c:"):
            parts.append('<rect x="%.1f" y="%.1f" width="9" height="9" fill="%s" stroke="%s" stroke-width="1.4"/>' % (x - 4.5, y - 4.5, fill, stroke))
        else:
            parts.append('<circle cx="%.1f" cy="%.1f" r="5" fill="%s" stroke="%s" stroke-width="1.4"/>' % (x, y, fill, stroke))
        if v in seeds:
            parts.append('<circle cx="%.1f" cy="%.1f" r="10" fill="none" stroke="%s" stroke-width="1.6" stroke-dasharray="3 2"/>' % (x, y, AMBER))
            parts.append(_badge(x - 9, y - 9, str(seeds.index(v) + 1), AMBER))
        if v in walked or v in seeds:
            parts.append('<text x="%.1f" y="%.1f" font-size="9.5" font-family="sans-serif" fill="#17231F">%s</text>'
                         % (x + 8, y + 3.5, esc(labels.get(v) or v)))
    parts.append("</svg>")
    return "".join(parts), (width, height)


# ---------------------------------------------------------------- page
CSS = """
body{margin:0;background:#F7F9F8;color:#17231F;font-family:Georgia,'Times New Roman',serif;font-size:14.5px;line-height:1.5;padding:22px 20px 60px}
h1,h2,h3{font-family:'Helvetica Neue',Arial,sans-serif;margin:0 0 6px}h1{font-size:26px}h2{font-size:17px;margin-top:22px}h3{font-size:14px;margin-top:14px}
.wrap{max-width:1180px;margin:0 auto}.eyebrow{font-family:Menlo,monospace;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#7C8985}
.grid{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:16px;align-items:start}@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:#fff;border:1px solid #D9E1DE;padding:12px 14px;min-width:0}pre{font-family:Menlo,monospace;font-size:11.5px;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere;background:#FBFCFC;border:1px solid #EDF1F0;padding:8px 10px;margin:6px 0}
table{border-collapse:collapse;width:100%;font-family:'Helvetica Neue',Arial,sans-serif;font-size:12.5px}th{text-align:left;font-family:Menlo,monospace;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:#7C8985;padding:5px 6px;border-bottom:1px solid #D9E1DE}td{padding:6px;border-bottom:1px solid #EDF1F0;vertical-align:top}
.leg{display:inline-block;min-width:16px;height:16px;line-height:16px;text-align:center;border-radius:50%;background:#2E7D6E;color:#fff;font-family:Menlo,monospace;font-size:9.5px;margin-right:4px}.leg.j{border-radius:2px;background:#C8642F}
.rd{color:#1F6FB2;font-size:12px}.why{color:#7C8985;font-size:12px}.ok{color:#1E5D51;font-family:Menlo,monospace;font-size:11px}.bad{color:#A33B2B;font-family:Menlo,monospace;font-size:11px}
.paper{border:1px solid #D9E1DE;background:#fff;padding:16px 20px;margin:8px 0}.paper .t{font-family:'Helvetica Neue',Arial,sans-serif;font-weight:700;font-size:16px}.paper .s{color:#4B5A55;font-style:italic;margin-bottom:8px}.paper p{text-align:justify;margin:0 0 8px}
.chip{display:inline-block;font-family:Menlo,monospace;font-size:10.5px;background:#E6F1EE;color:#1E5D51;padding:0 5px;border-radius:2px;margin:0 2px}.meta{font-family:'Helvetica Neue',Arial,sans-serif;font-size:12.5px;color:#4B5A55}
"""


def _chips(text):
    out = esc(text)
    import re
    out = re.sub(r"\[e(\d+)\]", r'<span class="leg">\1</span>', out)
    out = re.sub(r"\[([^\]]+ · [^\]]+)\]", r'<span class="chip">\1</span>', out)
    return out


def render(record, network=None, kgml_path=None, png_path=None):
    walk = record["walk"]
    card = record.get("design_card") or {}
    if kgml_path and png_path and os.path.isfile(kgml_path) and os.path.isfile(png_path):
        scene, _ = map_scene(record, kgml_path, png_path)
        scene_note = "Legs drawn on the KEGG raster at the KGML coordinates; miRNAs have no box and are pills."
    else:
        scene, _ = region_scene(record, network)
        scene_note = "The walked region laid out by a spring layout; edge colour is the database the edge came from."
    counts = "%d steps, %d jumps, %d notes, %d scans, %d refusals · %d seen, not walked" % (
        sum(1 for l in walk["chain"] if l["kind"] == "step"),
        sum(1 for l in walk["chain"] if l["kind"] == "jump"), len(walk["notes"]), walk["scans"],
        walk["refusals"], len(walk["seen"]))
    p = []
    p.append("<!doctype html><meta charset='utf-8'><title>Walk %s · %s</title><style>%s</style><div class='wrap'>" % (
        esc(record["job"]), esc(record["scope"]), CSS))
    p.append("<div class='eyebrow'>Agentic Graph Walk · job %s · %s · sealed %s · model %s</div>" % (
        esc(record["job"]), esc(record["scope"]), esc(record.get("sealed_at")), esc(record.get("model_used"))))
    p.append("<h1>%s</h1><div class='meta'>%s · graph %s nodes, %s edges · N=%s measured, K=%s relevant · %s</div>" % (
        esc((record.get("results") or {}).get("title") or "Walk of %s" % record["scope"]), esc(counts),
        record["graph"]["nodes"], record["graph"]["edges"], record["graph"]["N"], record["graph"]["K"],
        " · ".join("%s %.1fs" % (k, v) for k, v in (record.get("timings") or {}).items())))
    p.append("<div class='grid'><div><div class='card'>%s<div class='why'>%s</div></div>" % (scene, esc(scene_note)))
    # results
    results = record.get("results")
    if results:
        p.append("<div class='paper'><div class='t'>%s</div><div class='s'>%s</div>" % (esc(results.get("title")), esc(results.get("summary"))))
        for para in results.get("paragraphs") or []:
            tag = "S%s" % para.get("from_statement") if para.get("from_statement") else "link"
            p.append("<p><span class='chip'>%s</span> %s</p>" % (tag, _chips(para.get("text"))))
        checks = (record.get("checks") or {}).get("results")
        if checks is not None:
            p.append("<div class='%s'>narrative verify: %s</div>" % ("ok" if not checks else "bad", esc("pass" if not checks else "; ".join(checks))))
        p.append("</div>")
    # statements
    p.append("<h2>Statements</h2>")
    for s in record.get("statements") or []:
        sense = s.get("sense") or {}
        p.append("<div class='card' style='margin-bottom:8px'><b>%d. %s</b><div>%s</div><div class='why'>cites %s · legs %s · grounded in %s · beyond %s · papers %s</div>" % (
            s.get("n", 0), esc(s.get("claim")), _chips(s.get("prose")),
            esc("; ".join("%s · %s" % tuple(c) for c in s.get("cites") or [])), esc(s.get("legs")),
            esc(s.get("grounded_in")), esc(s.get("beyond")), esc(s.get("papers"))))
        p.append("<div class='ok'>verifier: %s</div>" % esc(s.get("verifier", "pass")))
        if sense:
            p.append("<div class='%s'>sense check: %s</div>" % (
                "ok" if all(v.get("ok") for v in sense.values()) else "bad",
                esc(" · ".join("%s %s%s" % (k, "✓" if v.get("ok") else "✗", (" " + v.get("note", "")) if not v.get("ok") else "") for k, v in sense.items()))))
        if s.get("rewritten"):
            p.append("<div class='why'>rewritten once after the sense check</div>")
        p.append("</div>")
    for d in record.get("dropped") or []:
        p.append("<div class='card' style='margin-bottom:8px;border-left:3px solid %s'><b>dropped · %s</b> <span class='bad'>%s: %s</span></div>" % (
            RED, esc(d.get("claim")), esc(d.get("by")), esc(d.get("why"))))
    p.append("</div><div>")   # right column
    p.append("<div class='card'><h3>Design card <span class='why'>· %s</span></h3><pre>%s</pre></div>" % (
        esc(card.get("source")), esc("\n".join("%-13s %s" % (k, card.get(k, "")) for k in ("perturbation", "value", "axis", "baseline", "relevant", "columns")))))
    plan = walk.get("plan") or {}
    p.append("<div class='card' style='margin-top:10px'><h3>Plan</h3><div>seeds: %s</div><div>steps %s · jumps %s · notes %s</div><div class='why'>%s</div></div>" % (
        esc(", ".join(record["nodes"].get(s, {}).get("label", s) for s in plan.get("seeds", []))), esc(plan.get("steps")),
        len(plan.get("seeds", [])), max(1, int(plan.get("steps") or 2) // 2), esc(plan.get("reason"))))
    p.append("<div class='card' style='margin-top:10px'><h3>Chain</h3><table><tr><th>leg</th><th>move</th><th>reading · reason</th></tr>")
    for leg in walk["chain"]:
        e = leg.get("edge") or {}
        move = "%s → %s<div class='why'>%s</div>" % (
            esc(record["nodes"].get(leg["from"], {}).get("label", leg["from"])), esc(record["nodes"].get(leg["to"], {}).get("label", leg["to"])),
            esc("%s · %s · %s %s · %s" % (e.get("db"), e.get("name"), e.get("subtype") or "edge",
                                          "+" if e.get("sign", 0) > 0 else ("−" if e.get("sign", 0) < 0 else "?"), e.get("dir"))) if e else "jump")
        p.append("<tr><td><span class='leg %s'>%s</span></td><td>%s</td><td><div class='rd'>reads: %s</div><div class='why'>%s</div></td></tr>" % (
            "" if leg["kind"] == "step" else "j", leg["n"] if leg["kind"] == "step" else "J%d" % leg["n"], move, esc(leg["reading"]), esc(leg["reason"])))
    p.append("</table><div class='why'>stop: %s · %s</div></div>" % (esc(walk.get("stop_reason")), esc(walk.get("stop_reading"))))
    scans = [t for t in walk["turns"] if t["tool"] == "scan"]
    if scans:
        p.append("<div class='card' style='margin-top:10px'><h3>Scans</h3>")
        for t in scans:
            p.append("<pre>scan(%s)\n%s</pre>" % (esc(json.dumps(t["args"])), esc(t["answer"][:1800])))
        p.append("</div>")
    if walk["notes"]:
        p.append("<div class='card' style='margin-top:10px'><h3>Notes</h3>%s</div>" % "".join(
            "<div>after e%d · %s</div>" % (n["after_leg"], esc(n["text"])) for n in walk["notes"]))
    seen = sorted(walk["seen"], key=lambda r: -r["heat"])[:20]
    p.append("<div class='card' style='margin-top:10px'><h3>Seen, not walked (hottest 20 of %d)</h3><table><tr><th>node</th><th>r</th><th>heat</th><th>after</th></tr>%s</table></div>" % (
        len(walk["seen"]), "".join("<tr><td>%s</td><td>%s</td><td>%.2f</td><td>%s</td></tr>" % (
            esc(r["label"]), "—" if r["r"] is None else r["r"], r["heat"], esc(",".join("e%d" % x for x in r["after_legs"][:4]))) for r in seen)))
    p.append("</div></div></div>")
    return "".join(p)


def render_curves(evaluation, title="Planted-module recall"):
    """An SVG of recall and seed hit against q, one line per m, plus the table."""
    summary = evaluation.get("summary") or []
    ms = sorted({c["m"] for c in summary})
    qs = sorted({c["q"] for c in summary})
    W, H, M = 520, 300, 46
    colors = [TEAL, AMBER, "#1F6FB2", "#8A5FB2"]

    def x(q):
        return M + (q - qs[0]) / max(qs[-1] - qs[0], 1e-9) * (W - 2 * M) if len(qs) > 1 else W / 2

    def y(v):
        return H - M - v * (H - 2 * M)
    parts = ['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" style="max-width:560px;width:100%%;height:auto;background:#fff;border:1px solid #D9E1DE">' % (W, H)]
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#EDF1F0"/><text x="%d" y="%.1f" font-size="10" fill="#7C8985" text-anchor="end" font-family="sans-serif">%.2f</text>' % (M, y(v), W - M, y(v), M - 6, y(v) + 3, v))
    for q in qs:
        parts.append('<text x="%.1f" y="%d" font-size="10" fill="#7C8985" text-anchor="middle" font-family="sans-serif">q=%.2f</text>' % (x(q), H - M + 16, q))
    parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-dasharray="4 3"/>' % (M, y(0.7), W - M, y(0.7), RED))
    for i, m in enumerate(ms):
        cells = [c for c in summary if c["m"] == m]
        for key, dash in (("recall", ""), ("seed_hit", "5 3")):
            pts = " ".join("%.1f,%.1f" % (x(c["q"]), y(c[key])) for c in sorted(cells, key=lambda c: c["q"]))
            parts.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2" stroke-dasharray="%s"/>' % (pts, colors[i % 4], dash))
        parts.append('<text x="%d" y="%d" font-size="11" fill="%s" font-family="sans-serif">m=%d: recall (solid), seed hit (dashed)</text>' % (M + 4, 16 + 13 * i, colors[i % 4], m))
    parts.append("</svg>")
    rows = "".join("<tr><td>%d</td><td>%.2f</td><td>%d</td><td>%.2f</td><td>%.2f</td><td>%.2f</td></tr>" % (
        c["m"], c["q"], c["plants"], c["seed_hit"], c["recall"], c["precision"]) for c in summary)
    verdict = evaluation.get("pass")
    vtext = "no verdict cell (m=12, q=0.33) in this grid" if not verdict else (
        "PASS" if verdict["recall"] and verdict["seed_hit"] else "FAIL") + " at m=12, q=0.33: recall %.2f (≥0.70), seed hit %.2f (≥0.90)" % (
        verdict["cell"]["recall"], verdict["cell"]["seed_hit"])
    return ("<!doctype html><meta charset='utf-8'><title>%s</title><style>%s</style><div class='wrap'><div class='eyebrow'>Test 1 · %s</div>"
            "<h1>%s</h1><div class='meta'>%d plants per cell · seed %s · params %s</div>%s"
            "<table style='max-width:560px'><tr><th>m</th><th>q</th><th>plants</th><th>seed hit</th><th>recall</th><th>precision</th></tr>%s</table>"
            "<div class='%s' style='margin-top:8px'>%s</div></div>" % (
                esc(title), CSS, esc(title), esc(title), evaluation.get("plants", 0), evaluation.get("seed"),
                esc(json.dumps(evaluation.get("params"))), "".join(parts), rows,
                "ok" if verdict and verdict["recall"] and verdict["seed_hit"] else "bad", esc(vtext)))
