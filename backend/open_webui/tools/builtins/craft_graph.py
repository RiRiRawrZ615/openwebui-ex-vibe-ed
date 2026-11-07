"""
@title: Craft Graph
@author: RiRi
@version: 1.1.0
@namespace: RIRI
@description:
Semantic visualization engine for structured, data-dense topics (quest timelines,
talent trees, recipes, dependency chains, phase plans). Emits a renderer-ready
blueprint that the Craft Widget frontend draws as an interactive card/tab widget.

USE THIS TOOL ONLY WHEN ALL THREE ARE TRUE (activation filter):
  1. The topic is structural, not narrative: sequences, prerequisites, branches,
     or multi-attribute phases that read better as cards than paragraphs.
  2. There are 2 or more discrete nodes/phases with 2 or more attributes each.
  3. There are directional relationships (chronological, prerequisite, branch).
Do not use it for definitions, single-item answers, or thin lists.

DELIVERY RULE: paste the returned ```craft-widget block verbatim in your reply.
The frontend renderer keys off that fence and turns it into the widget.

ENGINE RULES (separation of logic and style):
  - Emit semantic entities only. No colors, no coordinates, no CSS.
  - Group events under explicit time-domain or phase parents.
  - Classify hazard conditions by severity only; the client decides presentation.
"""

import json
import re

USE_CASE = "craft_graph"
FENCE = "craft-widget"
STRUCTURE_TYPES = ("sequential_timeline", "dependency_tree", "clustered_cards")

SEVERITY_KEYWORDS = (
    ("fail_state", ("fail state", "critical failure", "lock out", "locks out",
                    "lock you out", "breaks", "irreversible", "permanently",
                    "game breaking", "game-breaking", "cannot be undone")),
    ("warning", ("do not", "don't", "never", "required", "must", "only then",
                 "otherwise", "before", "otherwise")),
)

ANCHOR = (r"(?:\d{1,2}:\d{2}(?:\s*(?:AM|PM)?)?"
          r"(?:\s*[-–]\s*\d{1,2}:\d{2}(?:\s*(?:AM|PM)?)?)?"
          r"|Day \d+|Night \d+|Phase \d+|Step \d+|Hour \d+|Heist Phase|Reward|Segment \d+)")

