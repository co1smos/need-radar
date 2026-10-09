import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  buildImplementerRoundContext,
  buildPhaseCommand,
  declaredBlockerNumbers,
  extractAcceptanceCriteria,
  failedCriterionFindings,
  formatAcceptanceCriteria,
  parseCliOptions,
  roundArtifactPaths,
  selectReadyIssues,
  summarizeSettled,
  successfulCandidates,
  unsuccessfulIssueNumbers,
  updateReviewState,
  validateImplementerReceipt,
  validateMergerReceipt,
  validateReviewerReceipt,
} from "./workflow-core.mjs";

const routingEnv = {
  RALPH_IMPLEMENTER_HARNESS: "codex",
  RALPH_IMPLEMENTER_MODEL: "test-implementer-model",
  RALPH_IMPLEMENTER_EFFORT: "max",
  RALPH_REVIEWER_HARNESS: "codex",
  RALPH_REVIEWER_MODEL: "test-reviewer-model",
  RALPH_REVIEWER_EFFORT: "medium",
  RALPH_MERGER_HARNESS: "codex",
  RALPH_MERGER_MODEL: "test-merger-model",
  RALPH_MERGER_EFFORT: "high",
};

test("parseCliOptions requires explicit routing for all phases", () => {
  assert.throws(() => parseCliOptions([], {}), /implementer harness, model, and effort/);
  const options = parseCliOptions([], routingEnv);
  assert.deepEqual(options.implementer, { harness: "codex", model: "test-implementer-model", effort: "max" });
  assert.deepEqual(options.reviewer, { harness: "codex", model: "test-reviewer-model", effort: "medium" });
  assert.deepEqual(options.merger, { harness: "codex", model: "test-merger-model", effort: "high" });
  assert.equal(options.maxParallel, 4);
});

test("CLI phase routing overrides env", () => {
  const options = parseCliOptions([
    "--implementer-model", "override-implementer-model", "--reviewer-effort", "high",
    "--merger-model", "override-merger-model", "--max-parallel", "2", "--preflight",
  ], routingEnv);
  assert.equal(options.implementer.model, "override-implementer-model");
  assert.equal(options.reviewer.effort, "high");
  assert.equal(options.merger.model, "override-merger-model");
  assert.equal(options.maxParallel, 2);
  assert.equal(options.dryRun, true);
});

test("v1 rejects declared but unimplemented harnesses cleanly", () => {
  assert.throws(() => parseCliOptions([], { ...routingEnv, RALPH_REVIEWER_HARNESS: "pi" }), /pi harness is declared but not implemented in v1/);
  assert.throws(() => parseCliOptions([], { ...routingEnv, RALPH_IMPLEMENTER_HARNESS: "claude-code" }), /claude-code harness is declared but not implemented in v1/);
});

test("declaredBlockerNumbers extracts fallback blocker declarations", () => {
  assert.deepEqual(declaredBlockerNumbers("Blocked by: #3, #7\nDepends on: #9\nfoo #11"), [3, 7, 9]);
});

function issue(number, { state = "OPEN", labels = ["ready-for-agent"], blockers = [] } = {}) {
  return { number, title: `Issue ${number}`, state, labels, blockers };
}

test("selectReadyIssues returns deterministic ready frontier up to max parallel", () => {
  const issues = [
    issue(9), issue(2), issue(5, { blockers: [{ number: 1, state: "OPEN" }] }),
    issue(1, { state: "CLOSED" }), issue(7, { labels: [] }), issue(3),
  ];
  assert.deepEqual(selectReadyIssues(issues, { maxParallel: 2 }).map((x) => x.number), [2, 3]);
  assert.deepEqual(selectReadyIssues(issues, { maxParallel: 5 }).map((x) => x.number), [2, 3, 9]);
});

test("selectReadyIssues skips triage issues before applying parallelism", () => {
  const issues = [issue(1), issue(2), issue(3), issue(4)];
  assert.deepEqual(selectReadyIssues(issues, { maxParallel: 2, excludedNumbers: new Set([1, 2]) }).map((x) => x.number), [3, 4]);
});

test("selectReadyIssues supports a single issue override", () => {
  assert.deepEqual(selectReadyIssues([issue(2), issue(3)], { overrideNumber: 3 }).map((x) => x.number), [3]);
  assert.throws(() => selectReadyIssues([issue(2), issue(3, { labels: [] })], { overrideNumber: 3 }), /not ready/);
});

