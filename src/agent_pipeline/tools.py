"""Tool schemas (sent to Claude) and the dispatch table that executes them.

Design: every heavy bioinformatics step (qc, alignment, mutation_calling,
cell_annotation, clustering, differential_expression, gsea) is started via
`start_job` and polled via `check_job_status` / `get_job_result` -- the
model is never blocked on a long shell command. Checkpointing happens
automatically inside the job's completion callback, not left to the model
to remember to do.
"""

from __future__ import annotations

from typing import Any

from . import jobs, state
from .steps import annotation, clustering, detect, diffexp, gsea, mutation, qc, alignment

STEP_FUNCS = {
    "qc": qc.run,
    "alignment": alignment.run,
    "mutation_calling": mutation.run,
    "cell_annotation": annotation.run,
    "clustering": clustering.run,
    "differential_expression": diffexp.run,
    "gsea": gsea.run,
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "inspect_data_source",
        "description": (
            "Classify a data path as DNA-exome fastq archive, scRNA fastq archive, scRNA count matrix "
            "(.h5), scRNA AnnData (.h5ad), or unknown -- WITHOUT extracting any archive. Always call this "
            "before deciding which pipeline branch (WES: qc/alignment/mutation_calling, or scRNA: "
            "cell_annotation/clustering/differential_expression/gsea) applies to a given input. "
            "Returns a data_type, supporting evidence, and details (e.g. n_cells/n_genes for matrices, "
            "archive size and peeked read length for fastq)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to inspect, relative to the repo root or absolute."}},
            "required": ["path"],
        },
    },
    {
        "name": "list_available_assets",
        "description": (
            "List files under a directory matching a glob pattern, with size and mtime. Use this to "
            "discover existing data (e.g. the pre-processed scRNA .h5 matrices under StrasbourgOC/data/scRNA) "
            "instead of guessing file paths."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Directory to list, relative to repo root or absolute."},
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.h5' or '**/*.h5ad'.", "default": "*"},
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["root"],
        },
    },
    {
        "name": "locate_fastq_pairs",
        "description": (
            "Scan a directory for paired-end fastq.gz files and return R1/R2 path pairs, "
            "one entry per sample. Use this before starting real-mode `qc` or `alignment` "
            "jobs: those steps require explicit `r1` and `r2` paths, not just a directory. "
            "Recognises common naming conventions: _R1_/_R2_, _1./_2., .R1./.R2., _R1./_R2."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Path to the directory that contains extracted fastq.gz files.",
                },
                "pattern": {
                    "type": "string",
                    "default": "**/*.fastq.gz",
                    "description": "Glob pattern relative to `directory` (default: **/*.fastq.gz).",
                },
            },
            "required": ["directory"],
        },
    },
    {
        "name": "start_job",
        "description": (
            "Start a pipeline step asynchronously and return a job_id immediately. Never blocks. "
            f"`step` must be one of: {', '.join(STEP_FUNCS)}. `args` is a free-form object whose fields "
            "depend on the step:\n"
            "  qc(sample_id, input_path, mode)  -- mock mode only needs input_path (zip dir).\n"
            "    Real mode requires: r1 (str), r2 (str) -- paths to the extracted R1/R2 fastq.gz\n"
            "    files (use locate_fastq_pairs first), output_dir (str, optional).\n"
            "  alignment(sample_id, input_path, mode)  -- mock mode only needs input_path.\n"
            "    Real mode requires: r1, r2 (extracted fastq.gz from qc trimming step),\n"
            "    output_dir (str, optional), reference (str, optional -- defaults to GRCh38).\n"
            "  mutation_calling(sample_id, input_path, mode)  -- mock mode only needs input_path.\n"
            "    Real mode requires: bam_path (str -- sorted BAM from alignment step),\n"
            "    output_dir (str, optional), reference (str, optional), known_sites (str, optional).\n"
            "  cell_annotation(sample_id, input_path, mode, [n_cells]) -- marker-based cell typing\n"
            "  clustering(sample_id, input_path, mode, [batch_key], [resolution]) -- Harmony+Leiden clustering\n"
            "  differential_expression(sample_id, input_path, mode, [group_key], [groups], [method]) -- rank_genes_groups\n"
            "  gsea(sample_id, mode, [group], [ranked_genes], [gene_sets]) -- gseapy prerank against a local .gmt\n"
            "`mode` is 'mock' (fast synthetic-but-plausible metrics; use this unless told otherwise) or "
            "'real' (shells out to the actual tool -- only use if explicitly instructed and after calling "
            "request_confirmation, since real mode can take a long time and touch real files)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "step": {"type": "string", "enum": list(STEP_FUNCS)},
                "args": {"type": "object", "description": "Step-specific arguments, see tool description."},
            },
            "required": ["step", "args"],
        },
    },
    {
        "name": "check_job_status",
        "description": "Poll a job started via start_job. Returns status: running | done | failed.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_job_result",
        "description": "Fetch the result of a job once check_job_status reports 'done'.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "request_confirmation",
        "description": (
            "Ask for human confirmation before an expensive or irreversible real-mode action "
            "(e.g. extracting a multi-hundred-GB archive, running a full GATK pipeline). In this "
            "session's mock-mode demo this always returns 'deferred' -- treat that as 'do not "
            "actually perform the real action; note it as a follow-up for the user instead.'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "reason": {"type": "string"},
                "estimated_cost": {"type": "string", "description": "e.g. '~1TB disk, ~6h runtime'"},
            },
            "required": ["action", "reason"],
        },
    },
    {
        "name": "read_checkpoint",
        "description": "Return a summary of steps already recorded for this run (for resuming a previous session).",
        "input_schema": {"type": "object", "properties": {}},
    },
]


