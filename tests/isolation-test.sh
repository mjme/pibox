#!/usr/bin/env bash
# Checks pibox's isolation end to end: starts real boxes in throwaway folders and
# probes what they can and can't reach. Needs Docker running and the pibox image built
# (`pibox rebuild`). Ollama is optional; without it the chat test is skipped.
#
#   bash tests/isolation-test.sh
set -uo pipefail

PIBOX="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/pibox"
pass=0; fail=0
ok()  { printf '\e[32mPASS\e[0m %s\n' "$*"; pass=$((pass + 1)); }
bad() { printf '\e[31mFAIL\e[0m %s\n' "$*"; fail=$((fail + 1)); }

tmp="$(cd -P "$(mktemp -d)" && pwd)"
proj="$tmp/project"
cleanup() {
  local d
  for d in "$proj" "$tmp/symlinked"; do
    docker volume ls -q --filter "label=pibox.dir=$d" | xargs docker volume rm >/dev/null 2>&1
  done
  rm -rf "$tmp"
}
trap cleanup EXIT

mkdir -p "$proj"
git -C "$proj" init -q && git -C "$proj" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init

# The probe runs inside the box. It prints PASS/FAIL lines, which are counted below.
cat > "$proj/probe.sh" <<EOF
#!/usr/bin/env bash
HOST_HOME="$HOME"
EOF
cat >> "$proj/probe.sh" <<'EOF'
p() { printf 'PASS %s\n' "$*"; }
f() { printf 'FAIL %s\n' "$*"; }
expect_fail() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then f "$what"; else p "$what"; fi; }
expect_ok()   { local what="$1"; shift; if "$@" >/dev/null 2>&1; then p "$what"; else f "$what"; fi; }
code() { curl -s -o /dev/null -w '%{http_code}' -m 10 "$@"; }
expect_code() { local want="$1" what="$2"; shift 2; local got; got="$(code "$@")"
  [ "$got" = "$want" ] && p "$what ($got)" || f "$what (wanted $want, got $got)"; }

# Network: nothing but the proxy.
expect_fail "no internet (https://example.com)"      curl -sf -m 5 https://example.com
expect_fail "no internet by IP (1.1.1.1)"            curl -sf -m 5 https://1.1.1.1
expect_fail "no DNS for outside names"               getent hosts example.com
expect_fail "no route to the Mac (host.docker.internal)" curl -sf -m 3 http://host.docker.internal:11434/api/version
expect_fail "no Docker Engine API (192.168.65.7:2375)" curl -sf -m 3 http://192.168.65.7:2375/version
expect_fail "no Docker socket"                       test -e /var/run/docker.sock

# Ollama proxy: chat with local models only.
expect_code 200 "proxy: /api/version allowed"   http://ollama:11434/api/version
expect_code 200 "proxy: /api/tags allowed"      http://ollama:11434/api/tags
expect_code 200 "proxy: /v1/models allowed"     http://ollama:11434/v1/models
expect_code 403 "proxy: /api/pull blocked"      -X POST -d '{"model":"x"}' http://ollama:11434/api/pull
expect_code 403 "proxy: /api/push blocked"      -X POST -d '{"model":"x"}' http://ollama:11434/api/push
expect_code 403 "proxy: /api/create blocked"    -X POST -d '{"model":"x"}' http://ollama:11434/api/create
expect_code 403 "proxy: /api/copy blocked"      -X POST -d '{"source":"a","destination":"b"}' http://ollama:11434/api/copy
expect_code 405 "proxy: DELETE /api/delete blocked" -X DELETE -d '{"model":"x"}' http://ollama:11434/api/delete
expect_code 403 "proxy: /api/generate blocked"  -X POST -d '{"model":"x"}' http://ollama:11434/api/generate
expect_code 403 "proxy: ../ path trick blocked" --path-as-is -X POST -d '{"model":"x"}' http://ollama:11434/v1/../api/pull
expect_code 403 "proxy: unknown model refused"  -X POST -d '{"model":"not-installed:1","messages":[]}' http://ollama:11434/v1/chat/completions
expect_code 403 "proxy: cloud model refused"    -X POST -d '{"model":"gpt-oss:120b-cloud","messages":[]}' http://ollama:11434/v1/chat/completions
model="$(curl -s -m 10 http://ollama:11434/api/tags | python3 -c 'import json,sys; m=[x["name"] for x in json.load(sys.stdin)["models"] if "embed" not in x["name"]]; print(sorted(m, key=len)[0] if m else "")' 2>/dev/null)"
if [ -n "$model" ]; then
  body="{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hi\"}],\"max_tokens\":3}"
  got="$(curl -s -o /dev/null -w '%{http_code}' -m 180 -H 'Content-Type: application/json' -d "$body" http://ollama:11434/v1/chat/completions)"
  [ "$got" = 200 ] && p "proxy: chat with local model $model (200)" || f "proxy: chat with $model (got $got)"
  got="$(curl -sN -m 180 -H 'Content-Type: application/json' -d "${body%\}},\"stream\":true}" http://ollama:11434/v1/chat/completions | grep -c '^data:')"
  [ "${got:-0}" -gt 1 ] && p "proxy: streaming chat ($got events)" || f "proxy: streaming chat ($got events)"
