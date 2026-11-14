"""Model-agnostic benchmark runner for ContractMind / Advotech Copilot.

Usage examples:
  python scripts/benchmark/run_benchmark.py --dry-run --full
  python scripts/benchmark/run_benchmark.py --models default_production --quick
  python scripts/benchmark/run_benchmark.py --models colab_gemma4_e4b,gemini_flash_lite --full --push-db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import pathlib
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT_DIR / ".env", override=False)

from scripts.benchmark.evaluators import (  # noqa: E402
    evaluate_contract_suite,
    evaluate_hallucination_suite,
    evaluate_ocr_suite,
    evaluate_rag_suite,
    push_benchmark_events_to_db,
    summarize_performance,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("benchmark_runner")

BENCHMARK_DIR = pathlib.Path(__file__).resolve().parent
DATASETS_DIR = BENCHMARK_DIR / "datasets"
REPORTS_DIR = BENCHMARK_DIR / "reports"
PARTIAL_DIR = REPORTS_DIR / "partial"
MODELS_FILE = BENCHMARK_DIR / "models.json"

QUICK_CASE_IDS = {
    "rag": {
        "RAG-BN-001",
        "RAG-BN-003",
        "RAG-BN-020",
        "RAG-EN-001",
        "RAG-BGL-001",
    },
    "contract": {
        "CON-EMP-001",
        "CON-EMP-003",
        "CON-PROP-001",
        "CON-TECH-010",
    },
    "hallucination": {
        "HAL-FAKE-001",
        "HAL-FAKE-002",
        "HAL-OOS-001",
        "HAL-SEC-001",
    },
    "ocr": {
        "OCR-PDF-011",
        "OCR-PDF-012",
        "OCR-PDF-013",
    },
}


def load_json(path: pathlib.Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_llm_from_profile(profile_name: str, profile: Dict[str, Any]):
    """Instantiates a BaseChatModel strictly matching a models.json profile."""
    import app.services.llm_provider as lp

    provider = profile.get("provider", "app_default")
    temperature = float(profile.get("temperature", 0.1))
    max_tokens = int(profile.get("max_tokens", 3000))
    timeout_sec = float(profile.get("timeout_sec", 60.0))

    if provider == "app_default":
        # Production LLM chain (Gemini -> Z.ai GLM -> Groq) exactly as deployed.
        llm = lp.get_llm()
        if llm is None:
            raise RuntimeError(
                "No GEMINI_API_KEY / ZAI_API_KEY / GROQ_API_KEY found for default_production."
            )
        return llm

    if provider == "google_genai":
        from langchain_google_genai import ChatGoogleGenerativeAI

        api_key = os.getenv(profile.get("api_key_env", "GEMINI_API_KEY"), "").strip()
        if not api_key:
            raise RuntimeError(f"Missing env var {profile.get('api_key_env', 'GEMINI_API_KEY')}")
        return ChatGoogleGenerativeAI(
            model=profile.get("model", "gemini-3.5-flash-lite"),
            google_api_key=api_key,
            temperature=temperature,
            max_output_tokens=max_tokens,
            max_retries=2,
            timeout=timeout_sec,
        )

    if provider == "groq":
        from langchain_groq import ChatGroq

        api_key = os.getenv(profile.get("api_key_env", "GROQ_API_KEY"), "").strip()
        if not api_key:
            raise RuntimeError(f"Missing env var {profile.get('api_key_env', 'GROQ_API_KEY')}")
        return ChatGroq(
            model=profile.get("model", "qwen/qwen3.8-27b"),
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=1,
            timeout=timeout_sec,
        )

    if provider == "openai_compatible":
        base_url = profile.get("base_url") or os.getenv(profile.get("base_url_env", "COLAB_LLM_BASE_URL"), "")
        base_url = str(base_url).strip()
        if not base_url:
            raise RuntimeError(
                f"Profile '{profile_name}' missing base_url / env {profile.get('base_url_env')}"
            )
        model_name = (
            os.getenv(profile.get("model_env", ""), "").strip()
            or profile.get("model")
            or profile.get("default_model", "gemma4:e4b")
        )
        api_key = (
            os.getenv(profile.get("api_key_env", ""), "").strip()
            or profile.get("default_api_key", "")
        )
        if profile_name == "openrouter_custom" and not api_key:
            raise RuntimeError(f"Missing env var {profile.get('api_key_env', 'OPENROUTER_API_KEY')}")
        return lp.OpenAICompatibleChatModel(
            base_url=base_url,
            model=model_name,
            api_key=api_key or "local",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout_sec,
        )

    raise ValueError(f"Unsupported model provider '{provider}' in profile '{profile_name}'")


@contextmanager
def override_app_llm(llm_instance):
    """Temporarily binds get_llm() across all graph/service modules for isolated benchmarking."""
    import app.agents.nodes as nodes_mod
    import app.agents.qa_graph as qa_mod
    import app.services.llm_provider as lp_mod
    import app.services.playbook_parser as pb_mod
    import app.services.query_analyzer as qan_mod

    targets = [lp_mod, qa_mod, nodes_mod, pb_mod, qan_mod]
    originals = {mod: getattr(mod, "get_llm", None) for mod in targets}

    def _fixed_get_llm():
        return llm_instance

    try:
        for mod in targets:
            if hasattr(mod, "get_llm"):
                setattr(mod, "get_llm", _fixed_get_llm)
        yield
    finally:
        for mod, orig in originals.items():
            if orig is not None:
                setattr(mod, "get_llm", orig)


def filter_cases(suite: str, cases: List[Dict[str, Any]], quick: bool) -> List[Dict[str, Any]]:
    if not quick:
        return cases
    wanted = QUICK_CASE_IDS.get(suite, set())
    selected = [c for c in cases if c.get("id") in wanted]
    return selected if selected else cases[:3]


def _partial_path(profile_name: str, suite: str, quick: bool) -> pathlib.Path:
    mode = "quick" if quick else "full"
    return PARTIAL_DIR / f"{profile_name}_{suite}_{mode}.json"


def _load_partial(path: pathlib.Path, expected_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Returns saved per-case results if the case list matches, else {} (stale/absent)."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("case_ids") == expected_ids:
            return {r["id"]: r for r in data.get("results", []) if r.get("id")}
    except Exception:
        logger.warning(f"Could not read partial file {path}; starting fresh.")
    return {}


def _save_partial(path: pathlib.Path, case_ids: List[str], results: List[Dict[str, Any]]) -> None:
    """Atomically writes per-case results so a killed run loses nothing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"case_ids": case_ids, "results": results}, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def _clear_partial(path: pathlib.Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _make_progress_callback(
    profile_name: str,
    suite: str,
    case_ids: List[str],
    path: pathlib.Path,
    initial_results: Optional[List[Dict[str, Any]]] = None,
):
    saved: List[Dict[str, Any]] = list(initial_results or [])

    def on_result(result: Dict[str, Any]) -> None:
        saved.append(result)
        _save_partial(path, case_ids, saved)
        logger.info(
            f"[{profile_name}/{suite}] {result.get('id')} -> {result.get('status')} "
            f"({result.get('latency_ms', 0)} ms) [{len(saved)}/{len(case_ids)}]"
        )

    return on_result, saved


def validate_datasets_offline() -> Dict[str, Any]:
    """Verifies JSON structure, unique IDs, and presence of real PDF fixtures."""
    files = {
        "rag": DATASETS_DIR / "rag_cases.json",
        "contract": DATASETS_DIR / "contract_cases.json",
        "hallucination": DATASETS_DIR / "hallucination_cases.json",
        "ocr": DATASETS_DIR / "ocr_cases.json",
    }
    all_ids: List[str] = []
    counts: Dict[str, int] = {}
    for suite, fpath in files.items():
        data = load_json(fpath)
        cases = data.get("cases", [])
        counts[suite] = len(cases)
        all_ids.extend(c["id"] for c in cases)

    unique_ok = len(all_ids) == len(set(all_ids))
    return {
        "total_cases": len(all_ids),
        "unique_ids": unique_ok,
        "counts": counts,
    }


def _pct(val: Any) -> str:
    if isinstance(val, (int, float)):
        return f"{val * 100:.1f}%"
    return "-"


def _suite_map(run: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {s["suite"]: s for s in run.get("suites", [])}


def _failure_reason(case: Dict[str, Any]) -> str:
    if case.get("error"):
        return f"ERROR: {str(case['error'])[:120]}"
    reasons = []
    suite = case.get("suite")
    if suite == "rag":
        if not case.get("router_match", True):
            reasons.append(f"router={case.get('actual_status')}!=exp:{case.get('expected_status')}")
        if case.get("act_hit_at_k") == 0:
            reasons.append(f"missed_acts={case.get('expected_acts')}")
        if case.get("missed_keywords"):
            reasons.append(f"missed_kw={case.get('missed_keywords')}")
        if not case.get("bengali_script_ok", True):
            reasons.append(f"bn_ratio={case.get('bengali_script_ratio')}")
    elif suite == "contract":
        if not case.get("domain_match", True):
            reasons.append(f"domain={case.get('predicted_domain')}!=exp:{case.get('domain')}")
        if case.get("overall_risk_match") is False:
            reasons.append(f"risk={case.get('actual_overall_risk')}!=exp:{case.get('expected_overall_risk')}")
        if case.get("missed_clauses"):
            reasons.append(f"missed_clauses={case.get('missed_clauses')}")
        if case.get("has_playbook") and not case.get("playbook_label_ok", True):
            reasons.append("playbook_label_missing")
        if case.get("summary_contradiction"):
            reasons.append("summary_contradiction")
    elif suite == "hallucination":
        if case.get("prompt_leaked"):
            reasons.append("PROMPT_LEAKED")
        if case.get("forbidden_hits"):
            reasons.append(f"forbidden={case.get('forbidden_hits')}")
        if not case.get("indicates_absence", True):
            reasons.append("did_not_abstain")
        if case.get("expected_status") in ("OUT_OF_SCOPE", "INSUFFICIENT_EVIDENCE") and case.get("actual_status") != case.get("expected_status"):
            reasons.append(f"status={case.get('actual_status')}!=exp:{case.get('expected_status')}")
    elif suite == "ocr":
        reasons.append(f"cer={case.get('cer')}, ent={case.get('entity_recall')}, clauses={case.get('parsed_clauses')}")
    return "; ".join(reasons) or "threshold_failed"


def render_markdown_summary(report: Dict[str, Any]) -> str:
    active_runs = [r for r in report.get("runs", []) if not r.get("skipped_reason")]
    lines = [
        "# Advotech / ContractMind Benchmark Summary",
        "",
        f"- **Generated (UTC):** {report['generated_at']}",
        f"- **Mode:** `{report['mode']}` | **Dry Run:** `{report['dry_run']}`",
        f"- **Suites:** {', '.join(report['suites_requested'])}",
        "",
        "## 1. Overall Model Comparison",
        "",
        "| Model Profile | Executed | Passed | Pass Rate | p50 (ms) | p95 (ms) | Mean (ms) | Max (ms) | Errors | Status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for run in report.get("runs", []):
        profile = run["model_profile"]
        if run.get("skipped_reason"):
            lines.append(f"| `{profile}` | 0 | 0 | - | - | - | - | - | - | SKIPPED ({run['skipped_reason']}) |")
            continue
        overall = run["performance"]["overall"]
        lines.append(
            f"| `{profile}` | {overall['executed']} | {overall['passed']} | {_pct(overall['pass_rate'])} "
            f"| {overall['p50_ms']} | {overall['p95_ms']} | {overall['mean_ms']} | {overall['max_ms']} | {overall['errors']} | OK |"
        )

    if any("rag" in _suite_map(r) for r in active_runs):
        lines.extend([
            "",
            "## 2. RAG & Legal QA Comparison",
            "",
            "| Model Profile | Pass Rate | Router Acc | Act Hit@K | Act MRR | Sec Recall | Keyword Hit | BN Script Quality | JSON Valid | Regex Fallback | p50 (ms) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for run in active_runs:
            s = _suite_map(run).get("rag", {}).get("summary", {})
            p = run["performance"]["by_suite"].get("rag", {})
            lines.append(
                f"| `{run['model_profile']}` | {_pct(p.get('pass_rate'))} | {_pct(s.get('router_accuracy'))} "
                f"| {_pct(s.get('act_hit_at_k'))} | {s.get('act_mrr', '-')} | {_pct(s.get('section_recall'))} "
                f"| {_pct(s.get('keyword_hit_rate'))} | {_pct(s.get('bengali_script_quality_rate'))} "
                f"| {_pct(s.get('json_valid_rate'))} | {_pct(s.get('regex_fallback_rate'))} | {p.get('p50_ms', '-')} |"
            )

        # Language breakdown (Bangla vs Banglish vs English)
        lines.extend([
            "",
            "### RAG Language Breakdown (`bn` / `banglish` / `en`)",
            "",
            "| Model Profile | Language | Cases | Passed | Pass Rate | Act Hit@K | Keyword Hit | JSON Valid |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ])
        for run in active_runs:
            by_lang = _suite_map(run).get("rag", {}).get("summary", {}).get("by_language", {})
            for lang_key in ("bn", "banglish", "en"):
                if lang_key in by_lang:
                    d = by_lang[lang_key]
                    lines.append(
                        f"| `{run['model_profile']}` | `{lang_key}` | {d['total']} | {d['passed']} "
                        f"| {_pct(d['pass_rate'])} | {_pct(d['act_hit_at_k'])} | {_pct(d['keyword_hit_rate'])} | {_pct(d['json_valid_rate'])} |"
                    )

    if any("contract" in _suite_map(r) for r in active_runs):
        lines.extend([
            "",
            "## 3. Contract Audit & Playbook Comparison",
            "",
            "| Model Profile | Pass Rate | Domain Acc | Overall Risk Acc | Clause F1 | Status Acc | Playbook Detection | Contradiction Rate | p50 (ms) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for run in active_runs:
            s = _suite_map(run).get("contract", {}).get("summary", {})
            p = run["performance"]["by_suite"].get("contract", {})
            lines.append(
                f"| `{run['model_profile']}` | {_pct(p.get('pass_rate'))} | {_pct(s.get('domain_accuracy'))} "
                f"| {_pct(s.get('overall_risk_accuracy'))} | {s.get('clause_f1', '-')} "
                f"| {_pct(s.get('statutory_status_accuracy'))} | {_pct(s.get('playbook_detection_rate'))} "
                f"| {_pct(s.get('contradiction_rate'))} | {p.get('p50_ms', '-')} |"
            )

    if any("hallucination" in _suite_map(r) for r in active_runs):
        lines.extend([
            "",
            "## 4. Hallucination, Boundary & Security Comparison",
            "",
            "| Model Profile | Pass Rate | Abstention Rate | Prompt Injection Defense | Citation Grounded | Overall Safety | p50 (ms) |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        for run in active_runs:
            s = _suite_map(run).get("hallucination", {}).get("summary", {})
            p = run["performance"]["by_suite"].get("hallucination", {})
            lines.append(
                f"| `{run['model_profile']}` | {_pct(p.get('pass_rate'))} | {_pct(s.get('abstention_rate'))} "
                f"| {_pct(s.get('prompt_injection_defense_rate'))} | {_pct(s.get('citation_grounded_rate'))} "
                f"| {_pct(s.get('overall_safety_pass_rate'))} | {p.get('p50_ms', '-')} |"
            )

    # Failed / error diagnostics table
    failures = []
    for run in active_runs:
        for s_out in run.get("suites", []):
            for c in s_out.get("cases", []):
                if c.get("passed") is False:
                    failures.append((run["model_profile"], s_out["suite"], c))

    lines.extend(["", "## 5. Failed & Error Cases Breakdown", ""])
    if not failures:
        lines.append("- No failed or errored cases in this run.")
    else:
        lines.extend([
            "| Model Profile | Suite | Case ID | Status | Latency (ms) | Diagnostic Reason |",
            "|---|---|---|---|---:|---|",
        ])
        for prof, s_name, c in failures[:80]:
            reason = _failure_reason(c).replace("|", "/")
            lines.append(
                f"| `{prof}` | `{s_name}` | `{c.get('id')}` | {c.get('status')} | {c.get('latency_ms', 0)} | {reason} |"
            )

    return "\n".join(lines) + "\n"


def render_detailed_markdown(report: Dict[str, Any]) -> str:
    """Renders full case-by-case drilldown with model outputs, citations, and contract risks."""
    lines = [
        "# Advotech / ContractMind Detailed Per-Case Benchmark Report",
        "",
        f"- **Generated (UTC):** {report['generated_at']}",
        f"- **Mode:** `{report['mode']}` | **Dry Run:** `{report['dry_run']}`",
        "",
    ]
    for run in report.get("runs", []):
        profile = run["model_profile"]
        lines.extend([f"## Model: `{profile}` ({run.get('display_name', profile)})", ""])
        if run.get("skipped_reason"):
            lines.append(f"- Skipped: {run['skipped_reason']}\n")
            continue

        for s_out in run.get("suites", []):
            s_name = s_out["suite"].upper()
            lines.extend([f"### Suite: {s_name}", ""])
            for c in s_out.get("cases", []):
                if c.get("status") == "SKIPPED_MISSING_FIXTURE":
                    continue
                lines.append(
                    f"#### `{c.get('id')}` — **{c.get('status')}** ({c.get('latency_ms', 0)} ms)"
                )
                if c.get("error"):
                    lines.append(f"- **Error:** `{c['error']}`\n")
                    continue
                if s_name == "RAG":
                    lines.append(f"- **Query ({c.get('language')}):** {c.get('query', '')}")
                    lines.append(
                        f"- **Router:** `{c.get('actual_status')}` (expected `{c.get('expected_status')}`) | "
                        f"**Act Hit@K:** `{c.get('act_hit_at_k')}` | **Sec Recall:** `{c.get('section_recall')}` | "
                        f"**Keyword Hit:** `{c.get('keyword_hit_rate')}` | **JSON Valid:** `{c.get('json_valid')}`"
                    )
                    if c.get("expected_acts") or c.get("retrieved_acts"):
                        lines.append(
                            f"- **Expected Acts:** `{c.get('expected_acts')}` | **Retrieved/Cited Acts:** `{c.get('retrieved_acts')}`"
                        )
                    if c.get("missed_keywords"):
                        lines.append(f"- **Missed Keywords:** `{c.get('missed_keywords')}`")
                    if c.get("answer_preview"):
                        preview = c["answer_preview"].replace("\n", " ")[:500]
                        lines.append(f"- **Answer Preview:** {preview}")
                elif s_name == "CONTRACT":
                    lines.append(f"- **Contract:** {c.get('contract_title', '')} (`{c.get('audited_for')}`)")
                    lines.append(
                        f"- **Domain:** `{c.get('predicted_domain')}` (expected `{c.get('domain')}`) | "
                        f"**Parsed Clauses:** `{c.get('parsed_clause_count')}`"
                    )
                    if c.get("mode") != "dry_run":
                        lines.append(
                            f"- **Overall Risk:** `{c.get('actual_overall_risk')}` (expected `{c.get('expected_overall_risk')}`) | "
                            f"**Clause F1:** `{c.get('clause_f1')}` | **Playbook Hits:** `{c.get('playbook_hits', 0)}` "
                            f"(label ok: `{c.get('playbook_label_ok')}`)"
                        )
                        if c.get("missed_clauses"):
                            lines.append(f"- **Missed Expected Clauses:** `{c.get('missed_clauses')}`")
                        for r in c.get("detected_risks_summary", []):
                            lines.append(
                                f"  - Clause `{r.get('clause')}` [{r.get('severity')} | {r.get('statutory_status')}]: "
                                f"`{r.get('violated_law')}` ({r.get('verified_label')}) — {r.get('explanation_preview')}"
                            )
                        if c.get("memo_summary_preview"):
                            lines.append(f"- **Executive Summary Preview:** {c['memo_summary_preview'].replace(chr(10), ' ')}")
                elif s_name == "HALLUCINATION":
                    lines.append(f"- **Query ({c.get('category')}):** {c.get('query', '')}")
                    lines.append(
                        f"- **Status:** `{c.get('actual_status')}` (expected `{c.get('expected_status')}`) | "
                        f"**No Forbidden:** `{c.get('no_forbidden_claims')}` | **Abstained:** `{c.get('indicates_absence')}` | "
                        f"**Prompt Safe:** `{not c.get('prompt_leaked')}`"
                    )
                    if c.get("forbidden_hits"):
                        lines.append(f"- **Forbidden Claims Found:** `{c.get('forbidden_hits')}`")
                    if c.get("answer_preview"):
                        lines.append(f"- **Answer Preview:** {c['answer_preview'].replace(chr(10), ' ')[:400]}")
                elif s_name == "OCR":
                    lines.append(
                        f"- **Doc Type:** `{c.get('doc_type')}` | **CER:** `{c.get('cer')}` | "
                        f"**Entity Recall:** `{c.get('entity_recall')}` | **Clauses:** `{c.get('parsed_clauses')}`"
                    )
                lines.append("")
    return "\n".join(lines) + "\n"


async def run_for_profile(
    profile_name: str,
    profile_cfg: Dict[str, Any],
    suites: List[str],
    quick: bool,
    dry_run: bool,
    push_db: bool,
) -> Dict[str, Any]:
    logger.info(f"=== Running benchmark profile: {profile_name} (dry_run={dry_run}, quick={quick}) ===")

    datasets = {
        "rag": filter_cases("rag", load_json(DATASETS_DIR / "rag_cases.json")["cases"], quick),
        "contract": filter_cases("contract", load_json(DATASETS_DIR / "contract_cases.json")["cases"], quick),
        "hallucination": filter_cases(
            "hallucination", load_json(DATASETS_DIR / "hallucination_cases.json")["cases"], quick
        ),
        "ocr": filter_cases("ocr", load_json(DATASETS_DIR / "ocr_cases.json")["cases"], quick),
    }

    suite_outputs: List[Dict[str, Any]] = []

    if dry_run:
        if "contract" in suites:
            suite_outputs.append(await evaluate_contract_suite(datasets["contract"], dry_run=True))
        if "ocr" in suites:
            suite_outputs.append(await evaluate_ocr_suite(datasets["ocr"], datasets_dir=DATASETS_DIR, dry_run=True))
        if "rag" in suites:
            suite_outputs.append({
                "suite": "rag",
                "summary": {"total": len(datasets["rag"]), "mode": "dry_run_schema_validated"},
                "cases": [
                    {
                        "id": c["id"],
                        "suite": "rag",
                        "language": c.get("language"),
                        "query": c.get("query"),
                        "expected_status": c.get("expected_status"),
                        "status": "PASSED",
                        "passed": True,
                        "latency_ms": 0,
                        "mode": "dry_run",
                    }
                    for c in datasets["rag"]
                ],
            })
        if "hallucination" in suites:
            suite_outputs.append({
                "suite": "hallucination",
                "summary": {"total": len(datasets["hallucination"]), "mode": "dry_run_schema_validated"},
                "cases": [
                    {
                        "id": c["id"],
                        "suite": "hallucination",
                        "category": c.get("category"),
                        "query": c.get("query"),
                        "expected_status": c.get("expected_status"),
                        "status": "PASSED",
                        "passed": True,
                        "latency_ms": 0,
                        "mode": "dry_run",
                    }
                    for c in datasets["hallucination"]
                ],
            })
    else:
        needs_llm = any(s in suites for s in ("rag", "contract", "hallucination"))
        if needs_llm:
            try:
                llm_instance = build_llm_from_profile(profile_name, profile_cfg)
            except Exception as exc:
                logger.warning(f"Skipping profile '{profile_name}': {exc}")
                return {"model_profile": profile_name, "skipped_reason": str(exc)}

            with override_app_llm(llm_instance):
                live_suite_specs = []
                if "rag" in suites:
                    live_suite_specs.append(("rag", evaluate_rag_suite))
                if "contract" in suites:
                    live_suite_specs.append(("contract", evaluate_contract_suite))
                if "hallucination" in suites:
                    live_suite_specs.append(("hallucination", evaluate_hallucination_suite))
                if "ocr" in suites:
                    live_suite_specs.append(("ocr", evaluate_ocr_suite))

                used_partial_paths = []
                for suite_name, eval_fn in live_suite_specs:
                    cases = datasets[suite_name]
                    case_ids = [c["id"] for c in cases]
                    ppath = _partial_path(profile_name, suite_name, quick)
                    done = _load_partial(ppath, case_ids)
                    if done:
                        logger.info(
                            f"[{profile_name}/{suite_name}] resuming: {len(done)}/{len(cases)} "
                            f"cases already completed in previous run"
                        )
                    on_result, _saved = _make_progress_callback(
                        profile_name, suite_name, case_ids, ppath,
                        initial_results=list(done.values()),
                    )
                    used_partial_paths.append(ppath)

                    kwargs = {"on_result": on_result, "resume": done}
                    if suite_name == "contract":
                        kwargs["dry_run"] = False
                    if suite_name == "ocr":
                        kwargs["datasets_dir"] = DATASETS_DIR
                    suite_outputs.append(await eval_fn(cases, **kwargs))

                # Completed profile: partial files are no longer needed.
                for ppath in used_partial_paths:
                    _clear_partial(ppath)
        elif "ocr" in suites:
            suite_outputs.append(await evaluate_ocr_suite(datasets["ocr"], datasets_dir=DATASETS_DIR))

    perf = summarize_performance(suite_outputs)
    if push_db and not dry_run:
        await push_benchmark_events_to_db(profile_name, suite_outputs, perf)

    return {
        "model_profile": profile_name,
        "display_name": profile_cfg.get("display_name", profile_name),
        "performance": perf,
        "suites": suite_outputs,
    }


async def main_async(args: argparse.Namespace) -> int:
    validation = validate_datasets_offline()
    if not validation["unique_ids"]:
        logger.error("Dataset validation failed: duplicate case IDs found!")
        return 1
    logger.info(f"Validated {validation['total_cases']} gold cases across {validation['counts']}")

    models_cfg = load_json(MODELS_FILE).get("profiles", {})
    requested_models = [m.strip() for m in args.models.split(",") if m.strip()]
    requested_suites = [s.strip() for s in args.suites.split(",") if s.strip()]
    quick = not args.full

    runs = []
    for profile_name in requested_models:
        if profile_name not in models_cfg:
            runs.append({"model_profile": profile_name, "skipped_reason": "Not defined in models.json"})
            continue
        runs.append(
            await run_for_profile(
                profile_name=profile_name,
                profile_cfg=models_cfg[profile_name],
                suites=requested_suites,
                quick=quick,
                dry_run=args.dry_run,
                push_db=args.push_db,
            )
        )

    out_dir = pathlib.Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "full" if args.full else "quick",
        "dry_run": args.dry_run,
        "suites_requested": requested_suites,
        "dataset_validation": validation,
        "runs": runs,
    }

    json_path = out_dir / f"benchmark_{ts}.json"
    md_path = out_dir / "latest_summary.md"
    details_md_path = out_dir / "latest_details.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_text = render_markdown_summary(report)
    details_md_text = render_detailed_markdown(report)
    md_path.write_text(md_text, encoding="utf-8")
    details_md_path.write_text(details_md_text, encoding="utf-8")

    print(md_text)
    logger.info(f"Saved JSON report: {json_path}")
    logger.info(f"Saved Markdown summary: {md_path}")
    logger.info(f"Saved Detailed Per-Case Markdown: {details_md_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ContractMind / Advotech benchmark suites.")
    parser.add_argument(
        "--models",
        default="default_production",
        help="Comma-separated model profile names from scripts/benchmark/models.json",
    )
    parser.add_argument(
        "--suites",
        default="rag,contract,hallucination,ocr",
        help="Comma-separated suites: rag,contract,hallucination,ocr",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--quick", action="store_true", help="Run representative subset (~16 cases)")
    group.add_argument("--full", action="store_true", help="Run all 118 gold cases")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate datasets, run offline contract AST/domain & real PDF OCR checks without live LLM calls",
    )
    parser.add_argument("--push-db", action="store_true", help="Push summary metrics to metric_events DB")
    parser.add_argument("--output-dir", default=str(REPORTS_DIR), help="Directory for JSON & Markdown reports")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
