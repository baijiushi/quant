---
name: benben-super-boom
description: Evidence-constrained A-share sector and stock research based on the public "super-prosperity value speculation" framework associated with Bilibili creator Benben de Jiucai. Use when evaluating phenomenon-level events, high-prosperity sectors, industry-chain beneficiaries, candidate stocks, scoring dimensions, thesis invalidation, or when updating oversell's local methodology knowledge base. Do not use it to promise returns or fabricate inaccessible paid content.
---

# Benben Super Boom

Use this skill to turn news, industry research, user materials, and candidate lists into traceable A-share research. Separate direct evidence from inference and let deterministic code, not an LLM, perform the final score arithmetic.

## Required References

Read [references/methodology.md](references/methodology.md) before evaluating a sector or stock. Read [references/evidence.yaml](references/evidence.yaml) when attribution, formula provenance, the creator's holdings, or current evidence boundaries matter.

## Research Workflow

1. Establish the as-of date and list every supplied source. Do not imply access to news or paid posts that was not actually available.
2. Label evidence as `direct`, `user_primary`, `secondary`, or `catalog`. Treat a video title as a discovery lead only.
3. Build the transmission chain: phenomenon-level event -> industry supply/demand -> highest-elasticity chain segment -> company exposure.
4. Test commercialization, duration, market size, business purity, competitive position, price position, recognition, and risks.
5. Record evidence gaps and falsifiable invalidation triggers. Mark every behavioral conclusion drawn from holdings or titles as an inference.
6. Score the five positive dimensions and risk deduction from 0 to 100. Use 0.8, 1.0, or 1.2 for the liquidity coefficient.
7. Recompute the final score with code or explicit arithmetic: `((positive_sum - risk * 0.2) * liquidity) / 5`. If industry prosperity is zero or a veto risk exists, the decision remains avoid regardless of the arithmetic.

## Output Contract

For sector research, return the event type, event strength, commercialization stage, supply/demand impact, time horizon, score, confidence, evidence grade, `source_refs`, risks, evidence gaps, and invalidation triggers.

For candidate research, return the five dimension scores, risk deduction, liquidity coefficient, deterministic final score, transmission thesis, `source_refs`, missing data, and invalidation triggers. Use `重点研究`, `观察`, and `回避` as research labels, not automatic trade instructions.

## Oversell Integration

The project reads these same reference files from `skills/benben-super-boom/references/`. Public Bilibili collection catalogs are refreshed into SQLite and explicitly stored as weak catalog evidence.

Use `python -m ai_scoring.run_ai_scoring --skip-sector --skip-candidates --refresh-knowledge` to force a public knowledge refresh. The local API also provides `GET /api/knowledge/benben/status`, `GET /api/knowledge/benben/documents`, and `POST /api/knowledge/benben/refresh`.

Never read or expose `.env.local`, browser cookies, API keys, or session data. Do not bypass Bilibili membership tiers.
