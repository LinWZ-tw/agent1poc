"""Reporter Agent — Layer 3: reads the checkpoint and writes Markdown + HTML report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .. import state
from ..prompts.reporter import SYSTEM_PROMPT
from ..providers import ANTHROPIC_MODEL_DEFAULT, OPENAI_MODEL_DEFAULT, make_provider
from ..tools import REPORTER_TOOLS, dispatch

MAX_ITERATIONS = 20


# ── HTML rendering ────────────────────────────────────────────────────────────

def _md_to_html_body(md: str) -> str:
    """Convert a Markdown string to an HTML body fragment (no <html> wrapper)."""
    lines = md.splitlines()
    out: list[str] = []
    in_code = False
    in_table = False
    li_open = False

    def _flush_li():
        nonlocal li_open
        if li_open:
            out.append("</ul>")
            li_open = False

    def _flush_table():
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    for line in lines:
        # fenced code blocks
        if line.startswith("```"):
            _flush_li()
            _flush_table()
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                lang = line[3:].strip()
                out.append(f'<pre><code class="language-{lang}">' if lang else "<pre><code>")
                in_code = True
            continue
        if in_code:
            out.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            continue

        # table rows
        if line.startswith("|"):
            _flush_li()
            if re.match(r"^\|[-| :]+\|$", line):
                continue  # separator row
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not in_table:
                out.append('<table><thead><tr>')
                out.append("".join(f"<th>{c}</th>" for c in cells))
                out.append("</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        _flush_table()

        # headings
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            _flush_li()
            lvl = len(m.group(1))
            text = _inline(m.group(2))
            slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
            out.append(f'<h{lvl} id="{slug}">{text}</h{lvl}>')
            continue

        # horizontal rules
        if re.match(r"^---+$", line.strip()):
            _flush_li()
            out.append("<hr>")
            continue

        # list items
        if re.match(r"^\s*[-*]\s+", line):
            if not li_open:
                out.append("<ul>")
                li_open = True
            out.append(f"<li>{_inline(re.sub(r'^\s*[-*]\s+', '', line))}</li>")
            continue
        _flush_li()

        # blank line
        if not line.strip():
            out.append("")
            continue

        out.append(f"<p>{_inline(line)}</p>")

    _flush_li()
    _flush_table()
    return "\n".join(out)


def _inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


_FIGURE_CAPTIONS = {
    "celltype_composition": "Cell Type Composition",
    "umap_clusters": "UMAP — Leiden Clusters (mock)",
    "cluster_sizes": "Cluster Sizes (Leiden)",
    "de_genes": "Top DE Genes",
    "gsea": "GSEA Enrichment",
    "wes_variants": "WES Variant Summary",
}


def _caption(png_path: str) -> str:
    stem = Path(png_path).stem
    for prefix, cap in _FIGURE_CAPTIONS.items():
        if stem.startswith(prefix):
            return cap
    return stem


def _figures_html(figure_paths: dict[str, list[str]], report_dir: Path) -> str:
    if not figure_paths:
        return ""
    parts = ['<section id="figures">', "<h2>Figures</h2>"]
    for sid, paths in figure_paths.items():
        parts.append(f'<div class="sample-figures">')
        parts.append(f"<h3>Sample: {sid}</h3>")
        parts.append('<div class="figure-grid">')
        for p in paths:
            rel = Path(p).relative_to(report_dir)
            cap = _caption(p)
            parts.append(
                f'<figure><img src="{rel}" alt="{cap}">'
                f"<figcaption>{cap}</figcaption></figure>"
            )
        parts.append("</div></div>")
    parts.append("</section>")
    return "\n".join(parts)


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipeline Report — {run_id}</title>
<style>
  :root {{
    --bg: #ffffff; --text: #1a1a2e; --muted: #555; --border: #dde;
    --accent: #2563eb; --code-bg: #f4f4f8; --table-head: #eef2ff;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
         background: var(--bg); color: var(--text); margin: 0;
         display: flex; min-height: 100vh; }}
  nav {{ width: 220px; min-width: 200px; padding: 32px 16px;
        position: sticky; top: 0; height: 100vh; overflow-y: auto;
        border-right: 1px solid var(--border); font-size: 13px; }}
  nav h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
            color: var(--muted); margin: 0 0 12px; }}
  nav a {{ display: block; color: var(--accent); text-decoration: none;
           padding: 3px 0; line-height: 1.4; }}
  nav a:hover {{ text-decoration: underline; }}
  main {{ flex: 1; max-width: 860px; padding: 40px 48px; overflow-x: hidden; }}
  h1 {{ font-size: 1.8rem; border-bottom: 2px solid var(--accent);
        padding-bottom: 10px; margin-top: 0; }}
  h2 {{ font-size: 1.3rem; margin-top: 2.2em; border-bottom: 1px solid var(--border);
        padding-bottom: 6px; color: #1e3a8a; }}
  h3 {{ font-size: 1.05rem; margin-top: 1.6em; color: #1e40af; }}
  h4 {{ font-size: 0.95rem; margin-top: 1.2em; color: var(--muted); }}
  p {{ line-height: 1.65; margin: 0.6em 0 0.9em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 13.5px; }}
  th {{ background: var(--table-head); text-align: left; padding: 7px 10px;
        border: 1px solid var(--border); font-weight: 600; }}
  td {{ padding: 6px 10px; border: 1px solid var(--border); }}
  tr:nth-child(even) td {{ background: #f9f9fc; }}
  code {{ background: var(--code-bg); padding: 2px 5px; border-radius: 3px;
          font-size: 0.88em; }}
  pre {{ background: var(--code-bg); border: 1px solid var(--border);
         border-radius: 6px; padding: 14px; overflow-x: auto; }}
  pre code {{ background: none; padding: 0; font-size: 0.85em; }}
  ul {{ margin: 0.5em 0 0.9em 1.4em; line-height: 1.6; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 2em 0; }}
  .figure-grid {{ display: flex; flex-wrap: wrap; gap: 20px; margin: 1.2em 0; }}
  figure {{ margin: 0; flex: 1 1 380px; }}
  figure img {{ max-width: 100%; border: 1px solid var(--border);
                border-radius: 6px; display: block; }}
  figcaption {{ font-size: 12px; color: var(--muted); margin-top: 6px;
                text-align: center; }}
  .sample-figures {{ margin-bottom: 2em; }}
  #figures h2 {{ color: #166534; border-color: #bbf7d0; }}
  #figures h3 {{ color: #166534; }}
</style>
</head>
<body>
<nav>
  <h2>Contents</h2>
  {toc}
</nav>
<main>
{body}
{figures_section}
<hr>
<p style="font-size:12px;color:var(--muted);">
  Generated by the bioinformatics pipeline agent &mdash; run <code>{run_id}</code>
</p>
</main>
</body>
</html>"""


