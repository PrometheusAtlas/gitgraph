#!/usr/bin/env python3
"""gitgraph web API — serves the graph, node details, search, and queries."""
import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from db import get_driver
from queries import QUERIES

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(title="gitgraph")

class NoCache(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

app.add_middleware(NoCache)

WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|REMOVE|SET|DROP|ALTER|RENAME|LOAD|CALL\s+db\.|"
    r"CREATE\s+USER|CREATE\s+DATABASE|CREATE\s+ROLE|GRANT|REVOKE|DENY)\b",
    re.IGNORECASE,
)

def driver():
    return get_driver()

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

@app.get("/api/repos")
def repos():
    with driver().session() as s:
        rows = s.run("""
            MATCH (r:Repo)-[:CONTAINS]->(c:Commit)
            OPTIONAL MATCH (b:Branch {remote: 'local'})-[:POINTS_TO]->()
            WHERE b IS NULL OR b.repo = r.name
            WITH r, count(DISTINCT c) AS commits, collect(DISTINCT b.name) AS branches
            RETURN r.name AS name, r.path AS path, commits, branches
            ORDER BY commits DESC
        """).data()
    return rows

@app.get("/api/graph")
def graph(repo: str, branch: str = "", limit: int = 2500):
    with driver().session() as s:
        # the SHAPE of the repo: every merge, every branch/tag tip, and a
        # stride-sampled trunk — not just the newest slice
        all_commits = s.run("""
            MATCH (r:Repo {name: $repo})-[:CONTAINS]->(c:Commit)
            RETURN c.hash AS hash, c.merged AS merged
        """, repo=repo).data()
        tip_hashes = set()
        for t in s.run("""
            MATCH (b:Branch {repo: $repo})-[:POINTS_TO]->(tip)
            MATCH (t:Tag {repo: $repo})-[:POINTS_TO]->(ttip)
            RETURN tip.hash AS h UNION
            MATCH (t:Tag {repo: $repo})-[:POINTS_TO]->(ttip)
            RETURN ttip.hash AS h
        """, repo=repo).data():
            tip_hashes.add(t["h"])

        # order all commits oldest -> newest for even stride sampling
        chrono = s.run("""
            MATCH (r:Repo {name: $repo})-[:CONTAINS]->(c:Commit)
            RETURN c.hash AS hash, c.merged AS merged
            ORDER BY c.authored_at ASC
        """, repo=repo).data()

        merges = [c for c in chrono if c["merged"]]
        trunk = [c for c in chrono if not c["merged"]]
        sample = set(tip_hashes)
        # all tips + a balanced mix: ~45% merges, rest stride-sampled trunk
        remaining = limit - len(sample)
        merge_cap = max(0, int(remaining * 0.45))
        merge_stride = max(1, len(merges) // merge_cap) if merge_cap else 10 ** 9
        for i in range(0, len(merges), merge_stride):
            if len(sample) >= limit:
                break
            sample.add(merges[i]["hash"])
        remaining = limit - len(sample)
        trunk_stride = max(1, len(trunk) // remaining) if remaining > 0 else 10 ** 9
        for i in range(0, len(trunk), trunk_stride):
            if len(sample) >= limit:
                break
            sample.add(trunk[i]["hash"])

        commits = s.run("""
            MATCH (c:Commit)
            WHERE c.hash IN $hashes
            RETURN c.hash AS hash, c.msg AS msg, c.author_name AS author,
                   c.authored_at AS date, c.merged AS merged,
                   c.additions AS add, c.deletions AS del,
                   c.files_changed AS files
            ORDER BY c.authored_at DESC
        """, hashes=list(sample)).data()
        hash_set = {c["hash"] for c in commits}

        # branch membership via a plain Python BFS over the parent edges
        edges = s.run("""
            MATCH (r:Repo {name: $repo})-[:CONTAINS]->(c:Commit)-[:PARENT]->(p:Commit)
            RETURN c.hash AS c, p.hash AS p
        """, repo=repo).data()
        child = {}
        for e in edges:
            child.setdefault(e["c"], []).append(e["p"])
        tips = s.run("""
            MATCH (b:Branch {repo: $repo, remote: 'local'})-[:POINTS_TO]->(tip)
            RETURN b.name AS name, tip.hash AS tip
        """, repo=repo).data()

        order = ["main", "master", "develop"]
        tips.sort(key=lambda b: (order.index(b["name"]) if b["name"] in order else 99, b["name"]))
        # each commit joins the branch whose tip is CLOSEST to it — historical
        # branches keep their own lanes instead of everything collapsing onto main
        best_branch = {}
        best_depth = {}
        for b in tips:
            stack, seen = [(b["tip"], 0)], set()
            while stack:
                h, depth = stack.pop()
                if h in seen:
                    continue
                seen.add(h)
                if h in hash_set and (h not in best_depth or depth < best_depth[h]):
                    best_depth[h] = depth
                    best_branch[h] = b["name"]
                for p in child.get(h, []):
                    if p not in seen:
                        stack.append((p, depth + 1))
        branch_of = best_branch

        # topological depth over the FULL graph via Kahn's algorithm
        # (O(V+E) — a naive relaxation re-pushes merges and takes minutes)
        from collections import deque as _deque
        nodes = set(child.keys())
        for ps in child.values():
            nodes.update(ps)
        children_of = {}
        for c, ps in child.items():
            for p in ps:
                children_of.setdefault(p, []).append(c)
        indeg = {n: len(child.get(n, [])) for n in nodes}
        queue = _deque(n for n in nodes if indeg[n] == 0)
        level = {n: 0 for n in nodes}
        while queue:
            n = queue.popleft()
            for c in children_of.get(n, []):
                if level[c] < level[n] + 1:
                    level[c] = level[n] + 1
                indeg[c] -= 1
                if indeg[c] == 0:
                    queue.append(c)
        max_level = max(level.values()) if level else 0

        # optional branch scope: the ancestry set of one branch
        scope = None
        if branch:
            scope = set()
            stack = [next((b["tip"] for b in tips if b["name"] == branch), "")]
            seen = set()
            while stack:
                h = stack.pop()
                if not h or h in seen:
                    continue
                seen.add(h)
                scope.add(h)
                for p in child.get(h, []):
                    if p not in seen:
                        stack.append(p)

        tags = {}
        for t in s.run("""
            MATCH (t:Tag {repo: $repo})-[:POINTS_TO]->(c:Commit)
            RETURN t.name AS name, c.hash AS hash
        """, repo=repo).data():
            tags.setdefault(t["hash"], []).append(t["name"])

    nodes = []
    for c in commits:
        if scope is not None and c["hash"] not in scope:
            continue
        nodes.append({
            "id": c["hash"],
            "msg": c["msg"],
            "author": c["author"],
            "date": c["date"],
            "merged": bool(c["merged"]),
            "add": c["add"], "del": c["del"], "files": c["files"],
            "branch": branch_of.get(c["hash"], ""),
            "tags": tags.get(c["hash"], []),
            "level": level.get(c["hash"], 0),
            "tip": best_depth.get(c["hash"], 999) == 0,
        })
    in_set = {n["id"] for n in nodes}
    with driver().session() as s:
        edges = s.run("""
            MATCH (c:Commit)-[:PARENT]->(p:Commit)
            WHERE c.hash IN $hashes AND p.hash IN $hashes
            RETURN c.hash AS source, p.hash AS target
        """, hashes=list(in_set)).data() if in_set else []
    return {"nodes": nodes, "edges": edges, "maxLevel": max_level}

@app.get("/api/node/{hash}")
def node(hash: str):
    with driver().session() as s:
        row = s.run("""
            MATCH (c:Commit {hash: $hash})
            OPTIONAL MATCH (b:Branch)-[:POINTS_TO]->(c)
            OPTIONAL MATCH (t:Tag)-[:POINTS_TO]->(c)
            OPTIONAL MATCH (a:Author)<-[:AUTHORED_BY]-(c)
            OPTIONAL MATCH (c)-[:PARENT]->(p:Commit)
            OPTIONAL MATCH (child:Commit)-[:PARENT]->(c)
            OPTIONAL MATCH (c)-[:MODIFIED]->(f:File)
            RETURN c.hash AS hash, c.msg AS msg, c.body AS body,
                   c.author_name AS author_name, c.author_email AS author_email,
                   c.authored_at AS authored_at, c.committer_name AS committer_name,
                   c.committed_at AS committed_at, c.additions AS add, c.deletions AS del,
                   c.files_changed AS files_changed, c.merged AS merged,
                   collect(DISTINCT b.name) AS branches,
                   collect(DISTINCT t.name) AS tags,
                   collect(DISTINCT p.hash) AS parents,
                   collect(DISTINCT child.hash) AS children,
                   [f IN collect(DISTINCT f.path) WHERE f IS NOT NULL] AS files
        """, hash=hash).single()
    if not row:
        raise HTTPException(404, "commit not found")
    return dict(row)

@app.get("/api/search")
def search(q: str, limit: int = 50):
    with driver().session() as s:
        rows = s.run("""
            MATCH (c:Commit)
            WHERE c.hash STARTS WITH $q OR toLower(c.msg) CONTAINS toLower($q)
               OR toLower(c.author_name) CONTAINS toLower($q)
            RETURN c.hash AS hash, c.msg AS msg, c.author_name AS author,
                   c.authored_at AS date
            ORDER BY c.authored_at DESC
            LIMIT $limit
        """, q=q, limit=limit).data()
    return rows

@app.get("/api/queries")
def queries():
    return [{"name": n, "description": q["description"],
             "needs_repo": q.get("needs_repo", False),
             "needs_branch": q.get("needs_branch", False)}
            for n, q in QUERIES.items()]

@app.post("/api/query")
def run_query(payload: dict):
    name = payload.get("name")
    cypher = payload.get("cypher")
    repo = payload.get("repo")
    branch = payload.get("branch")
    if name:
        if name not in QUERIES:
            raise HTTPException(404, f"unknown query '{name}'")
        q = QUERIES[name]
        if q.get("needs_repo") and not repo:
            raise HTTPException(400, "this query needs a repo")
        if q.get("needs_branch") and not branch:
            raise HTTPException(400, "this query needs a branch")
        cypher, params = q["cypher"], {"repo": repo, "branch": branch}
    elif cypher:
        if WRITE_RE.search(cypher):
            raise HTTPException(400, "read-only queries only")
        params = {"repo": repo, "branch": branch}
    else:
        raise HTTPException(400, "provide 'name' or 'cypher'")
    try:
        with driver().session() as s:
            records = s.run(cypher, **{k: v for k, v in params.items() if v}).data()
    except Exception as e:
        raise HTTPException(400, str(e))
    return JSONResponse(records)
