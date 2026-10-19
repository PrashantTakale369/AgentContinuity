"""User-facing global setup, session browsing, and continuation commands."""

import argparse
import json
import sys

from ..services.catalog import list_sessions
from ..services.resume import resume_session
from ..services.setup import setup_global
from ..workspace.global_config import load_global


def execute_global(args: argparse.Namespace) -> int | None:
    if args.command == "init":
        roots = {name[:-5]: value for name, value in vars(args).items()
                 if name.endswith("_root") and value is not None}
        result = setup_global(roots)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(result["message"])
            for name, record in result["agents"].items():
                print(f"  {name}: {record['status']}" + (f" — {record['invoke']}" if "invoke" in record else ""))
            print("Storage: " + result["storage"])
        failed = any("conflict" in r["status"] or "failed" in r["status"] for r in result["agents"].values())
        return 1 if failed else 0
    if args.command == "sessions":
        result = list_sessions(source=args.source, query=args.query, limit=None if args.all else args.limit)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Local sessions (all projects):")
            for item in result["sessions"]:
                # JSON escaping prevents transcript titles/paths from injecting terminal controls.
                print(f"  {item['number']}. {json.dumps(item['title'], ensure_ascii=False)}  [{item['agent']}]  "
                      f"{json.dumps(item['workspace'], ensure_ascii=False)}  {item['last_activity']}")
            print(f"Listing: {result['listing_id']} ({len(result['sessions'])}/{result['total']} shown)")
            if result["omitted"]:
                print("Use --all or --query TEXT to see more sessions.")
            print("Continue: agenthandoff resume NUMBER --listing " + result["listing_id"] + " --target AGENT")
            for warning in result["warnings"]:
                print(warning, file=sys.stderr)
        return 0
    if args.command == "resume":
        result = resume_session(args.session, args.target, args.cwd, source=args.source,
                                listing=args.listing, print_only=args.print_only, dry_run=args.dry_run,
                                allow_partial=args.allow_partial, budget_bytes=args.budget_bytes, mode=args.mode)
        if isinstance(result, dict):
            print(json.dumps(result, indent=2))
            return 0
        return result
    if args.command == "doctor":
        from ..services.health import diagnose
        report = diagnose(args.cwd)
        directory, config = load_global(required=False)
        report["global_setup"] = {"storage": str(directory), "initialized": (directory / "config.json").exists(),
                                  "integrations": config.get("integrations", {})}
        print(json.dumps(report, indent=2))
        return 0
    return None
