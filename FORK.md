# This fork — local modifications

This is Lettucech's fork of `NousResearch/hermes-agent`, used by a multi-machine
Hermes fleet. Fork `main` = upstream `main` + the commits listed below.

**Policy:** modifying Hermes source code needs Brian's explicit approval per
instance — it is the LAST resort (config / skill / runbook first, then upstream
issue / PR, then — only when cornered — a source patch).

## Local modifications

| Commit | What | Upstream | Revert when |
|---|---|---|---|
| `229c4367cd` | Slack processing indicator: `_session_status_method` pinned to legacy `assistant_threads_setStatus` — slack-sdk >= 3.44 rejects free-text status on `agents.sessions` (`invalid_arguments`), so the indicator silently vanished | issue #110374; fix PRs #110389 / #110391 | those PRs land |

## Where the rest lives

- Authoritative register + patch provenance: `Lettucech/agent-skills` →
  `hermes-patches/README.md`
- Fleet ops runbook (sync / update / verify / bootstrap): `Lettucech/agent-skills`
  → skill `hermes-fleet-ops`
