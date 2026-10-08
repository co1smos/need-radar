import { createSandbox } from "@ai-hero/sandcastle";
import { noSandbox } from "@ai-hero/sandcastle/sandboxes/no-sandbox";
import { execFile } from "node:child_process";
import { copyFile, mkdir, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

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
  shellQuote,
  summarizeSettled,
  successfulCandidates,
  updateReviewState,
  validateImplementerReceipt,
  validateMergerReceipt,
  validateReviewerReceipt,
} from "./workflow-core.mjs";

const execFileAsync = promisify(execFile);
const root = process.cwd();
const options = parseCliOptions(process.argv.slice(2));
const runId = `${Date.now()}-${process.pid}`;
const artifactRoot = join(root, ".sandcastle", "runs", runId);
const lockDir = join(root, ".sandcastle", "orchestrator.lock");

type CommandResult = { exitCode: number; stdout: string; stderr: string };
type Issue = any;
type Candidate = { issue: Issue; branch: string; head: string; ticketRoot: string };

const run = async (file: string, args: string[], cwd = root): Promise<CommandResult> => {
  try {
    const result = await execFileAsync(file, args, { cwd, encoding: "utf8", maxBuffer: 20 * 1024 * 1024 });
    return { exitCode: 0, stdout: result.stdout, stderr: result.stderr };
  } catch (error: any) {
    return {
      exitCode: error.code === "ENOENT" ? 127 : Number.isInteger(error.code) ? error.code : 1,
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? error.message ?? String(error),
    };
  }
};

const requireOk = async (file: string, args: string[], cwd = root) => {
  const result = await run(file, args, cwd);
  if (result.exitCode !== 0) throw new Error(`${file} ${args.join(" ")} failed: ${result.stderr || result.stdout}`);
  return result.stdout.trim();
};

function fillTemplate(template: string, values: Record<string, string | number>) {
  return template.replace(/\{\{([A-Z_]+)\}\}/g, (_, key) => {
    if (!(key in values)) throw new Error(`missing template value ${key}`);
    return String(values[key]);
  });
}

async function repoSlug() {
  return requireOk("gh", ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]);
}

async function resolveIssues(): Promise<Issue[]> {
  const slug = await repoSlug();
  const list = JSON.parse(await requireOk("gh", [
    "issue", "list", "--state", "all", "--limit", "200",
    "--json", "number,title,body,state,labels,url",
  ]));
  const byNumber = new Map(list.map((issue: any) => [issue.number, issue]));
  for (const issue of list) {
    const result = await run("gh", ["api", `repos/${slug}/issues/${issue.number}/dependencies/blocked_by`]);
    issue.nativeBlockerNumbers = result.exitCode === 0
      ? JSON.parse(result.stdout).map((blocker: any) => blocker.number)
      : [];
  }
  return list.map((issue: any) => ({
    ...issue,
    labels: issue.labels.map((label: any) => label.name),
    blockers: [...new Set([...issue.nativeBlockerNumbers, ...declaredBlockerNumbers(issue.body)])]
      .sort((a: any, b: any) => a - b)
      .map((number: any) => ({ number, state: (byNumber.get(number) as any)?.state ?? "OPEN" })),
  }));
}

