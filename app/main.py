#!/usr/bin/env python3
"""gitgraph web API — serves the graph, node details, search, and queries."""
import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from db import get_driver
from queries import QUERIES

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(title="gitgraph")

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
            OPTIONAL MATCH (b:Branch {remote: 'local'})-[:POINTS_TO]->(:Commit)
            WITH r, count(DISTINCT c) AS commits, collect(DISTINCT b.name) AS branches
            RETURN r.name AS name, r.path AS path, commits, branches
            ORDER BY commits DESC
        """).data()
    return rows

@app.get("/api/graph")
def graph(repo: str, branch: str = "", limit: int = 2000):
    with driver().session() as s:
        scope = None
        if branch:
            scope = set(s.run("""
                MATCH (b:Branch {repo: $repo, name: $branch, remote: 'local'})-[:POINTS_TO]->(tip)
                MATCH (tip)-[:PARENT*0..]->(c:Commit)
                RETURN collect(c.hash) AS hashes
            """, repo=repo, branch=branch).single()["hashes"])
        params = {"repo": repo, "limit": limit}
        extra = "WHERE c.hash IN $scope" if scope else ""
        if scope:
            params["scope"] = list(scope)
        commits = s.run(f"""
            MATCH (r:Repo {{name: $repo}})-[:CONTAINS]->(c:Commit)
            {extra}
            RETURN c.hash AS hash, c.msg AS msg, c.author_name AS author,
                   c.authored_at AS date, c.merged AS merged,
                   c.additions AS add, c.deletions AS del,
                   c.files_changed AS files
            ORDER BY c.authored_at DESC
            LIMIT $limit
        """, **params).data()
        hashes = [c["hash"] for c in commits]
        edges = s.run("""
            MATCH (c:Commit)-[:PARENT]->(p:Commit)
            WHERE c.hash IN $hashes AND p.hash IN $hashes
            RETURN c.hash AS source, p.hash AS target
        """, hashes=hashes).data()
        branch_sets = {}
        for b in s.run("""
            MATCH (b:Branch {repo: $repo, remote: 'local'})-[:POINTS_TO]->(tip)
            RETURN b.name AS name, tip.hash AS tip
        """, repo=repo).data():
            anc = s.run("""
                MATCH (tip:Commit {hash: $tip})-[:PARENT*0..]->(c:Commit)
                RETURN collect(c.hash) AS hashes
            """, tip=b["tip"]).single()["hashes"]
            branch_sets[b["name"]] = set(anc)
        tags = {}
        for t in s.run("""
            MATCH (t:Tag {repo: $repo})-[:POINTS_TO]->(c:Commit)
            RETURN t.name AS name, c.hash AS hash
        """, repo=repo).data():
            tags.setdefault(t["hash"], []).append(t["name"])

    order = ["main", "master", "develop"]
    branch_names = [b for b in branch_sets if b not in order] + [b for b in order if b in branch_sets]
    nodes = []
    for c in commits:
        owned = next((b for b in branch_names if c["hash"] in branch_sets.get(b, set())), "")
        nodes.append({
            "id": c["hash"],
            "msg": c["msg"],
            "author": c["author"],
            "date": c["date"],
            "merged": bool(c["merged"]),
            "add": c["add"], "del": c["del"], "files": c["files"],
            "branch": owned,
            "tags": tags.get(c["hash"], []),
        })
    return {"nodes": nodes, "edges": edges}

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
