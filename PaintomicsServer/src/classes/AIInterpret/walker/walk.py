"""The walk: six tools, the rules code enforces, and the log that makes a run
replayable.

The model (or a scripted policy) calls ``scan``, ``plan``, ``step``, ``jump``,
``note`` and ``stop`` on a ``Walker``. Every call is logged as a turn with the
exact answer text; every neighbour shown is logged as *seen*; a refusal costs
nothing and repeats what the caller may choose from. Candidate order is
deterministic (r, heat, label), so the same inputs print the same text and a
replayed transcript reproduces the chain.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field

from src.classes.AIInterpret.walker import heat as heat_mod

logger = logging.getLogger(__name__)

PATHWAY_PARAMS = {"sep": 1, "max_seeds": 8, "candidates": 12, "ceiling": 40,
                  "names_shown": 30, "degree_cap": None, "min_steps": 3}
NETWORK_PARAMS = {"sep": 2, "max_seeds": 16, "candidates": 40, "ceiling": 120,
                  "names_shown": 30, "degree_cap": 99, "min_steps": 3}
NOTE_MAX_CHARS = 400
SCAN_RADII = (1, 2, 3)


def sign_glyph(sign):
    """+, − or ? for an edge sign; the one definition every module prints."""
    return "+" if sign > 0 else ("−" if sign < 0 else "?")


def _short(label):
    label = str(label).replace("mmu-", "")
    return label if len(label) <= 28 else label[:26] + "…"


@dataclass
class Leg:
    n: int
    kind: str                      # "step" | "jump"
    src: str
    dst: str
    reading: str
    reason: str
    edge: dict | None = None       # {db, pathway, name, subtype, sign, dir}

    def to_dict(self):
        return {"n": self.n, "kind": self.kind, "from": self.src, "to": self.dst,
                "reading": self.reading, "reason": self.reason, "edge": self.edge}


@dataclass
class Walker:
    """One walk over one graph. Construct, then call the tool methods."""
    network: object
    overlay: object
    scope: str                                   # "network" or a pathway tag
    params: dict = field(default_factory=lambda: dict(PATHWAY_PARAMS))
    ranked: list | None = None                   # rows of the graph scan
    plan: dict | None = None
    current: str | None = None
    chain: list = field(default_factory=list)
    closed: set = field(default_factory=set)     # (src, dst) walked
    visited: set = field(default_factory=set)
    seen: OrderedDict = field(default_factory=OrderedDict)   # id -> [after leg n, ...]
    notes: list = field(default_factory=list)
    turns: list = field(default_factory=list)
    budget: dict = field(default_factory=lambda: {"steps": 0, "jumps": 0, "notes": 0})
    stop_reason: str | None = None
    stop_reading: str | None = None
    done: bool = False
    scans: int = 0
    refusals: int = 0
    steps_here: int = 0                          # steps taken since the last plan or jump

    # ------------------------------------------------------------ helpers
    def label(self, node_id):
        node = self.network.nodes.get(node_id)
        return _short(node["label"]) if node else node_id

    def _log(self, tool, args, answer, refused=False):
        self.turns.append({"t": len(self.turns), "tool": tool, "args": args,
                           "answer": answer, "refused": refused})
        if refused:
            self.refusals += 1
        return answer

    def _refuse(self, tool, args, why):
        return self._log(tool, args, "REFUSED · " + why + " Nothing spent.", refused=True)

    def _resolve(self, name, pool):
        """A node id or label (case-insensitive, with or without the mmu- prefix)
        among ``pool`` ids; None when nothing matches."""
        wanted = str(name or "").strip()
        if not wanted:
            return None
        if wanted in pool:
            return wanted
        low = wanted.lower().replace("mmu-", "")
        for node_id in pool:
            node = self.network.nodes.get(node_id, {})
            label = str(node.get("label", "")).lower().replace("mmu-", "")
            if label == low or node_id.lower() == low or node_id.split(":", 1)[-1].lower() == low:
                return node_id
        # a box label like "Foxo6, Foxo4, Foxo1" may be given by one member
        for node_id in pool:
            label = str(self.network.nodes.get(node_id, {}).get("label", "")).lower()
            if low in [part.strip() for part in label.split(",")]:
                return node_id
        return None

    def neighbour_rows(self, node_id=None):
        """The neighbours of a node (hubs excluded), ordered r, heat, label,
        each with the edge's sign, direction and whether it is still open."""
        node_id = node_id or self.current
        rows = []
        for w in self.network.neighbours(node_id):
            if w in self.overlay.capped:
                continue
            edge, direction = self.network.edge_between(node_id, w)
            h = self.overlay.heat.get(w, {})
            rows.append({"id": w, "label": self.label(w),
                         "r": int(bool(self.overlay.r.get(w))) if w in self.overlay.measured else None,
                         "heat": round(h.get("heat", 0.0), 2), "sign": edge["sign"],
                         "dir": direction, "open": (node_id, w) not in self.closed,
                         "visited": w in self.visited})
        rows.sort(key=lambda r: (-(r["r"] or 0), -r["heat"], r["label"]))
        return rows

    def _edge_record(self, src, dst):
        edge, direction = self.network.edge_between(src, dst)
        tag = edge["tags"][0] if edge["tags"] else "?"
        db, _, pathway = tag.partition(":")
        return {"db": db, "pathway": pathway, "name": self.network.pathway_name(tag),
                "subtype": edge["subtype"], "sign": edge["sign"], "dir": direction,
                "tags": len(edge["tags"])}

    def _budget_line(self):
        return "budget · %d steps · %d jumps · %d notes left" % (
            self.budget["steps"], self.budget["jumps"], self.budget["notes"])

    def _show_node(self):
        """What a move answers with: the node's layers and its neighbours by
        name. Everything listed is logged as seen."""
        node_id = self.current
        h = self.overlay.heat.get(node_id, {})
        head = "%s · r=%s · heat %.2f (%d of %d neighbours relevant)" % (
            self.label(node_id),
            int(bool(self.overlay.r.get(node_id))) if node_id in self.overlay.measured else "—",
            h.get("heat", 0.0), h.get("x", 0), h.get("n", 0))
        rows = self.neighbour_rows(node_id)
        after = len(self.chain)
        for row in rows:
            self.seen.setdefault(row["id"], []).append(after)
        shown = rows[:self.params["names_shown"]]
        names = " · ".join("%s r=%s %.2f [%s%s%s]" % (
            row["label"], "—" if row["r"] is None else row["r"], row["heat"],
            sign_glyph(row["sign"]), "" if row["open"] else ", closed",
            ", walked" if row["visited"] else "") for row in shown)
        more = "" if len(rows) <= len(shown) else " · %d more" % (len(rows) - len(shown))
        # The layers of the relevant open neighbours, so a reading can quote a
        # node's values BEFORE stepping onto it (the protocol's "candidates with
        # full layer text"); the rest are known by name, r and heat only.
        detail = []
        for row in [r for r in rows if r["r"] == 1 and r["open"]][:self.params["candidates"]]:
            detail.append("  %s (r=1, heat %.2f):\n%s" % (
                row["label"], row["heat"], _indent(self.overlay.layer_text(row["id"]), "    ")))
        return "%s\nlayers:\n%s\nneighbours (%d): %s%s\n%s%s" % (
            head, _indent(self.overlay.layer_text(node_id)), len(rows), names, more,
            ("relevant neighbours' layers:\n" + "\n".join(detail) + "\n") if detail else "",
            self._budget_line())

    # ------------------------------------------------------------- tools
    def scan(self, scope="graph", radius=2):
        """The one scoring tool. scope="graph": every measured node ranked by
        heat with the seed candidates flagged. scope="here": the nodes within
        ``radius`` steps of the current node, with distance and first hop."""
        args = {"scope": scope, "radius": radius}
        if self.done:
            return self._refuse("scan", args, "The walk is over.")
        if scope == "graph":
            self.ranked = heat_mod.scan_graph(self.network, self.overlay,
                                              self.params["sep"], self.params["candidates"])
            self.scans += 1
            cands = [r for r in self.ranked if r["candidate"]]
            lines = ["%d measured nodes ranked by heat; %d seed candidates (relevant, not "
                     "within %d edge(s) of a hotter candidate); ceiling %d steps" % (
                         len(self.ranked), len(cands), self.params["sep"], self.params["ceiling"])]
            for i, row in enumerate(cands, 1):
                lines.append("  candidate %d  %s · r=1 · heat %.2f (%d of %d) · degree %d" % (
                    i, row["label"], row["heat"], row["x"], row["n"], row["degree"]))
            hot = [r for r in self.ranked if not r["candidate"]][:8]
            if hot:
                lines.append("  not candidates, hottest: " + " · ".join(
                    "%s r=%d %.2f%s" % (r["label"], r["r"], r["heat"],
                                        " (adjacent to %s)" % r["skipped"] if r["skipped"]
                                        else (" (hub)" if r["hub"] else "")) for r in hot))
            return self._log("scan", args, "\n".join(lines))
        if scope != "here":
            return self._refuse("scan", args, 'scope must be "graph" or "here".')
        if self.plan is None:
            return self._refuse("scan", args, 'scan(scope="here") needs a plan first.')
        try:
            radius = int(radius)
        except (TypeError, ValueError):
            radius = 0
        if radius not in SCAN_RADII:
            return self._refuse("scan", args, "radius must be 1, 2 or 3.")
        rows = heat_mod.scan_here(self.network, self.overlay, self.current, radius, self.visited)
        self.scans += 1
        lines = ["%d measured nodes within %d step(s) of %s, hottest first:" % (
            len(rows), radius, self.label(self.current))]
        for row in rows[:12]:
            lines.append("  %s · r=%d · heat %.2f (%d of %d) · %d step%s%s%s" % (
                row["label"], row["r"], row["heat"], row["x"], row["n"], row["distance"],
                "s" if row["distance"] > 1 else "",
                " via %s" % _short(row["via"]) if row["distance"] > 1 else "",
                " · walked" if row["walked"] else ""))
        return self._log("scan", args, "\n".join(lines))

    def plan_walk(self, seeds, steps, reason=""):
        """Fix the seeds, their order and the step budget; one jump per seed
        and one note per two steps follow; place the walker on the first seed."""
        args = {"seeds": list(seeds or []), "steps": steps, "reason": reason}
        if self.done:
            return self._refuse("plan", args, "The walk is over.")
        if self.plan is not None:
            return self._refuse("plan", args, "The plan is fixed; plan is called once.")
        if self.ranked is None:
            self.scan("graph")
        candidates = {r["id"]: r for r in self.ranked if r["candidate"]}
        chosen = []
        for name in seeds or []:
            node_id = self._resolve(name, candidates)
            if node_id is None:
                return self._refuse("plan", args, "%r is not a seed candidate; candidates are %s."
                                    % (name, ", ".join(candidates[c]["label"] for c in candidates)))
            if node_id not in chosen:
                chosen.append(node_id)
        if not chosen:
            return self._refuse("plan", args, "Choose at least one seed.")
        if len(chosen) > self.params["max_seeds"]:
            return self._refuse("plan", args, "At most %d seeds here." % self.params["max_seeds"])
        try:
            steps = int(steps)
        except (TypeError, ValueError):
            return self._refuse("plan", args, "steps must be a number.")
        if steps < self.params["min_steps"] or steps > self.params["ceiling"]:
            return self._refuse("plan", args, "steps must be between %d and the ceiling %d."
                                % (self.params["min_steps"], self.params["ceiling"]))
        self.plan = {"seeds": chosen, "steps": steps, "reason": reason}
        self.budget = {"steps": steps, "jumps": len(chosen), "notes": max(1, steps // 2)}
        self.current = chosen[0]
        self.visited.add(self.current)
        answer = "plan set · %d steps · %d jumps · %d notes · standing on seed 1:\n%s" % (
            steps, len(chosen), self.budget["notes"], self._show_node())
        return self._log("plan", args, answer)

    def _move_guard(self, tool, args):
        if self.done:
            return self._refuse(tool, args, "The walk is over.")
        if self.plan is None:
            return self._refuse(tool, args, "Plan first: scan the graph and call plan.")
        return None

    def _reading_ok(self, node_id, reading):
        """The reading must name a layer the node has (an omic name or a member)."""
        text = str(reading or "").lower()
        if not text.strip():
            return False
        for layer in self.overlay.layers.get(node_id, []):
            if layer["omic"].lower() in text or layer["member"].lower() in text \
                    or layer["omic"].split("-")[0].lower() in text:
                return True
        node = self.network.nodes.get(node_id, {})
        return any(part.strip().lower() in text for part in str(node.get("label", "")).split(","))

    def step(self, to, reading, reason=""):
        """Move one edge to a neighbour, with or against the arrow; close that
        edge in that direction; append a leg with the reading and the reason."""
        args = {"to": to, "reading": reading, "reason": reason}
        guard = self._move_guard("step", args)
        if guard:
            return guard
        if self.budget["steps"] <= 0:
            return self._refuse("step", args, "Steps are spent; jump, note or stop.")
        rows = self.neighbour_rows()
        target = self._resolve(to, [r["id"] for r in rows])
        if target is None:
            return self._refuse("step", args, "%r is not a neighbour of %s; neighbours are %s." % (
                to, self.label(self.current), ", ".join(r["label"] for r in rows[:20])))
        if (self.current, target) in self.closed:
            return self._refuse("step", args, "The edge %s → %s was already walked that way." % (
                self.label(self.current), self.label(target)))
        if not self._reading_ok(target, reading):
            return self._refuse("step", args, "The reading must name a layer or member of %s: %s"
                                % (self.label(target), ", ".join(sorted({
                                    l["omic"] for l in self.overlay.layers.get(target, [])})) or "unmeasured"))
        self.closed.add((self.current, target))
        self.budget["steps"] -= 1
        self.steps_here += 1
        edge = self._edge_record(self.current, target)
        leg = Leg(len(self.chain) + 1, "step", self.current, target, str(reading), str(reason), edge)
        self.chain.append(leg)
        self.current = target
        self.visited.add(target)
        head = "e%d · %s → %s · %s · %s · %s %s · %s the arrow" % (
            leg.n, self.label(leg.src), self.label(leg.dst), edge["db"], edge["name"],
            edge["subtype"] or "edge", sign_glyph(edge["sign"]), edge["dir"])
        return self._log("step", args, head + "\n" + self._show_node())

    def jump(self, to, reading, reason=""):
        """Teleport to an unvisited seed or a node on the chain; a leg without an edge."""
        args = {"to": to, "reading": reading, "reason": reason}
        guard = self._move_guard("jump", args)
        if guard:
            return guard
        if self.budget["jumps"] <= 0:
            return self._refuse("jump", args, "Jumps are spent; step, note or stop.")
        pool = [s for s in self.plan["seeds"] if s not in self.visited] + \
               [n for n in dict.fromkeys([l.src for l in self.chain] + [l.dst for l in self.chain])]
        target = self._resolve(to, pool)
        if target is None:
            return self._refuse("jump", args, "%r is neither an unvisited seed nor on the chain." % to)
        if target == self.current:
            return self._refuse("jump", args, "Already standing on %s." % self.label(target))
        if self.steps_here == 0 and any(r["r"] == 1 and r["open"] and not r["visited"]
                                        for r in self.neighbour_rows()):
            return self._refuse("jump", args, "Step first: %s still has a relevant unvisited neighbour. "
                                "A jump is for an exhausted neighbourhood." % self.label(self.current))
        self.budget["jumps"] -= 1
        self.steps_here = 0
        leg = Leg(len(self.chain) + 1, "jump", self.current, target, str(reading), str(reason), None)
        self.chain.append(leg)
        self.current = target
        self.visited.add(target)
        return self._log("jump", args, "J%d · at %s\n%s" % (leg.n, self.label(target), self._show_node()))

    def note(self, text):
        """An observation pinned to the last leg; reaches the Writer, never cited."""
        args = {"text": text}
        if self.done:
            return self._refuse("note", args, "The walk is over.")
        text = str(text or "").strip()
        if not text:
            return self._refuse("note", args, "An empty note.")
        if len(text) > NOTE_MAX_CHARS:
            return self._refuse("note", args, "Notes are at most %d characters." % NOTE_MAX_CHARS)
        if self.budget["notes"] <= 0:
            return self._refuse("note", args, "Notes are spent.")
        self.budget["notes"] -= 1
        self.notes.append({"after_leg": len(self.chain), "node": self.current, "text": text})
        return self._log("note", args, "noted after e%d · %d notes left" % (
            len(self.chain), self.budget["notes"]))

    def stop(self, reading="", reason=""):
        """Record the final reading and the stop reason; seal the walk."""
        args = {"reading": reading, "reason": reason}
        if self.done:
            return self._refuse("stop", args, "The walk is over.")
        self.done = True
        self.stop_reason = str(reason or "")
        self.stop_reading = str(reading or "")
        return self._log("stop", args, "done · %s · chain sealed" % self.counts_line())

    # ------------------------------------------------------------ record
    def counts_line(self):
        steps = sum(1 for l in self.chain if l.kind == "step")
        return "%d steps, %d jumps, %d notes, %d scans · %d nodes seen, not walked" % (
            steps, len(self.chain) - steps, len(self.notes), self.scans,
            len(self.seen_not_walked()))

    def seen_not_walked(self):
        return [v for v in self.seen if v not in self.visited]

    def record(self):
        return {
            "scope": self.scope, "params": dict(self.params),
            "ranked": self.ranked or [], "plan": self.plan,
            "chain": [l.to_dict() for l in self.chain],
            "seen": [{"id": v, "label": self.label(v),
                      "r": int(bool(self.overlay.r.get(v))) if v in self.overlay.measured else None,
                      "heat": round(self.overlay.heat.get(v, {}).get("heat", 0.0), 2),
                      "after_legs": legs} for v, legs in self.seen.items() if v not in self.visited],
            "notes": list(self.notes), "stop_reason": self.stop_reason,
            "stop_reading": self.stop_reading, "budget_left": dict(self.budget),
            "turns": list(self.turns), "refusals": self.refusals, "scans": self.scans,
            "done": self.done,
        }


def _indent(text, pad="  "):
    return "\n".join(pad + line for line in str(text).splitlines())


def params_for(scope):
    return dict(NETWORK_PARAMS if scope == "network" else PATHWAY_PARAMS)
