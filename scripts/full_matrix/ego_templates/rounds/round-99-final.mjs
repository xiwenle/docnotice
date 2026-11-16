// round-99-final: only closes the shared TaskSpace after every prior round is PASS.

import {
  OUT, caseRow, readJson, recordCase, task, writeJson, writeResult,
} from "../lib/core.mjs";

const FINAL_ROUND = "round-99-final";
const ROUND_ORDER = [
  "round-00-smoke",
  "round-01-roles",
  "round-02-documents",
  "round-03-notices",
  "round-04-security",
  "round-05-destructive",
  "round-06-attachment-recycle",
  "round-07-notice-search",
  "round-08-admin-vocab",
  "round-09-validity-files",
  "round-10-notice-files",
  "round-11-backup-audit",
];
const cases = [];
const pages = [];
let allPass = true;
const roundSummaries = [];

try {
  for (const roundId of ROUND_ORDER) {
    const result = await readJson(`${OUT}/ego/results/${roundId}/result.json`, null);
    const casesInRound = result?.cases || [];
    const statuses = casesInRound.map(item => item.status);
    const pass = !!result && casesInRound.length > 0 && statuses.every(status => status === "PASS");
    if (!pass) allPass = false;
    roundSummaries.push({ roundId, resultPresent: !!result, cases: casesInRound.length, statuses, pass });
  }
  recordCase(cases, caseRow({
    id: "EGO-FINAL-ALL-ROUNDS-PASS",
    phase: FINAL_ROUND,
    owner: "H",
    role: "system",
    mutability: "ENV_EXCLUSIVE",
    precondition: "rounds 00 through 07 have produced result.json",
    expected: ["all required rounds exist", "every case status is PASS"],
    actual: { rounds: roundSummaries, allPass },
    status: allPass ? "PASS" : "BLOCKED",
    severity: allPass ? "NONE" : "P0",
    evidence: roundSummaries.map(item => ({ kind: "json", path: `${OUT}/ego/results/${item.roundId}/result.json` })),
    repro: { command: "python -m scripts.full_matrix.run ego round-99-final" },
  }));
  await writeJson(`${OUT}/ego/results/${FINAL_ROUND}/round-summary.json`, { allPass, rounds: roundSummaries });
  await writeResult(FINAL_ROUND, cases, pages, { allPass });
  if (allPass) await task.finish({ keep: [] });
} catch (error) {
  recordCase(cases, caseRow({
    id: "EGO-FINAL-RUNNER",
    phase: FINAL_ROUND,
    owner: "H",
    role: "system",
    mutability: "ENV_EXCLUSIVE",
    precondition: "ego runtime and prior result files",
    expected: ["final gate completes"],
    actual: { error: String(error), stack: error?.stack || null },
    status: "ERROR",
    severity: "P0",
    repro: { command: "python -m scripts.full_matrix.run ego round-99-final" },
  }));
  await writeResult(FINAL_ROUND, cases, pages, { allPass: false });
  process.exitCode = 1;
}