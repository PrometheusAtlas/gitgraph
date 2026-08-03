#!/usr/bin/env python3
"""Shared Neo4j connection + schema helpers for gitgraph."""
import os
from pathlib import Path

from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parent.parent

def load_env():
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def get_driver():
    env = load_env()
    uri = env.get("NEO4J_URI", "bolt://localhost:7687")
    user = env.get("NEO4J_USER", "neo4j")
    password = env.get("NEO4J_PASSWORD", "gitgraph")
    return GraphDatabase.driver(uri, auth=(user, password))

def ensure_schema(driver):
    with driver.session() as s:
        for stmt in [
            "CREATE INDEX gitgraph_commit_hash IF NOT EXISTS FOR (c:Commit) ON (c.hash)",
            "CREATE INDEX gitgraph_author_email IF NOT EXISTS FOR (a:Author) ON (a.email)",
            "CREATE INDEX gitgraph_file_path IF NOT EXISTS FOR (f:File) ON (f.path)",
            "CREATE INDEX gitgraph_branch_name IF NOT EXISTS FOR (b:Branch) ON (b.name, b.repo, b.remote)",
            "CREATE INDEX gitgraph_tag_name IF NOT EXISTS FOR (t:Tag) ON (t.name, t.repo)",
        ]:
            s.run(stmt)

def wait_ready(uri="bolt://localhost:7687", timeout=120):
    import time
    env = load_env()
    user = env.get("NEO4J_USER", "neo4j")
    password = env.get("NEO4J_PASSWORD", "gitgraph")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            driver.verify_connectivity()
            driver.close()
            return True
        except Exception:
            time.sleep(2)
    return False
