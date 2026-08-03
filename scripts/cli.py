#!/usr/bin/env python3
"""gitgraph CLI — import repos, query the graph, serve the web app.

  gitgraph import <path> [--with-files]
  gitgraph stats
  gitgraph query "<cypher>" | --named <name> [--repo X] [--branch Y] [--json]
  gitgraph queries            list named queries
  gitgraph serve              start the web UI (http://localhost:8000)
  gitgraph wipe               empty the graph
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # make `app` importable
from db import get_driver, wait_ready
from queries import QUERIES

def fmt_table(records):
    if not records:
        return "(no rows)"
    headers = list(records[0].keys())
    rows = [[str(r.get(h, "")) for h in headers] for r in records]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = []
    lines.append("  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    lines.append("  " + "  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        lines.append("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)

def cmd_import(args):
    from importer import import_repo
    import_repo(args.path, args.with_files)

def cmd_stats(_):
    if not wait_ready():
        print("error: Neo4j not reachable (docker compose up -d)", file=sys.stderr)
        sys.exit(1)
    d = get_driver()
    with d.session() as s:
        rows = s.run("""
            MATCH (r:Repo)-[:CONTAINS]->(c:Commit)
            RETURN r.name AS repo,
                   count(c) AS commits,
                   count(DISTINCT c.author_email) AS authors
            ORDER BY commits DESC
        """).data()
        rows += s.run("""
            MATCH (c:Commit)
            RETURN 'TOTAL' AS repo, count(c) AS commits,
                   count(DISTINCT c.author_email) AS authors
        """).data()
    d.close()
    print(fmt_table(rows))

def cmd_query(args):
    if not wait_ready():
        print("error: Neo4j not reachable", file=sys.stderr)
        sys.exit(1)
    if args.named:
        if args.named not in QUERIES:
            print(f"unknown query '{args.named}'. Try: gitgraph queries", file=sys.stderr)
            sys.exit(1)
        q = QUERIES[args.named]
        if q.get("needs_repo") and not args.repo:
            print(f"this query needs --repo", file=sys.stderr)
            sys.exit(1)
        if q.get("needs_branch") and not args.branch:
            print(f"this query needs --branch", file=sys.stderr)
            sys.exit(1)
        cypher, params = q["cypher"], {"repo": args.repo, "branch": args.branch}
    else:
        cypher, params = args.cypher, {"repo": args.repo, "branch": args.branch}
    d = get_driver()
    try:
        with d.session() as s:
            records = s.run(cypher, **{k: v for k, v in params.items() if v}).data()
    except Exception as e:
        print(f"query failed: {e}", file=sys.stderr)
        sys.exit(1)
    d.close()
    if args.json:
        print(json.dumps(records, indent=2))
    else:
        print(fmt_table(records))

def cmd_queries(_):
    for name, q in QUERIES.items():
        need = []
        if q.get("needs_repo"):
            need.append("--repo")
        if q.get("needs_branch"):
            need.append("--branch")
        print(f"  {name:<16} {' '.join(need):<18} {q['description']}")

def cmd_wipe(_):
    d = get_driver()
    with d.session() as s:
        n = s.run("MATCH (n) DETACH DELETE n RETURN count(n)").single()[0]
    d.close()
    print(f"wiped {n} nodes")

def cmd_serve(args):
    import os
    import uvicorn
    os.chdir(Path(__file__).resolve().parent.parent)  # make `app` importable
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)

def main():
    ap = argparse.ArgumentParser(prog="gitgraph")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import", help="import a repo")
    p.add_argument("path")
    p.add_argument("--with-files", action="store_true")
    p.set_defaults(fn=cmd_import)
    sub.add_parser("stats", help="graph summary").set_defaults(fn=cmd_stats)
    p = sub.add_parser("query", help="run Cypher or a named query")
    p.add_argument("cypher", nargs="?")
    p.add_argument("--named")
    p.add_argument("--repo")
    p.add_argument("--branch")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_query)
    sub.add_parser("queries", help="list named queries").set_defaults(fn=cmd_queries)
    sub.add_parser("wipe", help="empty the graph").set_defaults(fn=cmd_wipe)
    p = sub.add_parser("serve", help="start the web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)
    args = ap.parse_args()
    args.fn(args)

if __name__ == "__main__":
    main()