test("buildPhaseCommand builds a visible Codex/Unsnooze noninteractive command", () => {
  const command = buildPhaseCommand({
    harness: "codex", model: "test-model", effort: "high",
    worktreePath: "/tmp/w t", schemaPath: "/tmp/schema.json", receiptPath: "/tmp/receipt.json", promptPath: "/tmp/prompt.md",
  });
  assert.match(command, /^unsnooze _run codex /);
  assert.match(command, /--model test-model/);
  assert.match(command, /model_reasoning_effort/);
  assert.match(command, /--output-last-message/);
  assert.match(command, /< \/tmp\/prompt\.md$/);
});

test("round artifacts remain distinct across correction rounds", () => {
  assert.notDeepEqual(roundArtifactPaths("/tmp/t", 1), roundArtifactPaths("/tmp/t", 2));
});

test("correction context contains exact review/test evidence", () => {
  const text = buildImplementerRoundContext({ round: 2, currentHead: "abc", reviewerFindings: ["fix edge"], focusedTestEvidence: "FAIL x" });
  assert.match(text, /fix edge/);
  assert.match(text, /FAIL x/);
  assert.match(text, /abc/);
});

test("implementer and reviewer receipts enforce core contracts", () => {
  const head = "a".repeat(40);
  validateImplementerReceipt({ phase: "implementer", status: "completed", issue_number: 4, session_id: "session-a", head, completed_at: new Date().toISOString(), reason: "" }, { issueNumber: 4, head, previousHead: "b".repeat(40) });
  validateReviewerReceipt({
    phase: "reviewer", status: "completed", review_mode: "criteria", verdict: "approved",
    session_id: "session-b", reviewed_head: head, completed_at: new Date().toISOString(),
    criteria: [{ id: "AC1", status: "passed", finding: "" }], followups: [], blocker: "",
  }, { reviewedHead: head, implementerSessionId: "session-a", reviewMode: "criteria", expectedCriteriaIds: ["AC1"] });
});

test("implementer JSON schema allows explicit blocked/failed receipts with reasons", () => {
  const schema = JSON.parse(readFileSync(new URL("./implementer-schema.json", import.meta.url), "utf8"));
  assert.deepEqual(schema.properties.status.enum, ["completed", "blocked", "failed"]);
  assert.ok(schema.required.includes("reason"));
  assert.deepEqual(schema.properties.reason, { type: "string" });
});

test("implementer reports blocked or failed work without faking a new commit", () => {
  const head = "a".repeat(40);
  const base = {
    phase: "implementer", issue_number: 4, session_id: "session-a",
    head, completed_at: new Date().toISOString(),
  };
  const expected = { issueNumber: 4, head, previousHead: head };
  assert.throws(() => validateImplementerReceipt({ ...base, status: "completed", reason: "" }, expected),
    /did not create a new candidate commit/);
  assert.equal(validateImplementerReceipt({ ...base, status: "blocked", reason: "source credential unavailable" }, expected).status, "blocked");
  assert.equal(validateImplementerReceipt({ ...base, status: "failed", reason: "test regression requires further investigation" }, expected).status, "failed");
  assert.throws(() => validateImplementerReceipt({ ...base, status: "blocked", reason: "" }, expected), /explain the reason/);
  assert.throws(() => validateImplementerReceipt({ ...base, status: "failed" }, expected), /reason must be a string/);
  assert.throws(() => validateImplementerReceipt({ ...base, status: "completed", reason: "couldn't fix" },
    { ...expected, previousHead: "b".repeat(40) }), /empty reason/);
  assert.throws(() => validateImplementerReceipt({ ...base, status: "surprise", reason: "" }, expected), /invalid implementer status/);
});

test("reviewer followups are non-blocking but validated", () => {
  const head = "f".repeat(40);
  const base = {
    phase: "reviewer", status: "completed", review_mode: "criteria", verdict: "approved",
    session_id: "review", reviewed_head: head, completed_at: new Date().toISOString(), blocker: "",
    criteria: [{ id: "AC1", status: "passed", finding: "" }],
  };
  const expected = { reviewedHead: head, implementerSessionId: "implement", reviewMode: "criteria", expectedCriteriaIds: ["AC1"] };
  assert.equal(validateReviewerReceipt({ ...base, followups: ["Optional rare input support"] }, expected).verdict, "approved");
  assert.throws(() => validateReviewerReceipt({ ...base, followups: [""] }, expected), /followups/);
  assert.throws(() => validateReviewerReceipt({ ...base }, expected), /followups/);
});

