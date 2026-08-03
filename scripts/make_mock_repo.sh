#!/usr/bin/env bash
# Generate a fictional demo repository (demo/mock-repo) for screenshots and
# README GIFs — no real project data ever appears in public materials.
#
# "lumen" — a fictional lightweight web app, ~30 commits, 3 authors,
# main + 3 feature branches, 3 merges, 3 tags.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
MOCK="$DIR/demo/mock-repo"

if [ -d "$MOCK/.git" ]; then
  echo "mock repo already exists: $MOCK"
  exit 0
fi
mkdir -p "$MOCK"

who() {  # who <name> <email> — switch the commit author
  export GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2"
  export GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2"
}
who "Alex Rivera" "alex@lumen.dev"

cd "$MOCK"
git init -q -b main
TS="2026-01-05T10:00:00+00:00"

f() { mkdir -p "$(dirname "$1")"; printf '%s\n' "$2" > "$1"; }
c() {  # c <message>  (stages everything first)
  git add -A
  GIT_AUTHOR_DATE="$TS" GIT_COMMITTER_DATE="$TS" git commit -q -m "$1"
  bump
}
m() {  # m <branch> <message> — merge with the same fictional timeline
  GIT_AUTHOR_DATE="$TS" GIT_COMMITTER_DATE="$TS" git merge -q --no-ff "$1" -m "$2"
  bump
}
bump() { TS=$(date -u -d "$TS +2 days" +%Y-%m-%dT%H:%M:%S+00:00); }

# ---- main line (Alex) ----
f README.md "lumen — a tiny web app for tracking lightbulb inventory
Build: npm install && npm start
Tests: npm test"
f package.json '{"name":"lumen","version":"0.1.0","scripts":{"start":"node src/server.js","test":"node --test tests/"}}'
c "Initial project scaffold"

f src/server.js "const http = require('http');
http.createServer((req, res) => { res.end('lumen'); }).listen(3000);"
c "Add basic HTTP server"

f src/config.js "module.exports = { port: 3000, db: process.env.LUMEN_DB || 'sqlite' };"
c "Add config loading"

# ---- feat/payments (Sam) ----
git switch -q -c feat/payments
who "Sam Chen" "sam@lumen.dev"
f src/payments.js "module.exports = { create: (amount) => ({ id: Math.random(), amount }) };"
c "Add payment model"
f src/payments.js "module.exports = { create: (amount) => ({ id: Math.random(), amount }),
  charge: (p) => p };"
f src/stripe.js "module.exports = { charge: (token, amount) => ({ ok: true, token, amount }) };"
c "Add Stripe integration"
f src/webhooks.js "module.exports = (app) => app.post('/webhooks/stripe', (req, res) => res.json({ received: true }));"
c "Add webhook handler"

# ---- main catches up (Alex) ----
git switch -q main
who "Alex Rivera" "alex@lumen.dev"
f src/log.js "module.exports = (msg) => console.log(new Date().toISOString(), msg);"
c "Add logging middleware"
f src/server.js "const { createServer } = require('http');
const { log } = require('./log');
createServer((req, res) => { log(req.url); res.end('lumen'); }).listen(3000);"
c "Add health endpoint"
m feat/payments "Merge feat/payments into main"
git tag -a v0.2.0 -m "v0.2.0"

# ---- hotfix/login (Priya) ----
git switch -q -c hotfix/login
who "Priya Nair" "priya@lumen.dev"
f src/auth.js "module.exports = (req, res, next) => {
  if (!req.headers.authorization) return res.statusCode = 401;
  next(); };"
c "Fix login redirect loop"
f src/auth.js "module.exports = (req, res, next) => {
  if (!req.headers.authorization) return res.statusCode = 401;
  if (Date.now() - req.sessionAt > 3600e3) return res.statusCode = 401;
  next(); };"
c "Add session expiry"

# ---- main continues (Priya + Sam) ----
git switch -q main
who "Priya Nair" "priya@lumen.dev"
f src/routes.js "module.exports = { settings: (req, res) => res.json({ theme: 'dark' }) };"
c "Add user settings page"
m hotfix/login "Merge hotfix/login into main"
git tag -a v0.3.0 -m "v0.3.0"

who "Sam Chen" "sam@lumen.dev"
f src/rate-limit.js "module.exports = (n) => { let hits = 0; return () => ++hits > n ? 429 : 200; };"
c "Add API rate limiting"
f tests/auth.test.js "const test = require('node:test');
test('rejects missing auth', () => { /* placeholder */ });"
c "Add tests for auth"
f src/auth.js "const store = new Map();
module.exports = { login: (u, p) => { const t = String(Math.random()); store.set(t, u); return t; },
  verify: (t) => store.get(t) };"
c "Refactor auth middleware"
f src/metrics.js "module.exports = { inc: (name) => { globalThis.metrics = globalThis.metrics || {}; metrics[name] = (metrics[name] || 0) + 1; } };"
c "Add metrics endpoint"
who "Alex Rivera" "alex@lumen.dev"
f docs/architecture.md "# lumen architecture

server -> auth -> routes -> storage"
c "Update docs"
f src/server.js "const cors = require('./cors');
module.exports = { start: () => { cors(); } };"
c "Fix CORS headers"
f Dockerfile "FROM node:20-alpine
WORKDIR /app
COPY . .
CMD [\"npm\", \"start\"]"
c "Add dockerfile"
f package.json '{"name":"lumen","version":"0.3.1","scripts":{"start":"node src/server.js","test":"node --test tests/"}}'
c "Pin dependencies"

# ---- feat/search (Priya) ----
git switch -q -c feat/search
who "Priya Nair" "priya@lumen.dev"
f src/search-index.js "module.exports = { add: (doc) => { index.push(doc); }, find: (q) => index.filter(d => d.includes(q)) };"
c "Add search index"
f src/search.js "module.exports = (app) => app.get('/search', (req, res) => res.json([]));"
c "Add search endpoint"
f public/search.html "<input id=q placeholder=\"search\"><ul id=r></ul>"
c "Add search UI"

# ---- main continues + merge (Sam) ----
git switch -q main
who "Sam Chen" "sam@lumen.dev"
f public/index.html "<h1>lumen</h1><p>track your bulbs</p>"
c "Update landing page copy"
f src/routes.js "module.exports = { settings: (req, res) => res.json({ theme: 'dark' }),
  list: (req, res) => res.json([]) };"
c "Add pagination"
m feat/search "Merge feat/search into main"
git tag -a v0.4.0 -m "v0.4.0"

# ---- wrap up (mixed) ----
who "Priya Nair" "priya@lumen.dev"
f tests/search.test.js "const test = require('node:test');
test('search returns matches', () => { /* placeholder */ });"
c "Fix flaky test"
who "Sam Chen" "sam@lumen.dev"
f src/errors.js "module.exports = class LumenError extends Error {};"
c "Add error tracking"
who "Alex Rivera" "alex@lumen.dev"
f README.md "lumen — a tiny web app for tracking lightbulb inventory
Build: npm install && npm start
Tests: npm test
Docs: docs/architecture.md"
c "Update README"

echo "mock repo created: $MOCK"
git log --oneline | wc -l | xargs echo "commits:"
git log --format='%an' | sort | uniq -c
git branch