PAIR_RE = re.compile(rf"^\s*({ANCHOR})\s*[:\u2013-]\s*(.*)$", re.IGNORECASE)
PLAIN_KEY_RE = re.compile(r"^\s*([^\n:]{1,24})\s*[:\u2013-]\s*(.*)$", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s*(?:#{1,4}\s*|\[)([^\]#\n]{1,60})\]?[\s:]*$")
EDGE_RE = re.compile(r"^\s*(.+?)\s*->\s*(.+?)\s*$")


def classify_constraint(text: str) -> str:
    """Severity classification only. Presentation stays in the client."""
    low = text.lower()
    for severity, words in SEVERITY_KEYWORDS:
        if any(w in low for w in words):
            return severity
    return "note"


def split_pair(line: str):
    """Time anchor wins over naive first-colon splitting."""
    m = PAIR_RE.match(line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = PLAIN_KEY_RE.match(line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", line.strip()


def normalize_attributes(attrs):
    pairs = []
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            pairs.append([str(k), str(v)])
    elif isinstance(attrs, list):
        for item in attrs:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append([str(item[0]), str(item[1])])
            elif isinstance(item, str):
                k, v = split_pair(item)
                pairs.append([k, v])
            else:
                pairs.append(["", str(item)])
    return pairs


def normalize_constraints(items):
    out = []
    for c in items or []:
        if isinstance(c, dict):
            text = str(c.get("text", ""))
            out.append({"text": text, "severity": c.get("severity") or classify_constraint(text)})
        else:
            text = str(c)
            out.append({"text": text, "severity": classify_constraint(text)})
    return out


def build_from_blocks(goal, core, context):
    """Fallback path: derive topology from the three semantic text blocks."""
    order = []
    edges = []
    for line in (core or "").splitlines():
        m = EDGE_RE.match(line)
        if m:
            a, b = m.group(1).strip(), m.group(2).strip()
            for name in (a, b):
                if name and name not in order:
                    order.append(name)
            edges.append({"from": a, "to": b, "type": "chronological"})

    nodes = []
    current = None
    for line in (context or "").splitlines():
        if not line.strip():
            continue
        head = HEADING_RE.match(line)
        if head:
            name = head.group(1).strip()
            if name not in order:
                order.append(name)
            current = name
            nodes.append({"id": name, "title": name, "attributes": [], "critical_constraints": []})
            continue
        if current is None:
            continue
        body = line.lstrip("- ")
        key, value = split_pair(body)
        target = next((n for n in nodes if n["id"] == current), None)
        if target is None:
            continue
        if key and classify_constraint(value) != "note":
            target["critical_constraints"].append({"text": value, "severity": classify_constraint(value)})
        elif key:
            target["attributes"].append([key, value])
        else:
            target["critical_constraints"].append({"text": body.strip(), "severity": classify_constraint(body)})

    if not nodes and order:
        for name in order:
            nodes.append({"id": name, "title": name, "attributes": [], "critical_constraints": []})
    return nodes, edges


class Tools:
    def __init__(self):
        pass

    def generate(self,
                 goal: str = "",
                 core: str = "",
                 context: str = "",
                 blueprint: str = "",
                 structure_type: str = "",
                 markdown_fallback: bool = True) -> str:
        """Build the widget blueprint from the three blocks or an explicit JSON blueprint.

        Args:
            goal: Purpose and intent of the visualization.
            core: Structural logic and flow. Use 'A -> B' lines for directional edges.
            context: Factual data points, stats, conditions, critical rules. Group under phase headings.
            blueprint: Optional exact JSON matching the documented schema. Preferred when available.
            structure_type: sequential_timeline | dependency_tree | clustered_cards.
            markdown_fallback: Also emit plain markdown for clients without the renderer.
        """
        graph = None
        if blueprint:
            try:
                raw = json.loads(blueprint)
                graph = raw.get("graph_topology", raw)
            except Exception:
                return "blueprint is not valid JSON. Resend using the documented schema."

        intent = {"use_case": USE_CASE, "goal": goal or (graph or {}).get("goal", "")}

        if graph:
            raw_nodes = graph.get("nodes", {})
            items = raw_nodes.items() if isinstance(raw_nodes, dict) else [(n.get("id"), n) for n in raw_nodes]
            nodes = [{"id": str(nid),
                      "title": str(node.get("title", nid)),
                      "attributes": normalize_attributes(node.get("attributes", {})),
                      "critical_constraints": normalize_constraints(node.get("critical_constraints"))}
                     for nid, node in items]
            edges = [{"from": str(e.get("from", "")), "to": str(e.get("to", "")),
                      "type": str(e.get("type", "chronological"))}
                     for e in graph.get("directed_edges", [])]
            st = structure_type or graph.get("structure_type", "")
        else:
            nodes, edges = build_from_blocks(goal, core, context)
            st = structure_type

        if st not in STRUCTURE_TYPES:
            st = "sequential_timeline" if len(nodes) > 1 else "clustered_cards"

        if not edges:
            edges = [{"from": a["id"], "to": b["id"], "type": "chronological"}
                     for a, b in zip(nodes, nodes[1:])]

        payload = {"widget_intent": intent,
                   "graph_topology": {"structure_type": st, "nodes": nodes, "directed_edges": edges}}

        density = len(nodes) >= 2 and any(len(n["attributes"]) >= 2 for n in nodes)
        out = f"```{FENCE}\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n```"
        if markdown_fallback or not density:
            out += "\n\n" + self._markdown(payload)
        return out

    def _markdown(self, payload) -> str:
        lines = [f"**{payload['widget_intent']['goal']}**", ""]
        for n in payload["graph_topology"]["nodes"]:
            lines.append(f"### {n['title']}")
            for k, v in n["attributes"]:
                lines.append(f"- **{k}**{': ' if k else ''}{v}")
            if n["critical_constraints"]:
                lines.append("")
                lines.append("> Critical Failure States")
                for c in n["critical_constraints"]:
                    lines.append(f"> - {c['text']}")
            lines.append("")
        return "\n".join(lines)
