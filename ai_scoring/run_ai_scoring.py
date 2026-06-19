from __future__ import annotations

import argparse
import json

from ai_scoring.service import refresh_sector_scores, score_latest_candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DeepSeek AI sector and candidate scoring")
    parser.add_argument("--skip-sector", action="store_true", help="Do not refresh sector scores")
    parser.add_argument("--skip-candidates", action="store_true", help="Do not score latest candidates")
    parser.add_argument("--strategy-id", default=None, help="Candidate strategy id, e.g. b1 or volume_new_high")
    parser.add_argument("--max-candidates", type=int, default=None, help="Maximum candidates sent to DeepSeek")
    parser.add_argument("--extra-context", default=None, help="Extra sector-scoring context")
    args = parser.parse_args()

    result = {}
    if not args.skip_sector:
        result["sector_scores"] = refresh_sector_scores(extra_context=args.extra_context)
    if not args.skip_candidates:
        result["candidate_scores"] = score_latest_candidates(
            strategy_id=args.strategy_id,
            max_candidates=args.max_candidates,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