def _make_toc(md: str) -> str:
    links = []
    for line in md.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            lvl = len(m.group(1))
            raw = re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(2))
            raw = re.sub(r"`(.+?)`", r"\1", raw)
            slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
            indent = "&nbsp;" * ((lvl - 1) * 3)
            links.append(f'<a href="#{slug}">{indent}{raw}</a>')
    links.append('<a href="#figures">&nbsp;&nbsp;&nbsp;Figures</a>')
    return "\n  ".join(links)


def _build_html(
    md: str,
    run_id: str,
    figure_paths: dict[str, list[str]],
    report_dir: Path,
) -> str:
    body = _md_to_html_body(md)
    toc = _make_toc(md)
    figures_section = _figures_html(figure_paths, report_dir)
    return _HTML_TEMPLATE.format(
        run_id=run_id,
        toc=toc,
        body=body,
        figures_section=figures_section,
    )


# ── agent run ─────────────────────────────────────────────────────────────────

_LABEL = "Reporter"


def run(
    *,
    run_id: str,
    provider_name: str = "anthropic",
    api_key: str = "",
    model: str | None = None,
    base_url: str | None = None,
    effort: str = "high",
    emit_fn: Callable[..., None] | None = None,
    auto_approve: bool = True,
) -> dict[str, Any]:
    _model = model or (ANTHROPIC_MODEL_DEFAULT if provider_name == "anthropic" else OPENAI_MODEL_DEFAULT)
    _emit = emit_fn or (lambda **_: None)

    provider = make_provider(
        provider_name,
        api_key=api_key,
        model=_model,
        system_prompt=SYSTEM_PROMPT,
        base_url=base_url,
        effort=effort,
        tools=REPORTER_TOOLS,
    )

    provider.send_user_text(
        f"Generate the final analysis report for run '{run_id}'. "
        f"Call read_checkpoint to retrieve all step results, then write the full Markdown report."
    )
    state.append_log(run_id, {"event": "reporter_start"})
    _emit(type="system", text="Reporter started — synthesising report from checkpoint", agent=_LABEL)

    report_text = ""
    for _ in range(MAX_ITERATIONS):
        result = provider.step()
        if result.thinking:
            _emit(type="thinking", text=result.thinking, agent=_LABEL)
        if result.text:
            report_text = result.text
            _emit(type="text", text=result.text, agent=_LABEL)
        if result.stop_reason != "tool_use":
            break
        tool_results = []
        for call in result.tool_calls:
            _emit(type="tool_call", name=call["name"], input=call["input"], agent=_LABEL)
            try:
                output = dispatch(run_id, call["name"], call["input"], auto_approve)
                is_error = bool(isinstance(output, dict) and output.get("error"))
            except Exception as exc:  # noqa: BLE001
                output = {"error": f"{type(exc).__name__}: {exc}"}
                is_error = True
            _emit(type="tool_result", name=call["name"], output=output, is_error=is_error, agent=_LABEL)
            tool_results.append({
                "tool_use_id": call["id"],
                "content": json.dumps(output, default=str),
                "is_error": is_error,
            })
        provider.send_tool_results(tool_results)

    # Generate figures from raw checkpoint data (Python, not LLM)
    rdir = state.report_dir(run_id)
    checkpoint = state.load_state(run_id)
    figure_paths: dict[str, list[str]] = {}
    try:
        from .. import figures as fig_module
        figure_paths = fig_module.generate_figures_for_run(run_id, checkpoint, rdir)
        state.append_log(run_id, {
            "event": "figures_generated",
            "n_samples": len(figure_paths),
            "n_figures": sum(len(v) for v in figure_paths.values()),
        })
    except Exception as exc:  # noqa: BLE001
        state.append_log(run_id, {"event": "figures_error", "error": str(exc)})

    md_path = rdir / "report.md"
    html_path = rdir / "report.html"
    md_path.write_text(report_text)
    html_path.write_text(_build_html(report_text, run_id, figure_paths, rdir))

    state.append_log(run_id, {"event": "reporter_end", "report_md": str(md_path)})
    _emit(type="system", text=f"Reporter finished — report.html written to {rdir}", agent=_LABEL)
    return {"report_md": str(md_path), "report_html": str(html_path)}