test("validateMergerReceipt requires exactly one result per candidate", () => {
  const receipt = {
    phase: "merger", status: "completed", session_id: "merge-session", final_head: "b".repeat(40), completed_at: new Date().toISOString(),
    results: [{ issue_number: 2, status: "merged", detail: "ok" }, { issue_number: 5, status: "rejected", detail: "conflict" }],
  };
  assert.equal(validateMergerReceipt(receipt, [2, 5]), receipt);
  assert.throws(() => validateMergerReceipt({ ...receipt, results: receipt.results.slice(0, 1) }, [2, 5]), /does not cover every candidate/);
});

test("summarizeSettled preserves ticket/outcome alignment without sibling cancellation", async () => {
  const issues = [issue(2), issue(3), issue(9)];
  const settled = await Promise.allSettled([
    Promise.resolve({ head: "a" }), Promise.reject(new Error("boom")), Promise.resolve({ head: "c" }),
  ]);
  const summary = summarizeSettled(issues, settled);
  assert.deepEqual(summary.map((x) => [x.issue.number, x.outcome.status]), [[2, "fulfilled"], [3, "rejected"], [9, "fulfilled"]]);
});


test("ticket-local failure does not cancel siblings and merger sees only fulfilled candidates", async () => {
  let siblingFinished = false;
  const issues = [issue(1), issue(2), issue(3)];
  const settled = await Promise.allSettled([
    Promise.resolve({ issue: 1, head: "a".repeat(40) }),
    Promise.reject(new Error("reviewer crashed")),
    new Promise((resolve) => setTimeout(() => {
      siblingFinished = true;
      resolve({ issue: 3, head: "c".repeat(40) });
    }, 10)),
  ]);
  const summary = summarizeSettled(issues, settled);
  assert.equal(siblingFinished, true);
  assert.deepEqual(summary.map((x) => [x.issue.number, x.outcome.status]), [
    [1, "fulfilled"], [2, "rejected"], [3, "fulfilled"],
  ]);
  assert.deepEqual(successfulCandidates(summary).map((x) => x.issue), [1, 3]);
});

test("failed tickets are excluded for the rest of the run without blocking eligible siblings", async () => {
  const firstBatch = [issue(2), issue(3)];
  const summary = summarizeSettled(firstBatch, await Promise.allSettled([
    Promise.reject(new Error("implementer did not create a new candidate commit")),
    Promise.resolve({ issue: issue(3), head: "c".repeat(40) }),
  ]));
  const skipped = new Set(unsuccessfulIssueNumbers(summary));
  assert.deepEqual([...skipped], [2]);
  assert.deepEqual(successfulCandidates(summary).map((result) => result.issue.number), [3]);
  const nextFrontier = [
    issue(2), // still open and ready, but failed in this run
    issue(4, { blockers: [{ number: 3, state: "CLOSED" }] }),
    issue(5, { blockers: [{ number: 2, state: "OPEN" }] }),
  ];
  assert.deepEqual(selectReadyIssues(nextFrontier, { maxParallel: 3, excludedNumbers: skipped }).map((i) => i.number), [4]);
  assert.deepEqual(selectReadyIssues(nextFrontier, { maxParallel: 3 }).map((i) => i.number), [2, 4]);
  assert.deepEqual(selectReadyIssues([issue(2)], { excludedNumbers: skipped }), []);
});

test("merger rejections are deferred rather than repeatedly reselected", () => {
  const summary = [
    { issue: issue(2), outcome: { status: "fulfilled", value: { issue: issue(2) } } },
    { issue: issue(3), outcome: { status: "fulfilled", value: { issue: issue(3) } } },
  ];
  const mergerReceipt = { results: [
    { issue_number: 2, status: "rejected", detail: "merge conflict" },
    { issue_number: 3, status: "merged", detail: "pushed" },
  ] };
  const skipped = new Set(unsuccessfulIssueNumbers(summary, mergerReceipt));
  assert.deepEqual([...skipped], [2]);
  assert.deepEqual(selectReadyIssues([issue(2), issue(4)], { excludedNumbers: skipped }).map((i) => i.number), [4]);
});

test("all tickets failing yields an empty merger candidate set", async () => {
  const issues = [issue(4), issue(5)];
  const settled = await Promise.allSettled([
    Promise.reject(new Error("implementer exited")),
    Promise.reject(new Error("focused test failed")),
  ]);
  assert.deepEqual(successfulCandidates(summarizeSettled(issues, settled)), []);
});

