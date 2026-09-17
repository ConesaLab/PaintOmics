#!/usr/bin/env bash
# Report what a code-review run actually did, and fail when it reviewed nothing.
#
# A green "Claude review" has never meant a review took place. Three ways a run
# reaches a green tick having reviewed nothing, all observed:
#
#   * workflow-validation skip -- the action refuses to run when this file
#     differs from the copy on the default branch, logs a warning and exits 0
#     (run 33539372977). No transcript is written at all.
#   * quota rejection -- one API call, refused before any model ran
#     (`is_error: true, num_turns: 1, total_cost_usd: 0`). This one does fail
#     the step, but only because the action checks is_error.
#   * empty result -- `subtype: success, is_error: false`, and a result string
#     of length zero. Reproduced on 2026-09-17 driving the CSIC gateway model
#     through the CLI: one Read returned "File does not exist" and the session
#     ended silently, 5 turns, no findings, no summary.
#
# The third is the dangerous one, because nothing in the action's own output
# distinguishes it from a clean diff. This script is what separates them. It
# prints the outcome of every run and exits non-zero when no review happened,
# which is also the signal the gateway fallback job triggers on.
#
# Reads only `result` and `assistant` records. Tool results are `user` records
# and are never read: they carry the diff, and this runs on a public repo.
set -uo pipefail

FILE="${EXECUTION_FILE:-}"

if [ -z "${FILE}" ] || [ ! -s "${FILE}" ]; then
  echo "::error::no transcript at '${FILE:-<unset>}' -- Claude never started, so this diff was NOT reviewed"
  echo "the cause is in the action step above: the token, the plugin install, the checkout,"
  echo "or a workflow-validation skip (which happens on any pull request that edits this workflow)"
  exit 1
fi

jq -r '
  (map(select(.type == "result")) | last // {}) as $r
  | (map(select(.type == "assistant")) | last // {}) as $a
  | def text: if type == "array" then map(.text // "") | join(" ") else tostring end;
    "result: subtype=\($r.subtype // "none") is_error=\(if $r | has("is_error") then ($r.is_error | tostring) else "none" end) num_turns=\($r.num_turns // "none")",
    "result text: \(($r.result // "") | tostring | .[0:2000])",
    "result errors: \(($r.errors // []) | map(tostring) | join(" | "))",
    "last assistant error: \($a.error // "none")",
    "last assistant text: \(($a.message.content // "") | text | .[0:2000])"
' "${FILE}"

# The evidence that work happened: comments posted, or a summary written.
posted=$(jq '[.[] | select(.type == "assistant") | .message.content[]?
              | select(.type == "tool_use" and (.name | test("create_inline_comment")))] | length' "${FILE}")
tools=$(jq '[.[] | select(.type == "assistant") | .message.content[]?
             | select(.type == "tool_use")] | length' "${FILE}")
summary=$(jq -r '(map(select(.type == "result")) | last // {}) | (.result // "") | tostring | length' "${FILE}")
# NOT `.is_error // true`: jq's `//` treats `false` as absent, so the happy path
# (`is_error: false`) would come back "true" and fail every successful review.
failed=$(jq -r '(map(select(.type == "result")) | last // {})
                | if has("is_error") then (.is_error | tostring) else "true" end' "${FILE}")
denied=$(jq -r '(map(select(.type == "result")) | last // {}) | (.permission_denials // []) | length' "${FILE}")

echo "---"
echo "inline comments posted: ${posted}"
echo "tool calls made:        ${tools}"
echo "summary text length:    ${summary}"
echo "permission denials:     ${denied}"

if [ "${failed}" != "false" ]; then
  echo "::error::the run ended in an error -- this diff was NOT reviewed"
  exit 1
fi

# A genuinely clean diff still writes a summary saying so. Zero comments AND
# zero summary means the session died quietly rather than finding nothing.
if [ "${posted}" -eq 0 ] && [ "${summary}" -eq 0 ]; then
  echo "::error::no comments and no summary -- the session ended without reviewing the diff"
  exit 1
fi

# Not fatal: the reviewer worked, but its allowlist is too narrow to work well.
if [ "${denied}" -gt 0 ]; then
  echo "::warning::${denied} permission denial(s) -- the tool allowlist may be missing something the plugin needs"
fi

echo "a review happened"
