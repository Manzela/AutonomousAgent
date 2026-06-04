#!/usr/bin/env python3
"""SP-00f negative-permission proof for the autonomousagent-executor GitHub App.

Mints a short-lived installation token from (App ID + private key + installation ID),
then proves the C17 boundary: GATED actions are denied (>=400), STANDING actions succeed (2xx).
Secrets stay in-process: only HTTP statuses, token expiry, and granted-permission scopes are printed.
"""

import base64
import calendar
import json
import os
import subprocess
import time
import urllib.request
import urllib.error

import jwt  # PyJWT

OWNER, REPO = "Manzela", "AutonomousAgent"
BASE = f"https://api.github.com/repos/{OWNER}/{REPO}"
SOPS_ENV = {**os.environ, "SOPS_AGE_KEY_FILE": os.path.expanduser("~/.config/sops/age/keys.txt")}


def sops_d(path, it=None, ot=None):
    cmd = ["sops", "-d"]
    if it:
        cmd += ["--input-type", it]
    if ot:
        cmd += ["--output-type", ot]
    cmd.append(path)
    return subprocess.run(cmd, capture_output=True, text=True, env=SOPS_ENV, check=True).stdout


def api(method, url, token, body=None, scheme="Bearer"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"{scheme} {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


# 1. App credentials
env_txt = sops_d("secrets/github-app.env.sops", "dotenv", "dotenv")
env = dict(
    line.split("=", 1)
    for line in env_txt.splitlines()
    if "=" in line and not line.lstrip().startswith("#")
)
app_id = env["GITHUB_APP_ID"].strip()
inst_id = env["GITHUB_APP_INSTALLATION_ID"].strip()
pem = sops_d("secrets/github-app-private-key.pem.sops")

# 2. mint JWT (<=10m) then exchange for an installation token (<=1h)
now = int(time.time())
app_jwt = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, pem, algorithm="RS256")
st, tok = api("POST", f"https://api.github.com/app/installations/{inst_id}/access_tokens", app_jwt)
assert st == 201, f"token mint failed: {st} {tok}"
inst_token, expires_at, perms = tok["token"], tok["expires_at"], tok.get("permissions", {})

out = {
    "app_id": app_id,
    "installation_id": inst_id,
    "token_expires_at": expires_at,
    "token_ttl_seconds": int(
        calendar.timegm(time.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")) - time.time()
    ),
    "granted_permissions": perms,
    "tests": [],
}

# main sha (needs Contents:read — itself a sanity check)
s, ref = api("GET", f"{BASE}/git/ref/heads/main", inst_token)
main_sha = ref["object"]["sha"] if s == 200 and isinstance(ref, dict) else None
out["contents_read_main"] = {"status": s, "sha": (main_sha[:12] + "…") if main_sha else None}


def rec(name, method, status, expect, ok, extra=None):
    e = {"name": name, "method": method, "status": status, "expect": expect, "pass": ok}
    if extra:
        e.update(extra)
    out["tests"].append(e)


# --- RED: gated actions MUST be denied (>=400) ---
s, _ = api(
    "POST", "https://api.github.com/user/repos", inst_token, {"name": "sp00f-should-not-exist"}
)
rec("create-repo", "POST /user/repos", s, ">=400 denied", s >= 400)
s, _ = api(
    "PUT",
    f"{BASE}/actions/secrets/SP00F_PROOF",
    inst_token,
    {"encrypted_value": "AA==", "key_id": "0"},
)
rec("actions-secret-write", "PUT actions/secrets/SP00F_PROOF", s, ">=400 denied", s >= 400)
s, _ = api("PATCH", f"{BASE}/git/refs/heads/main", inst_token, {"sha": main_sha, "force": True})
rec(
    "force-push-protected-main",
    "PATCH git/refs/heads/main force=true (no-op self-sha)",
    s,
    ">=400 denied",
    s >= 400,
)

# --- GREEN: standing actions MUST succeed (2xx) ---
branch = "sp00f-app-proof"
api("DELETE", f"{BASE}/git/refs/heads/{branch}", inst_token)  # clear any prior run
s, _ = api("POST", f"{BASE}/git/refs", inst_token, {"ref": f"refs/heads/{branch}", "sha": main_sha})
rec("branch-create", "POST git/refs", s, "201", s == 201)
# commit a throwaway file ON the branch only (gives the PR a diff; also proves Contents:write)
content_b64 = base64.b64encode(
    b"SP-00f app-token proof marker. Auto-created, branch deleted.\n"
).decode()
s, _ = api(
    "PUT",
    f"{BASE}/contents/.sp00f-proof.txt",
    inst_token,
    {"message": "chore: SP-00f proof marker (auto)", "content": content_b64, "branch": branch},
)
rec("contents-write", "PUT contents/.sp00f-proof.txt (branch only)", s, "201", s == 201)
s, pr = api(
    "POST",
    f"{BASE}/pulls",
    inst_token,
    {
        "title": "chore: SP-00f app-token proof (auto, auto-closed)",
        "head": branch,
        "base": "main",
        "draft": True,
        "body": "Automated SP-00f negative-permission proof — auto-closed, branch deleted.",
    },
)
pr_num = pr.get("number") if isinstance(pr, dict) else None
rec("pr-open", "POST pulls (draft)", s, "201", s == 201, {"pr": pr_num})
if pr_num:
    s, _ = api(
        "POST",
        f"{BASE}/issues/{pr_num}/comments",
        inst_token,
        {"body": "SP-00f green proof: issue-comment via App installation token."},
    )
    rec("issue-comment", "POST issues/{n}/comments", s, "201", s == 201)

# --- cleanup ---
if pr_num:
    api("PATCH", f"{BASE}/pulls/{pr_num}", inst_token, {"state": "closed"})
api("DELETE", f"{BASE}/git/refs/heads/{branch}", inst_token)
out["cleanup"] = {"pr_closed": pr_num, "branch_deleted": branch}

reds = [t for t in out["tests"] if t["expect"].endswith("denied")]
greens = [t for t in out["tests"] if t["expect"] == "201"]
out["summary"] = {
    "red_all_denied": all(t["pass"] for t in reds),
    "green_all_2xx": all(t["pass"] for t in greens),
    "ttl_under_2h": out["token_ttl_seconds"] < 7200,
}
print(json.dumps(out, indent=2))
