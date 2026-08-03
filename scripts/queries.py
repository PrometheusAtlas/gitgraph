#!/usr/bin/env python3
"""Named showcase queries — the things plain git visualizers can't answer."""

QUERIES = {
    "stale-branches": {
        "description": "Local branches with commits NOT merged into main (how far behind)",
        "needs_repo": True,
        "cypher": """
MATCH (b:Branch {repo: $repo, remote: 'local'})-[:POINTS_TO]->(tip:Commit)
WHERE b.name <> 'main'
MATCH (main:Branch {repo: $repo, name: 'main', remote: 'local'})-[:POINTS_TO]->(mtip:Commit)
CALL (mtip) {
  MATCH (mtip)-[:PARENT*0..]->(mc:Commit)
  RETURN collect(mc.hash) AS main_hashes
}
CALL (tip) {
  MATCH (tip)-[:PARENT*0..]->(tc:Commit)
  RETURN collect(tc.hash) AS tip_hashes
}
RETURN b.name AS branch,
       size([h IN main_hashes WHERE NOT h IN tip_hashes]) AS commits_behind_main,
       size(tip_hashes) AS branch_commits
ORDER BY commits_behind_main DESC
""",
    },
    "divergent-files": {
        "description": "Files touched by a branch that main has never seen",
        "needs_repo": True,
        "needs_branch": True,
        "cypher": """
MATCH (b:Branch {repo: $repo, name: $branch, remote: 'local'})-[:POINTS_TO]->(tip:Commit)
MATCH (main:Branch {repo: $repo, name: 'main', remote: 'local'})-[:POINTS_TO]->(mtip:Commit)
MATCH (tip)-[:PARENT*0..]->(tc:Commit)
WHERE NOT (mtip)-[:PARENT*]->(tc)
WITH tip, mtip, collect(DISTINCT tc) AS branch_commits
UNWIND branch_commits AS tc
MATCH (tc)-[:MODIFIED]->(f:File)
OPTIONAL MATCH (f)<-[:MODIFIED]-(mc:Commit)
WHERE (mtip)-[:PARENT*]->(mc)
RETURN f.path AS file,
       count(DISTINCT tc) AS commits_touching,
       CASE WHEN count(mc) > 0 THEN 'also on main' ELSE 'BRANCH ONLY' END AS status
ORDER BY commits_touching DESC
LIMIT 25
""",
    },
    "author-stats": {
        "description": "Commits / additions / deletions per author, across all repos",
        "needs_repo": False,
        "cypher": """
MATCH (c:Commit)-[:AUTHORED_BY]->(a:Author)
MATCH (r:Repo)-[:CONTAINS]->(c)
RETURN a.name AS author, r.name AS repo,
       count(c) AS commits,
       sum(c.additions) AS additions,
       sum(c.deletions) AS deletions
ORDER BY commits DESC
""",
    },
    "hot-files": {
        "description": "The most frequently modified files in the graph",
        "needs_repo": False,
        "cypher": """
MATCH (f:File)<-[:MODIFIED]-(c:Commit)
RETURN f.path AS file,
       count(c) AS touches,
       count(DISTINCT c.author_email) AS authors
ORDER BY touches DESC
LIMIT 20
""",
    },
    "merge-map": {
        "description": "Every merge commit — the shape of your branching history",
        "needs_repo": True,
        "cypher": """
MATCH (r:Repo {name: $repo})-[:CONTAINS]->(c:Commit)
WHERE c.merged
RETURN c.hash AS hash, c.msg AS message, c.committed_at AS when,
       size((c)-[:PARENT]->()) AS parents
ORDER BY c.committed_at DESC
LIMIT 25
""",
    },
    "longest-history": {
        "description": "The longest chain of commits (deepest ancestry) per repo",
        "needs_repo": False,
        "cypher": """
MATCH (r:Repo)-[:CONTAINS]->(c:Commit)
WHERE NOT (c)<-[:PARENT]-()   /* roots only */
MATCH path = (c)-[:PARENT*]->(root:Commit)
WHERE NOT (root)-[:PARENT]->()
RETURN r.name AS repo, length(path) AS depth, root.hash AS root
ORDER BY depth DESC
LIMIT 10
""",
    },
    "night-owls": {
        "description": "Authors who commit outside working hours (local-time guess: hour < 8 or > 20)",
        "needs_repo": False,
        "cypher": """
MATCH (c:Commit)-[:AUTHORED_BY]->(a:Author)
WITH a, collect(c) AS all_c,
     [x IN collect(c) WHERE datetime(x.authored_at).hour < 8 OR datetime(x.authored_at).hour > 20] AS night
RETURN a.name AS author, size(night) AS night_commits,
       toInteger(size(night) * 100.0 / size(all_c)) AS pct_of_work
ORDER BY night_commits DESC
LIMIT 10
""",
    },
}