test("merger receipt cannot silently omit or invent failed-ticket outcomes", () => {
  const base = {
    phase: "merger", status: "completed", session_id: "merge-session",
    final_head: "d".repeat(40), completed_at: new Date().toISOString(),
  };
  assert.throws(() => validateMergerReceipt({
    ...base,
    results: [{ issue_number: 1, status: "merged", detail: "ok" }],
  }, [1, 3]), /does not cover every candidate/);
  assert.throws(() => validateMergerReceipt({
    ...base,
    results: [
      { issue_number: 1, status: "merged", detail: "ok" },
      { issue_number: 2, status: "merged", detail: "should not be here" },
    ],
  }, [1, 3]), /unexpected merger issue #2/);
});


test("extractAcceptanceCriteria prefers the explicit acceptance section", () => {
  const body = `## What to build\nDo it.\n\n## Acceptance criteria\n- [ ] first behavior\n- [ ] second behavior\n\n## Observability\n- [ ] separate policy checkbox`;
  assert.deepEqual(extractAcceptanceCriteria(body), [
    { id: "AC1", text: "first behavior" },
    { id: "AC2", text: "second behavior" },
  ]);
});

test("extractAcceptanceCriteria has bounded fallbacks for older tickets", () => {
  assert.deepEqual(extractAcceptanceCriteria("- [ ] only checkbox"), [{ id: "AC1", text: "only checkbox" }]);
  assert.deepEqual(extractAcceptanceCriteria("Just implement the described behavior."), [
    { id: "AC1", text: "Satisfy the complete issue requirements and repository policy." },
  ]);
});

test("criterion state converges one pending item at a time then can be reopened by final review", () => {
  let state = [
    { id: "AC1", text: "one", status: "pending" },
    { id: "AC2", text: "two", status: "pending" },
    { id: "AC3", text: "three", status: "pending" },
  ];
  state = updateReviewState(state, { criteria: [
    { id: "AC1", status: "passed", finding: "" },
    { id: "AC2", status: "failed", finding: "two is broken" },
    { id: "AC3", status: "passed", finding: "" },
  ] });
  assert.deepEqual(state.map((x) => [x.id, x.status]), [["AC1", "passed"], ["AC2", "pending"], ["AC3", "passed"]]);
  assert.deepEqual(state.filter((x) => x.status === "pending").map((x) => x.id), ["AC2"]);

  state = updateReviewState(state, { criteria: [{ id: "AC2", status: "passed", finding: "" }] });
  assert.equal(state.every((x) => x.status === "passed"), true);

  state = updateReviewState(state, { criteria: [
    { id: "AC1", status: "failed", finding: "regressed" },
    { id: "AC2", status: "passed", finding: "" },
    { id: "AC3", status: "passed", finding: "" },
  ] });
  assert.deepEqual(state.filter((x) => x.status === "pending").map((x) => x.id), ["AC1"]);
});

test("reviewer validation is scoped to requested criteria and final review must cover all requested criteria", () => {
  const head = "e".repeat(40);
  const base = { phase: "reviewer", status: "completed", session_id: "review-session", reviewed_head: head, completed_at: new Date().toISOString(), followups: [], blocker: "" };
  assert.equal(validateReviewerReceipt({
    ...base, review_mode: "criteria", verdict: "changes_requested",
    criteria: [{ id: "AC2", status: "failed", finding: "broken" }],
  }, { reviewedHead: head, implementerSessionId: "impl", reviewMode: "criteria", expectedCriteriaIds: ["AC2"] }).verdict, "changes_requested");
  assert.throws(() => validateReviewerReceipt({
    ...base, review_mode: "final", verdict: "approved",
    criteria: [{ id: "AC1", status: "passed", finding: "" }],
  }, { reviewedHead: head, implementerSessionId: "impl", reviewMode: "final", expectedCriteriaIds: ["AC1", "AC2"] }), /does not cover every requested criterion/);
  assert.throws(() => validateReviewerReceipt({
    ...base, review_mode: "criteria", verdict: "changes_requested",
    criteria: [{ id: "AC9", status: "failed", finding: "invented scope" }],
  }, { reviewedHead: head, implementerSessionId: "impl", reviewMode: "criteria", expectedCriteriaIds: ["AC2"] }), /unexpected reviewer criterion AC9/);
});

test("failedCriterionFindings gives the implementer only currently failed criteria", () => {
  const byId = new Map([["AC2", { id: "AC2", text: "second behavior" }]]);
  assert.deepEqual(failedCriterionFindings({ criteria: [
    { id: "AC2", status: "failed", finding: "fix this exact bug" },
  ] }, byId), ["AC2 (second behavior): fix this exact bug"]);
  assert.equal(formatAcceptanceCriteria([{ id: "AC2", text: "second behavior" }]), "AC2: second behavior");
});