else
  echo "SKIP chat test: Ollama isn't reachable or has no models"
fi

# Files: the project is writable, .git and the system aren't.
expect_ok   "project folder is writable"        touch ./pibox-probe-ok
rm -f ./pibox-probe-ok
expect_fail ".git is read-only"                 touch .git/pibox-probe
expect_fail ".git hooks can't be added"         sh -c 'echo x > .git/hooks/pre-commit'
expect_fail ".git can't be renamed"             mv .git .git-moved
expect_fail ".git can't be deleted"             rm -rf .git
expect_ok   "git status still works"            git status --short
expect_fail "system files are read-only"        touch /usr/local/bin/pibox-probe
expect_fail "/etc is read-only"                 touch /etc/pibox-probe
expect_fail "your home folder isn't in the box" test -e "$HOST_HOME"
expect_ok   "runs as uid 1000, not root"        test "$(id -u)" = 1000
expect_ok   "no Linux capabilities"             grep -q '^CapEff:[[:space:]]*0*$' /proc/self/status
expect_ok   "pi offline mode is on"             test "$PI_OFFLINE" = 1
n="$(ls ~/.pi/agent/sessions 2>/dev/null | wc -l | tr -d ' ')"
[ "$n" = 0 ] && p "fresh folder sees no other folder's sessions" || f "sessions visible: $n"

# Leave a new git repo behind: pibox should warn about it after the session.
mkdir -p sub && git init -q sub
EOF

echo "── box probes"
out="$(cd "$proj" && "$PIBOX" shell probe.sh 2>"$tmp/stderr")"
printf '%s\n' "$out" | grep -E '^(PASS|FAIL|SKIP)' | sed -e 's/^PASS /\x1b[32mPASS\x1b[0m /' -e 's/^FAIL /\x1b[31mFAIL\x1b[0m /'
pass=$((pass + $(printf '%s\n' "$out" | grep -c '^PASS')))
fail=$((fail + $(printf '%s\n' "$out" | grep -c '^FAIL')))
grep -q '^PASS no internet' <<< "$out" || { bad "the probe didn't run. pibox said:"; cat "$tmp/stderr"; }

echo "── host checks"
grep -q 'created a new git repository' "$tmp/stderr" && ok "warns about a new .git after the session" \
  || bad "no warning about the new .git"
left="$(docker ps -aq --filter "label=pibox.dir=$proj"; docker network ls -q --filter "label=pibox.dir=$proj")"
[ -z "$left" ] && ok "box, proxy and network removed after the session" || bad "left behind: $left"

mkdir -p "$tmp/symlinked" "$tmp/elsewhere"
ln -s "$tmp/elsewhere" "$tmp/symlinked/.git"
# (Output is captured first: pibox exits non-zero here, which pipefail would count.)
refused() { local out; out="$(cd "$1" && "$PIBOX" shell -c true 2>&1)"; grep -q "$2" <<< "$out"; }
if refused "$tmp/symlinked" 'is a symlink'; then
  ok "refuses to start when .git is a symlink"
else
  bad "started with a symlinked .git"
fi
if [ -d "$HOME/Library" ] && refused "$HOME/Library" Refusing; then
  ok "refuses to run in ~/Library"
else
  bad "ran in ~/Library"
fi
if refused "$(dirname "$HOME")" Refusing; then
  ok "refuses to run in a folder containing your home folder"
else
  bad "ran in $(dirname "$HOME")"
fi

echo
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
