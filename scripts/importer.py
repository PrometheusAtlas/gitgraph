#!/usr/bin/env python3
"""Import a git repository into Neo4j as a commit graph.

Usage:  python3 scripts/import.py <repo-path> [--with-files]

The importer is idempotent (MERGE everywhere), so re-running after new
commits simply adds what's missing. Multi-repo: run for each repo path;
commits are shared by hash, branches/tags are scoped per repo.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import ensure_schema, get_driver, load_env, wait_ready

RS = "\x1e"   # record separator
US = "\x1f"   # field separator

FMT_A = f"{RS}%H{US}%P{US}%an{US}%ae{US}%aI{US}%cn{US}%ce{US}%cI{US}%s{RS}"
FMT_B = f"{RS}%H{US}%b{RS}"

def run_git(path, *args):
    p = subprocess.run(["git", "-C", str(path), *args],
                       capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr[:300]}")
    return p.stdout

def parse_commits(text):
    """Parse `git log --numstat` output into commit dicts (no bodies).

    git prints each commit's fields, then the diffstat AFTER the record
    separator, so a field chunk is always followed by a file chunk.
    """
    commits = []
    pending = None
    for chunk in text.split(RS):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        lines = chunk.split("\n")
        fields = lines[0].split(US)
        if len(fields) >= 9 and fields[0]:
            if pending:
                commits.append(pending)
            h, parents, an, ae, aI, cn, ce, cI, msg = fields[:9]
            pending = {
                "hash": h,
                "parents": [p for p in parents.split() if p],
                "msg": msg,
                "author_name": an,
                "author_email": ae,
                "authored_at": aI,
                "committer_name": cn,
                "committer_email": ce,
                "committed_at": cI,
                "additions": 0,
                "deletions": 0,
                "files_changed": 0,
                "files": [],
                "merged": len(parents.split()) > 1,
            }
            for fl in lines[1:]:
                _consume_file_line(pending, fl)
        elif pending is not None:
            for fl in lines:
                _consume_file_line(pending, fl)
    if pending:
        commits.append(pending)
    return commits

def _consume_file_line(commit, fl):
    fl = fl.strip()
    if not fl:
        return
    parts = fl.split("\t")
    if len(parts) == 3:
        path = parts[2]
        if " => " in path:
            path = path.split(" => ")[-1]
        commit["files"].append(path)
        commit["files_changed"] += 1
        if parts[0].isdigit():
            commit["additions"] += int(parts[0])
        if parts[1].isdigit():
            commit["deletions"] += int(parts[1])
    elif len(parts) == 1:
        path = parts[0]
        if " => " in path:
            path = path.split(" => ")[-1]
        commit["files"].append(path)
        commit["files_changed"] += 1

def parse_bodies(text):
    """Parse `git log --format=%H\x1f%b` output into {hash: body}.

    Body may span many lines; the whole chunk after the hash belongs to it.
    """
    bodies = {}
    for record in text.split(RS):
        record = record.strip("\n")
        if not record:
            continue
        lines = record.split("\n")
        head, _, first = lines[0].partition(US)
        if not head:
            continue
        bodies[head] = "\n".join([first] + lines[1:]).strip()
    return bodies

def parse_refs(text):
    """for-each-ref output -> (kind, name, target_hash, remote, is_head)"""
    refs = []
    for line in text.splitlines():
        if not line:
            continue
        target, _, ref = line.partition(" ")
        ref = ref.strip()
        if ref.startswith("refs/heads/"):
            refs.append(("branch", ref[len("refs/heads/"):], target, None))
        elif ref.startswith("refs/remotes/"):
            parts = ref[len("refs/remotes/"):].split("/", 1)
            remote, name = parts if len(parts) == 2 else (parts[0], parts[0])
            refs.append(("branch", name, target, remote))
        elif ref.startswith("refs/tags/"):
            refs.append(("tag", ref[len("refs/tags/"):], target, None))
    return refs

def batched(rows, n=500):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]

def import_repo(path, with_files=False, no_stats=False):
    path = Path(path).expanduser().resolve()
    if not (path / ".git").exists():
        raise RuntimeError(f"not a git repository: {path}")

    print(f"== importing {path} ==")
    if no_stats:
        text = run_git(path, "log", "--all", f"--format={FMT_A}", "--name-only")
    else:
        text = run_git(path, "log", "--all", f"--format={FMT_A}", "--numstat")
    commits = parse_commits(text)
    if not commits:
        print("  no commits found")
        return 0

    bodies = parse_bodies(run_git(path, "log", "--all", f"--format={FMT_B}"))
    for c in commits:
        c["body"] = bodies.get(c["hash"], "")

    refs = parse_refs(run_git(path, "for-each-ref", "--format=%(objectname) %(refname)"))
    head = run_git(path, "symbolic-ref", "--short", "HEAD").strip()
    repo_name = path.name

    if not wait_ready():
        raise RuntimeError("Neo4j not reachable — is the container running? (docker compose up -d)")
    driver = get_driver()
    ensure_schema(driver)

    t0 = time.time()
    with driver.session() as s:
        s.run("MERGE (r:Repo {name: $name}) SET r.path = $path, r.last_sync = $ts",
              name=repo_name, path=str(path), ts=time.strftime("%Y-%m-%dT%H:%M:%S"))

        # commits + authors + parent edges
        for batch in batched(commits):
            rows = [{
                "hash": c["hash"], "msg": c["msg"], "body": c["body"],
                "author_name": c["author_name"], "author_email": c["author_email"],
                "authored_at": c["authored_at"],
                "committer_name": c["committer_name"], "committer_email": c["committer_email"],
                "committed_at": c["committed_at"],
                "additions": c["additions"], "deletions": c["deletions"],
                "files_changed": c["files_changed"], "merged": c["merged"],
                "parents": c["parents"], "repo": repo_name,
            } for c in batch]
            s.run("""
                UNWIND $rows AS r
                MERGE (c:Commit {hash: r.hash})
                SET c += {msg: r.msg, body: r.body, author_name: r.author_name,
                          author_email: r.author_email, authored_at: r.authored_at,
                          committer_name: r.committer_name, committer_email: r.committer_email,
                          committed_at: r.committed_at, additions: r.additions,
                          deletions: r.deletions, files_changed: r.files_changed,
                          merged: r.merged}
                MERGE (a:Author {email: r.author_email})
                SET a.name = coalesce(a.name, r.author_name)
                MERGE (c)-[:AUTHORED_BY]->(a)
                MERGE (repo:Repo {name: r.repo})
                MERGE (repo)-[:CONTAINS]->(c)
                WITH c, r
                UNWIND r.parents AS p
                MERGE (pc:Commit {hash: p})
                MERGE (c)-[:PARENT]->(pc)
                """, rows=rows)

        # refs
        rows = []
        for kind, name, target, remote in refs:
            if kind == "branch":
                rows.append({"name": name, "target": target, "remote": remote or "local",
                             "is_head": name == head and not remote, "repo": repo_name})
        if rows:
            for batch in batched(rows):
                s.run("""
                    UNWIND $rows AS r
                    MERGE (b:Branch {name: r.name, repo: r.repo, remote: r.remote})
                    SET b.is_head = r.is_head
                    WITH b, r
                    MATCH (c:Commit {hash: r.target})
                    MERGE (b)-[:POINTS_TO]->(c)
                    """, rows=batch)
        rows = []
        for kind, name, target, remote in refs:
            if kind == "tag":
                rows.append({"name": name, "target": target, "repo": repo_name})
        if rows:
            for batch in batched(rows):
                s.run("""
                    UNWIND $rows AS r
                    MERGE (t:Tag {name: r.name, repo: r.repo})
                    WITH t, r
                    MATCH (c:Commit {hash: r.target})
                    MERGE (t)-[:POINTS_TO]->(c)
                    """, rows=batch)

        # file edges
        if with_files:
            rows = [{"hash": c["hash"], "path": f} for c in commits for f in c["files"]]
            for batch in batched(rows):
                s.run("""
                    UNWIND $rows AS r
                    MATCH (c:Commit {hash: r.hash})
                    MERGE (f:File {path: r.path})
                    MERGE (c)-[:MODIFIED]->(f)
                    """, rows=batch)

    driver.close()
    dt = time.time() - t0
    print(f"  imported {len(commits)} commits, {len(refs)} refs in {dt:.1f}s")
    return len(commits)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--with-files", action="store_true")
    ap.add_argument("--no-stats", action="store_true",
                    help="skip diff stats (fast on large / blobless clones)")
    args = ap.parse_args()
    try:
        import_repo(args.path, args.with_files, args.no_stats)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