async function readCodexProviderEnvName() {
  const configPath = join(process.env.CODEX_HOME || join(homedir(), ".codex"), "config.toml");
  const text = await readFile(configPath, "utf8");
  const provider = text.match(/^model_provider\s*=\s*["']([^"']+)["']/m)?.[1];
  if (!provider) return undefined;
  const escaped = provider.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const section = text.match(new RegExp(`\\[model_providers\\.${escaped}\\]([\\s\\S]*?)(?=\\n\\[|$)`))?.[1] || "";
  return section.match(/^env_key\s*=\s*["']([A-Za-z_][A-Za-z0-9_]*)["']/m)?.[1];
}

async function createPane(cwd: string, metadata: Record<string, string>) {
  if (process.env.HERDR_ENV !== "1") throw new Error("workflow must run inside Herdr");
  const args = ["pane", "split", "--current", "--direction", "right", "--cwd", cwd];
  for (const [key, value] of Object.entries(metadata)) args.push("--env", `${key}=${value}`);
  args.push("--no-focus");
  const payload = JSON.parse(await requireOk("herdr", args));
  const paneId = payload?.result?.pane?.pane_id;
  if (!paneId) throw new Error("Herdr did not return a pane ID");
  return paneId as string;
}

async function closePane(paneId: string) { await run("herdr", ["pane", "close", paneId]); }
async function readPane(paneId: string) {
  const result = await run("herdr", ["pane", "read", paneId, "--source", "recent-unwrapped", "--lines", "400"]);
  return result.stdout || result.stderr;
}

async function readOptionalJson(path: string) {
  try { return JSON.parse(await readFile(path, "utf8")); }
  catch (error: any) { if (error.code === "ENOENT" || error instanceof SyntaxError) return undefined; throw error; }
}

async function runPhase({
  phase, ticket, worktreePath, promptPath, schemaPath, receiptPath, paneEvidencePath, config, providerEnvName,
}: any) {
  const env: Record<string, string> = {
    RALPH_RUN_ID: runId,
    RALPH_PHASE: phase,
    RALPH_TICKET: ticket == null ? "merger" : String(ticket),
  };
  if (providerEnvName && process.env[providerEnvName]) env[providerEnvName] = process.env[providerEnvName]!;
  const paneId = await createPane(worktreePath, env);
  const exitPath = `${receiptPath}.exit`;
  const command = buildPhaseCommand({ ...config, worktreePath, schemaPath, receiptPath, promptPath });
  const tracked = `${command}\nralph_exit=$?\nprintf '%s' "$ralph_exit" > ${shellQuote(exitPath)}`;
  try {
    await requireOk("herdr", ["pane", "run", paneId, `/bin/sh -c ${shellQuote(tracked)}`]);
    const deadline = Date.now() + options.timeoutMs;
    while (Date.now() < deadline) {
      const exitText = await readFile(exitPath, "utf8").catch(() => undefined);
      if (exitText !== undefined) {
        const receipt = await readOptionalJson(receiptPath);
        if (exitText.trim() === "0" && receipt !== undefined) {
          await writeFile(paneEvidencePath, await readPane(paneId));
          return { receipt };
        }
        const output = await readPane(paneId);
        throw new Error(`${phase} worker exited with code ${exitText.trim()} before valid receipt\n${output}`);
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(`${phase} worker timed out`);
  } finally {
    await writeFile(paneEvidencePath, await readPane(paneId)).catch(() => {});
    await closePane(paneId);
  }
}

async function runTicket(issue: Issue, iteration: number, baseSha: string, providerEnvName?: string): Promise<Candidate> {
  const ticketRoot = join(artifactRoot, `iteration-${iteration}`, `issue-${issue.number}`);
  await mkdir(join(ticketRoot, "control"), { recursive: true });
  const branch = `${options.branchPrefix}${issue.number}`;
  await using sandbox = await createSandbox({ branch, baseBranch: baseSha, sandbox: noSandbox(), cwd: root });
  const execInWorktree = (command: string) => sandbox.exec(command);
  const top = await execInWorktree("git rev-parse --show-toplevel");
  if (top.exitCode !== 0) throw new Error(top.stderr || top.stdout);
  const worktreePath = top.stdout.trim();
  const templates = {
    implementer: await readFile(join(root, ".sandcastle", "implementer-prompt.md"), "utf8"),
    reviewer: await readFile(join(root, ".sandcastle", "reviewer-prompt.md"), "utf8"),
  };
  const acceptanceCriteria = extractAcceptanceCriteria(issue.body);
  const criteriaById = new Map(acceptanceCriteria.map((criterion: any) => [criterion.id, criterion]));
  let reviewState = acceptanceCriteria.map((criterion: any) => ({ ...criterion, status: "pending" }));
  const usedSessionIds = new Set<string>();
  let round = 1;
  let candidateHead = baseSha;
  let reviewerFindings: string[] = [];
  let focusedTestEvidence = "";
  let lastImplementerSessionId = "";

  const writeReviewState = async (phase: string) => {
    await writeFile(join(ticketRoot, "review-state.json"), JSON.stringify({ phase, criteria: reviewState }, null, 2));
  };

  const runReviewer = async ({ mode, criteria, artifacts }: any) => {
    const finalMode = mode === "final";
    const promptPath = finalMode ? artifacts.finalReviewerPromptPath : artifacts.reviewerPromptPath;
    const schemaPath = finalMode ? artifacts.finalReviewerSchemaPath : artifacts.reviewerSchemaPath;
    const receiptPath = finalMode ? artifacts.finalReviewerReceiptPath : artifacts.reviewerReceiptPath;
    const panePath = finalMode ? artifacts.finalReviewerPanePath : artifacts.reviewerPanePath;
    const passed = reviewState.filter((criterion: any) => criterion.status === "passed");
    await writeFile(promptPath, fillTemplate(templates.reviewer, {
      ISSUE_NUMBER: issue.number,
      ISSUE_TITLE: issue.title,
      ISSUE_BODY: issue.body,
      BASE_SHA: baseSha,
      CANDIDATE_HEAD: candidateHead,
      REVIEW_MODE: mode,
      ALL_CRITERIA: formatAcceptanceCriteria(acceptanceCriteria),
      CRITERIA_TO_REVIEW: formatAcceptanceCriteria(criteria),
      PASSED_CRITERIA: finalMode
        ? "(not applicable; final review re-evaluates all criteria from scratch)"
        : (passed.length ? formatAcceptanceCriteria(passed) : "(none yet)"),
      TEST_EVIDENCE: focusedTestEvidence,
    }));
    await copyFile(join(root, ".sandcastle", "reviewer-schema.json"), schemaPath);
    const reviewer = await runPhase({
      phase: finalMode ? "final-reviewer" : "reviewer",
      ticket: issue.number,
      worktreePath,
      promptPath,
      schemaPath,
      receiptPath,
      paneEvidencePath: panePath,
      config: options.reviewer,
      providerEnvName,
    });
    validateReviewerReceipt(reviewer.receipt, {
      reviewedHead: candidateHead,
      implementerSessionId: lastImplementerSessionId,
      reviewMode: mode,
      expectedCriteriaIds: criteria.map((criterion: any) => criterion.id),
    });
    if (usedSessionIds.has(reviewer.receipt.session_id)) throw new Error("reviewer session reused");
    usedSessionIds.add(reviewer.receipt.session_id);
    const headAfter = await execInWorktree("git rev-parse HEAD");
    if (headAfter.stdout.trim() !== candidateHead) throw new Error("reviewer changed Git HEAD");
    const dirty = await execInWorktree("git status --short --untracked-files=no");
    if (dirty.stdout.trim()) throw new Error(`reviewer left tracked changes:\n${dirty.stdout}`);
    if (reviewer.receipt.verdict === "blocked") throw new Error(`reviewer blocked: ${reviewer.receipt.blocker}`);
    reviewState = updateReviewState(reviewState, reviewer.receipt);
    await writeReviewState(finalMode ? "final-review" : "criteria-review");
    return reviewer.receipt;
  };

  await writeReviewState("initial");

  while (true) {
    const artifacts = roundArtifactPaths(ticketRoot, round);
    const previousHead = candidateHead;
    await writeFile(artifacts.implementerPromptPath, fillTemplate(templates.implementer, {
      ISSUE_NUMBER: issue.number, ISSUE_TITLE: issue.title, ISSUE_BODY: issue.body,
      BASE_SHA: baseSha, BRANCH: branch,
      ROUND_CONTEXT: buildImplementerRoundContext({ round, currentHead: candidateHead, reviewerFindings, focusedTestEvidence }),
    }));
    await copyFile(join(root, ".sandcastle", "implementer-schema.json"), artifacts.implementerSchemaPath);
    const implementer = await runPhase({
      phase: "implementer", ticket: issue.number, worktreePath,
      promptPath: artifacts.implementerPromptPath, schemaPath: artifacts.implementerSchemaPath,
      receiptPath: artifacts.implementerReceiptPath, paneEvidencePath: artifacts.implementerPanePath,
      config: options.implementer, providerEnvName,
    });
    const head = await execInWorktree("git rev-parse HEAD");
    if (head.exitCode !== 0) throw new Error(head.stderr);
    candidateHead = head.stdout.trim();
    validateImplementerReceipt(implementer.receipt, { issueNumber: issue.number, head: candidateHead });
    if (usedSessionIds.has(implementer.receipt.session_id)) throw new Error("implementer session reused");
    usedSessionIds.add(implementer.receipt.session_id);
    lastImplementerSessionId = implementer.receipt.session_id;
    if (candidateHead === previousHead) throw new Error("implementer did not create a new candidate commit");

    const focused = await execInWorktree(options.focusedTest);
    focusedTestEvidence = `${focused.stdout}\n${focused.stderr}`;
    await writeFile(artifacts.focusedTestPath, focusedTestEvidence);
    if (focused.exitCode !== 0) throw new Error("focused implementation gate failed");

    const pendingCriteria = reviewState.filter((criterion: any) => criterion.status === "pending");
    const criteriaReceipt = await runReviewer({ mode: "criteria", criteria: pendingCriteria, artifacts });
    if (criteriaReceipt.verdict === "changes_requested") {
      reviewerFindings = failedCriterionFindings(criteriaReceipt, criteriaById);
      round += 1;
      continue;
    }

    const finalReceipt = await runReviewer({ mode: "final", criteria: acceptanceCriteria, artifacts });
    if (finalReceipt.verdict === "approved") break;
    reviewerFindings = failedCriterionFindings(finalReceipt, criteriaById);
    round += 1;
  }

  const final = await execInWorktree(options.finalTest);
  await writeFile(join(ticketRoot, "final-test.txt"), `${final.stdout}\n${final.stderr}`);
  if (final.exitCode !== 0) throw new Error("final acceptance gate failed");
  await writeFile(join(ticketRoot, "result.json"), JSON.stringify({
    status: "reviewed-local-candidate",
    issue: issue.number,
    branch,
    head: candidateHead,
    rounds: round,
    acceptanceCriteria: reviewState,
  }, null, 2));
  return { issue, branch, head: candidateHead, ticketRoot };
}

async function runMerger(candidates: Candidate[], iteration: number, providerEnvName?: string) {
  if (!candidates.length) return undefined;
  const mergerRoot = join(artifactRoot, `iteration-${iteration}`, "merger");
  await mkdir(join(mergerRoot, "control"), { recursive: true });
  const targetBranch = await requireOk("git", ["branch", "--show-current"]);
  const candidatesText = candidates.map((c) => `- issue #${c.issue.number}: branch ${c.branch}, reviewed HEAD ${c.head}`).join("\n");
  const promptPath = join(mergerRoot, "control", "merger.md");
  const schemaPath = join(mergerRoot, "control", "merger-schema.json");
  const receiptPath = join(mergerRoot, "merger.json");
  const panePath = join(mergerRoot, "merger-pane.txt");
  const template = await readFile(join(root, ".sandcastle", "merger-prompt.md"), "utf8");
  await writeFile(promptPath, fillTemplate(template, {
    TARGET_BRANCH: targetBranch, CANDIDATES: candidatesText, INTEGRATION_TEST: options.integrationTest,
  }));
  await copyFile(join(root, ".sandcastle", "merger-schema.json"), schemaPath);
  const merger = await runPhase({
    phase: "merger", ticket: undefined, worktreePath: root, promptPath, schemaPath,
    receiptPath, paneEvidencePath: panePath, config: options.merger, providerEnvName,
  });
  validateMergerReceipt(merger.receipt, candidates.map((c) => c.issue.number));
  const currentHead = await requireOk("git", ["rev-parse", "HEAD"]);
  if (currentHead !== merger.receipt.final_head) throw new Error("merger final HEAD does not match repository HEAD");
  const integration = await run("/bin/sh", ["-c", options.integrationTest], root);
  await writeFile(join(mergerRoot, "controller-integration-test.txt"), `${integration.stdout}\n${integration.stderr}`);
  if (integration.exitCode !== 0) throw new Error("controller integration verification failed after merger");
  return merger.receipt;
}

async function preflight(providerEnvName?: string) {
  const required = ["git", "gh", "herdr", "codex", "unsnooze", "npm"];
  for (const command of required) await requireOk("sh", ["-c", `command -v ${shellQuote(command)}`]);
  if (process.env.HERDR_ENV !== "1") throw new Error("workflow must run inside Herdr");
  if (providerEnvName && !process.env[providerEnvName]) throw new Error(`required Codex provider variable ${providerEnvName} is absent`);
  const issues = await resolveIssues();
  const ready = selectReadyIssues(issues, { overrideNumber: options.issueOverride, maxParallel: options.maxParallel });
  console.log(JSON.stringify({ status: "preflight-ok", runId, routing: {
    implementer: options.implementer, reviewer: options.reviewer, merger: options.merger,
  }, maxParallel: options.maxParallel, readyIssues: ready.map((i) => i.number) }, null, 2));
}

async function acquireLock() {
  try {
    await mkdir(lockDir);
    await writeFile(join(lockDir, "owner.json"), JSON.stringify({ runId, pid: process.pid, startedAt: new Date().toISOString() }, null, 2));
  } catch (error: any) {
    if (error.code === "EEXIST") throw new Error("another Ralph orchestrator already owns this repository");
    throw error;
  }
}

let ownsLock = false;

async function main() {
  const providerEnvName = await readCodexProviderEnvName();
  if (options.dryRun) return preflight(providerEnvName);
  await acquireLock();
  ownsLock = true;
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(join(artifactRoot, "run.json"), JSON.stringify({ runId, pid: process.pid, options, startedAt: new Date().toISOString() }, null, 2));
  let iteration = 1;
  let completedIterations = 0;
  try {
    while (true) {
      const issues = await resolveIssues();
      const ready = selectReadyIssues(issues, { overrideNumber: options.issueOverride, maxParallel: options.maxParallel });
      if (!ready.length) break;
      const baseSha = await requireOk("git", ["rev-parse", options.baseSha]);
      const iterationRoot = join(artifactRoot, `iteration-${iteration}`);
      await mkdir(iterationRoot, { recursive: true });
      await writeFile(join(iterationRoot, "plan.json"), JSON.stringify({ iteration, baseSha, issues: ready.map((i) => i.number) }, null, 2));

      const settled = await Promise.allSettled(ready.map((issue) => runTicket(issue, iteration, baseSha, providerEnvName)));
      const summary = summarizeSettled(ready, settled);
      const candidates = successfulCandidates(summary) as Candidate[];
      for (const entry of summary) {
        if (entry.outcome.status === "rejected") {
          console.error(`issue #${entry.issue.number} failed this iteration: ${entry.outcome.reason}`);
        }
      }
      await writeFile(join(iterationRoot, "settled.json"), JSON.stringify(summary.map((entry: any) => ({
        issue: entry.issue.number,
        status: entry.outcome.status,
        ...(entry.outcome.status === "rejected" ? { error: String(entry.outcome.reason) } : { head: (entry.outcome.value as Candidate).head }),
      })), null, 2));
      await runMerger(candidates, iteration, providerEnvName);
      completedIterations += 1;
      if (options.issueOverride !== undefined) break;
      iteration += 1;
    }
    console.log(JSON.stringify({ status: "complete", runId, iterations: completedIterations }, null, 2));
  } finally {
    if (ownsLock) {
      await rm(lockDir, { recursive: true, force: true });
      ownsLock = false;
    }
  }
}

main().catch(async (error) => {
  console.error(String(error));
  if (ownsLock) {
    await rm(lockDir, { recursive: true, force: true }).catch(() => {});
    ownsLock = false;
  }
  process.exitCode = 1;
});
