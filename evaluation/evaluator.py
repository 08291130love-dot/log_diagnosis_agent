from repositories.log_repository import LogRepository


def evaluate_cases(repository: LogRepository, cases: list[dict]) -> dict:
    results = []
    for case in cases:
        search = repository.search(
            keyword=case["keyword"],
            level=case.get("level", ""),
            limit=10,
        )
        records = search["records"]
        exception_hit = any(
            item.get("exception_type") == case["expected_exception"] for item in records
        )
        trace_hit = any(item.get("trace_id") == case["expected_trace_id"] for item in records)
        context_hit = False
        if records:
            context = repository.context(records[0]["id"])
            context_hit = any(
                item.get("trace_id") == case["expected_trace_id"]
                for item in context.get("records", [])
            )
        passed = exception_hit and trace_hit and context_hit
        results.append(
            {
                "case_id": case["id"],
                "name": case["name"],
                "passed": passed,
                "matches": search["total"],
                "exception_hit": exception_hit,
                "trace_hit": trace_hit,
                "context_hit": context_hit,
            }
        )
    passed_count = sum(item["passed"] for item in results)
    return {
        "total": len(results),
        "passed": passed_count,
        "pass_rate": passed_count / len(results) if results else 0.0,
        "results": results,
    }
