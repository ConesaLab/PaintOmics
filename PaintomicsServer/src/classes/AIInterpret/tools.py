"""
Tool definitions and executors for AI chat function-calling.

Chat tools (for follow-up Q&A):
  - get_feature_values: a gene's values in every layer under the user's labels
  - get_pathway_genes: matched genes in a pathway (fuzzy name matching)
  - compare_genes: side-by-side comparison of multiple genes
  - walk: the job's universal graph walk, or a short walk from a named gene
"""
import logging
from src.classes.AIInterpret.walker.card import labels_by_omic
from src.classes.AIInterpret.walker.overlay import values_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------
CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_feature_values",
            "description": (
                "Return a gene's values in every omic layer under the user's own "
                "column labels, with the relevant flag per layer. The design card "
                "says what the columns are (time points, doses or groups); a layer "
                "whose file had no header is reported as unlabeled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "gene_symbol": {
                        "type": "string",
                        "description": "Gene symbol to look up (case-insensitive), e.g. 'TP53'.",
                    }
                },
                "required": ["gene_symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pathway_genes",
            "description": (
                "Return all matched genes in a pathway with their values. "
                "Uses fuzzy name matching (case-insensitive substring). "
                "If no pathway matches, lists available pathway names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pathway_name": {
                        "type": "string",
                        "description": "Pathway name or partial name to search for, e.g. 'MAPK signaling'.",
                    }
                },
                "required": ["pathway_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_genes",
            "description": (
                "Return the values of several genes side by side, in every omic "
                "layer, under the user's own column labels."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "gene_symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of gene symbols to compare (max 10).",
                    }
                },
                "required": ["gene_symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "walk",
            "description": (
                "Return this job's universal graph walk: a chain of steps over KEGG, "
                "Reactome and OmniPath interaction edges with the user's values at "
                "every node, the statements that passed their checks, and the "
                "Results section. With gene_symbol, walk a few steps from that gene "
                "instead, to its relevant neighbours with the most surprising "
                "neighbourhoods, and read the values yourself; that walk uses no "
                "model and cites no paper."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "gene_symbol": {
                        "type": "string",
                        "description": "A gene to walk from. Omit it to read the stored universal walk.",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Steps for a walk from a gene, 3 to 12 (default 8).",
                    },
                },
                "required": [],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_header_map(job_instance):
    """omic name -> the user's column labels (None for an omic whose file had
    no header). The one reader every AI surface shares: see walker.card."""
    return labels_by_omic(job_instance)


def _find_gene_by_symbol(job_instance, symbol):
    """Case-insensitive gene lookup.  Returns (gene_id, gene_obj) or None."""
    target = symbol.upper()
    for gene_id, gene_obj in job_instance.getInputGenesData().items():
        if gene_obj.getName() and gene_obj.getName().upper() == target:
            return (gene_id, gene_obj)
    return None


def _format_gene_omics(gene_obj, header_map):
    """One line per omic layer: the relevant flag and the values under the
    user's labels. An omic whose file had no header, or whose header does not
    fit its values, prints numbered columns and says so; it never borrows the
    labels of another omic, which is how the miRNA columns of a job used to be
    called time points."""
    lines = []
    for ov in gene_obj.getOmicsValues():
        omic_name = ov.getOmicName()
        values = list(ov.getValues() or [])
        labels = header_map.get(omic_name)
        if labels and len(labels) != len(values):
            labels = None
        flag = "relevant" if ov.isRelevant() else "not relevant"
        text = values_text(values, labels) or "no values"
        if labels is None and values:
            text += "  (columns unlabeled)"
        lines.append(f"  {omic_name} ({flag}): {text}")
    return "\n".join(lines) if lines else "  (no omic data)"


# ---------------------------------------------------------------------------
# Executor functions — each takes (job_instance, args_dict) -> str
# ---------------------------------------------------------------------------

def _exec_get_feature_values(job_instance, args):
    symbol = args.get("gene_symbol", "").strip()
    if not symbol:
        return "Error: gene_symbol is required."

    result = _find_gene_by_symbol(job_instance, symbol)
    if result is None:
        return f"Gene '{symbol}' not found in this dataset."

    gene_id, gene_obj = result
    header_map = _build_header_map(job_instance)
    omics_text = _format_gene_omics(gene_obj, header_map)

    return (
        f"Gene: {gene_obj.getName()} (ID: {gene_id})\n"
        f"Values per layer, under the user's labels (the design card says what the columns are):\n"
        f"{omics_text}"
    )


def _exec_get_pathway_genes(job_instance, args):
    query = args.get("pathway_name", "").strip()
    if not query:
        return "Error: pathway_name is required."

    matched_pathways = job_instance.getMatchedPathways()
    input_genes = job_instance.getInputGenesData()
    query_upper = query.upper()

    # Fuzzy match: case-insensitive substring
    matches = [
        pw for pw in matched_pathways.values()
        if query_upper in pw.name.upper()
    ]

    if not matches:
        available = sorted(set(pw.name for pw in matched_pathways.values()))
        listing = "\n".join(f"  - {n}" for n in available[:30])
        suffix = f"\n  ... and {len(available) - 30} more" if len(available) > 30 else ""
        return (
            f"No pathway matching '{query}'. Available pathways:\n"
            f"{listing}{suffix}"
        )

    header_map = _build_header_map(job_instance)
    parts = []
    for pw in matches[:3]:  # limit to 3 pathway matches
        gene_lines = []
        for gid in pw.matchedGenes:
            gene = input_genes.get(gid)
            if gene is None:
                continue
            name = gene.getName() or gid
            # Brief summary: the first layer, under its own labels only
            omics = gene.getOmicsValues()
            if omics:
                ov = omics[0]
                vals = list(ov.getValues() or [])
                flag = "relevant" if ov.isRelevant() else "not relevant"
                labels = header_map.get(ov.getOmicName())
                if labels and len(labels) != len(vals):
                    labels = None
                val_str = values_text(vals, labels) or "no values"
                if labels is None and vals:
                    val_str += "  (columns unlabeled)"
                gene_lines.append(f"  {name}, {ov.getOmicName()} ({flag}): {val_str}")
            else:
                gene_lines.append(f"  {name}: (no data)")

        genes_text = "\n".join(gene_lines) if gene_lines else "  (no matched genes)"
        parts.append(
            f"Pathway: {pw.name} (ID: {pw.ID})\n"
            f"Matched genes ({len(pw.matchedGenes)}):\n{genes_text}"
        )

    return "\n\n".join(parts)


def _exec_compare_genes(job_instance, args):
    symbols = args.get("gene_symbols", [])
    if not symbols:
        return "Error: gene_symbols list is required."
    if len(symbols) > 10:
        symbols = symbols[:10]

    header_map = _build_header_map(job_instance)
    parts = []
    for sym in symbols:
        result = _find_gene_by_symbol(job_instance, sym.strip())
        if result is None:
            parts.append(f"Gene: {sym} — NOT FOUND")
        else:
            gene_id, gene_obj = result
            omics_text = _format_gene_omics(gene_obj, header_map)
            parts.append(f"Gene: {gene_obj.getName()} (ID: {gene_id})\n{omics_text}")

    return "\n\n".join(parts)


def _exec_walk(job_instance, args):
    """The stored universal walk, or a short scripted walk from a named gene."""
    from src.classes.AIInterpret.walker import service as walk_service
    symbol = str(args.get("gene_symbol") or "").strip()
    if symbol:
        return walk_service.quick_walk(job_instance, symbol, args.get("steps") or 8)
    from src.common.DAO.AIWalkDAO import AIWalkDAO
    dao = AIWalkDAO()
    try:
        return walk_service.stored_walk_text(dao.find(job_instance.getJobID(), "network"))
    finally:
        dao.closeConnection()


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
_EXECUTORS = {
    "get_feature_values": _exec_get_feature_values,
    "get_pathway_genes": _exec_get_pathway_genes,
    "compare_genes": _exec_compare_genes,
    "walk": _exec_walk,
}


def execute_tool(tool_name, job_instance, arguments):
    """Route a tool call to the correct executor. Returns a result string."""
    executor = _EXECUTORS.get(tool_name)
    if executor is None:
        return f"Error: unknown tool '{tool_name}'."
    try:
        return executor(job_instance, arguments)
    except Exception as e:
        logger.exception(f"Tool execution error ({tool_name})")
        return f"Error executing {tool_name}: {str(e)}"