_DISPATCH_TOOLS: list[dict[str, Any]] = [
    {
        "name": "dispatch_worker",
        "description": (
            "Dispatch a pipeline branch to the appropriate worker agent (Layer 2). "
            "Use branch='wes' for dna_exome_fastq_archive data; branch='scrna' for "
            "scrna_count_matrix / scrna_h5ad / scrna_matrix_directory data. "
            "The worker runs all steps in its branch and returns a findings summary. "
            "Call once per sample. Always call inspect_data_source first to confirm the branch."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "branch":    {"type": "string", "enum": ["wes", "scrna"]},
                "sample_id": {"type": "string", "description": "Identifier for this sample."},
                "input_path": {"type": "string", "description": "Path to the sample data file or directory."},
                "n_cells":   {"type": "integer", "description": "Optional cell count hint for scRNA (from inspect_data_source)."},
                "scenario":  {
                    "type": "string",
                    "enum": ["within_sample", "multi_group", "trajectory", "tme", "germline", "somatic", "multimodal"],
                    "description": (
                        "Analysis scenario: "
                        "'within_sample' — single group, DE between clusters; "
                        "'multi_group' — case/control or treatment groups, DE between groups; "
                        "'trajectory' — pseudotime / differentiation path; "
                        "'tme' — tumour microenvironment, immune infiltration; "
                        "'germline' — germline variant calling (WES); "
                        "'somatic' — paired tumour/normal somatic mutation calling (WES); "
                        "'multimodal' — combined WES + scRNA. "
                        "Defaults to 'within_sample' (scRNA) or 'germline' (WES) if omitted."
                    ),
                },
                "groups": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "For multi_group / tme / somatic: list of group labels (e.g. ['tumor','normal'] or ['pre_treatment','post_treatment']).",
                },
                "group_column": {
                    "type": "string",
                    "description": "Metadata column that encodes group membership (e.g. 'condition', 'treatment', 'diagnosis').",
                },
                "comparison": {
                    "type": "string",
                    "description": "Free-text description of the comparison (e.g. 'AML blast vs normal HSC, 3 replicates per group').",
                },
                "paired_normal_id": {
                    "type": "string",
                    "description": "For somatic WES: sample_id of the matched normal sample.",
                },
                "paired_normal_path": {
                    "type": "string",
                    "description": "For somatic WES: input_path of the matched normal sample.",
                },
            },
            "required": ["branch", "sample_id", "input_path"],
        },
    },
    {
        "name": "generate_report",
        "description": (
            "Trigger the Reporter agent (Layer 3) to synthesize all completed step results "
            "into a Markdown + HTML report written to result/<run_id>/report/. "
            "Call this after all dispatch_worker calls have returned successfully."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

_PLANNER_BASE_NAMES = {"inspect_data_source", "list_available_assets", "read_checkpoint", "request_confirmation"}
_WORKER_NAMES = {"start_job", "check_job_status", "get_job_result", "request_confirmation", "read_checkpoint", "locate_fastq_pairs"}
_REPORTER_NAMES = {"read_checkpoint", "list_available_assets"}

PLANNER_TOOLS: list[dict[str, Any]] = [t for t in TOOLS if t["name"] in _PLANNER_BASE_NAMES] + _DISPATCH_TOOLS
WORKER_TOOLS: list[dict[str, Any]] = [t for t in TOOLS if t["name"] in _WORKER_NAMES]
REPORTER_TOOLS: list[dict[str, Any]] = [t for t in TOOLS if t["name"] in _REPORTER_NAMES]


def _locate_fastq_pairs(directory: str, pattern: str) -> dict[str, Any]:
    """Find paired R1/R2 fastq.gz files in a directory.

    Recognises common WES naming conventions:
        SAMPLE_R1_001.fastq.gz / SAMPLE_R2_001.fastq.gz
        SAMPLE_R1.fastq.gz     / SAMPLE_R2.fastq.gz
        SAMPLE.R1.fastq.gz     / SAMPLE.R2.fastq.gz
        SAMPLE-R1.fastq.gz     / SAMPLE-R2.fastq.gz
        SAMPLE_1.fastq.gz      / SAMPLE_2.fastq.gz
    """
    import re
    from pathlib import Path

    from . import REPO_ROOT

    root = Path(directory)
    if not root.is_absolute():
        root = REPO_ROOT / root
    if not root.exists():
        return {"error": f"directory does not exist: {root}"}

    all_fastqs = sorted(root.glob(pattern))

    # Ordered from most specific to least to avoid ambiguous matches
    _PATTERNS = [
        re.compile(r"^(.+)[._\-]R([12])_\d+$", re.IGNORECASE),   # SAMPLE_R1_001
        re.compile(r"^(.+)[._\-]R([12])$", re.IGNORECASE),        # SAMPLE_R1 / SAMPLE.R1 / SAMPLE-R1
        re.compile(r"^(.+)[._\-]([12])$"),                         # SAMPLE_1 / SAMPLE_2
    ]

    def _parse(filename: str):
        stem = re.sub(r"\.(fastq|fq)(\.gz)?$", "", filename, flags=re.IGNORECASE)
        for pat in _PATTERNS:
            m = pat.match(stem)
            if m:
                return m.group(1), int(m.group(2))
        return None, None

    by_sample: dict[str, dict[str, str]] = {}
    unmatched: list[str] = []

    for f in all_fastqs:
        sample_key, read_idx = _parse(f.name)
        if sample_key and read_idx in (1, 2):
            by_sample.setdefault(sample_key, {})[f"r{read_idx}"] = str(f)
        else:
            unmatched.append(str(f.relative_to(root)))

    complete = [
        {"sample_id": s, "r1": v["r1"], "r2": v["r2"]}
        for s, v in sorted(by_sample.items())
        if "r1" in v and "r2" in v
    ]
    incomplete = [
        {"sample_id": s, **v}
        for s, v in sorted(by_sample.items())
        if not ("r1" in v and "r2" in v)
    ]

    return {
        "directory": str(root),
        "n_fastq_files": len(all_fastqs),
        "n_complete_pairs": len(complete),
        "pairs": complete,
        "incomplete": incomplete,
        "unmatched_files": unmatched,
        "note": (
            "Use the r1/r2 paths from `pairs` as explicit args when calling "
            "start_job for qc or alignment in real mode."
        ),
    }


def _job_on_complete(run_id: str, step_name: str, mode: str, args: dict[str, Any]):
    def _callback(job_id: str, result: dict[str, Any] | None, error: str | None) -> None:
        state.record_step(
            run_id,
            step=step_name,
            status="done" if error is None else "failed",
            mode=mode,
            inputs=args,
            outputs=result,
            job_id=job_id,
            error=error,
        )

    return _callback


def dispatch(run_id: str, name: str, tool_input: dict[str, Any], auto_approve: bool) -> dict[str, Any]:
    """Execute a single tool call. Returns the dict to JSON-encode as tool_result content."""
    if name == "locate_fastq_pairs":
        return _locate_fastq_pairs(tool_input["directory"], tool_input.get("pattern", "**/*.fastq.gz"))

    if name == "inspect_data_source":
        return detect.inspect(tool_input["path"])

    if name == "list_available_assets":
        from pathlib import Path

        from . import REPO_ROOT

        root = Path(tool_input["root"])
        if not root.is_absolute():
            root = REPO_ROOT / root
        pattern = tool_input.get("pattern", "*")
        limit = tool_input.get("limit", 30)
        if not root.exists():
            return {"error": f"directory does not exist: {root}"}
        matches = sorted(root.glob(pattern))[:limit]
        return {
            "root": str(root),
            "pattern": pattern,
            "count": len(matches),
            "files": [
                {"path": str(m.relative_to(root)), "size_bytes": m.stat().st_size if m.is_file() else None}
                for m in matches
            ],
        }

    if name == "start_job":
        step = tool_input["step"]
        args = tool_input.get("args", {})
        if step not in STEP_FUNCS:
            return {"error": f"unknown step '{step}'. valid steps: {list(STEP_FUNCS)}"}
        mode = args.get("mode", "mock")
        job_id = jobs.start_job(
            step, STEP_FUNCS[step], args, on_complete=_job_on_complete(run_id, step, mode, args)
        )
        state.record_step(run_id, step=step, status="started", mode=mode, inputs=args, job_id=job_id)
        return {"job_id": job_id, "step": step, "status": "started"}

    if name == "check_job_status":
        return jobs.check_job_status(tool_input["job_id"])

    if name == "get_job_result":
        return jobs.get_job_result(tool_input["job_id"])

    if name == "request_confirmation":
        if auto_approve:
            return {"decision": "approved_mock_only", "note": "Auto-approve is on for this demo session; real-mode execution is still deferred."}
        return {"decision": "deferred", "note": "No interactive confirmation available in this run; treat as not approved."}

    if name == "read_checkpoint":
        return {"summary": state.summarize_state(run_id)}

    return {"error": f"unknown tool: {name}"}
