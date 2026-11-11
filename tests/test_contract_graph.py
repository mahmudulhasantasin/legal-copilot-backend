import pytest

import app.agents.contract_graph as cg


async def test_analyze_contract_risk_sorts_clauses_without_name_error(monkeypatch):
    async def fake_retrieve_statutes_node(_state):
        return {"retrieved_statutes": []}

    async def fake_analyze_risk_node(_state):
        return {
            "analysis": {
                "overall_risk_score": "HIGH",
                "detected_risks": [
                    {
                        "source_clause_number": "10",
                        "clause_title": "Penalty Clause",
                        "severity": "HIGH",
                        "violated_law": "Commercial Standard",
                        "flagged_text": "10x penalty",
                        "explanation": "Excessive penalty",
                    },
                    {
                        "source_clause_number": "2",
                        "clause_title": "Payment Terms",
                        "severity": "MODERATE",
                        "violated_law": "Commercial Standard",
                        "flagged_text": "Net 90",
                        "explanation": "Delayed payment",
                    },
                ],
            }
        }

    async def fake_synthesize_summary_node(**_kwargs):
        return "Synthesized executive summary for contract audit."

    monkeypatch.setattr(cg, "retrieve_statutes_node", fake_retrieve_statutes_node)
    monkeypatch.setattr(cg, "analyze_risk_node", fake_analyze_risk_node)
    monkeypatch.setattr(cg, "synthesize_summary_node", fake_synthesize_summary_node)

    result = await cg.analyze_contract_risk_async(
        contract_text="2. Payment Terms: Net 90.\n\n10. Penalty Clause: 10x penalty.",
        playbook_rules=[],
        audited_for="CLIENT",
    )

    assert result["memo_summary"] == "Synthesized executive summary for contract audit."
    clause_nums = [r["source_clause_number"] for r in result["detected_risks"]]
    assert clause_nums == ["2", "10"]
