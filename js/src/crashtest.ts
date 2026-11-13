// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Salah Eddine Makboul
//
// call4agents crashtest <script> - experimental. Kills your agent at every step, restarts it, and says
// what repeated. The same idea and output as the Python kit's crashtest.
//
// Your script (an ES module) is run in a new empty folder, once cleanly, then once per crash point: the
// process is killed just before and just after each action, and just before each write it saves to SQLite
// or to a file in that folder. Then it is started again the same way you would restart it. On a restart the
// environment has CRASHTEST_RESUME=1. Every action really runs, many times: point the script at test
// accounts. The command asks you to say so (--test-accounts), and it refuses to start, even then, when it
// sees a live payment key in the environment or in the script, or a payment provider set to live mode.

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { cpus, tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { actionsIn, fixHint, liveSigns } from "./crashtest-scan.js";

export { actionsIn, instrument, liveSigns } from "./crashtest-scan.js";


interface Ran {
  code: number | null;
  signal: string | null;
  stdout: string;
  stderr: string;
}

export interface CrashResult {
  point: number;
  at: string;
  reached: boolean;
  twice: string[];
  unapproved: string[];
  lost: string[];
  stopped: boolean;
  in_doubt: boolean;
  error: string;
}

export interface Report {
  error?: string;
  live?: string[];
  stderr?: string;
  watched?: string[];
  script?: string;
  actions?: Record<string, number>;
  crash_points?: number;
  tested?: number;
  reached?: number;
  repeats_on_clean_restart?: string[];
  results?: CrashResult[];
  fix?: string;
}

export interface CrashtestOptions {
  watch?: string[];
  args?: string[];
  maxCrashes?: number;
  timeout?: number;          // seconds per run
  workers?: number;
  env?: Record<string, string | undefined>;
}

function start(script: string, work: string, journal: string, crashAt: number, watched: Set<string>, args: string[],
  resume: boolean, timeout: number, env: Record<string, string | undefined>): Promise<Ran> {
  return new Promise((done) => {
    const child = spawn(process.execPath, ["--import", new URL("./crashtest-child.js", import.meta.url).href, script, ...args], {
      cwd: work,
      env: {
        ...env, CRASHTEST_RESUME: resume ? "1" : "", C4A_CRASHTEST_JOURNAL: journal, C4A_CRASHTEST_AT: String(crashAt),
        C4A_CRASHTEST_WATCH: [...watched].sort().join(","), C4A_CRASHTEST_SCRIPT: script,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (b) => (stdout += b));
    child.stderr.on("data", (b) => (stderr += b));
    const timer = setTimeout(() => child.kill("SIGKILL"), timeout * 1000);
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      done({ code, signal, stdout, stderr });
    });
  });
}

function readJournal(p: string): any[] {
  if (!existsSync(p)) return [];
  return readFileSync(p, "utf8").split("\n").filter((l) => l.trim()).map((l) => JSON.parse(l));
}

function calls(entries: any[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const e of entries) if ("start" in e) out.set(e.call, (out.get(e.call) ?? 0) + 1);
  return out;
}

function lastLine(text: string): string {
  const lines = text.trim().split("\n").map((l) => l.trim()).filter(Boolean);
  const named = lines.find((l) => /^[A-Za-z]*Error\b|^EffectInDoubt|^ApprovalRequired|^KeyConflict/.test(l));
  return (named ?? lines[lines.length - 1] ?? "").slice(0, 160);
}

async function oneCrash(script: string, point: number, watched: Set<string>, args: string[], timeout: number,
  baseline: Map<string, number>, env: Record<string, string | undefined>): Promise<CrashResult> {
  const work = mkdtempSync(join(tmpdir(), "c4a-crashtest-"));
  try {
    const first = join(work, ".first");
    const second = join(work, ".second");
    const killed = await start(script, work, first, point, watched, args, false, timeout, env);
    const resumed = await start(script, work, second, 0, watched, args, true, timeout, env);
    const a = readJournal(first);
    const b = readJournal(second);
    const at = a.find((e) => e.point === point)?.at ?? "?";
    const ran = calls([...a, ...b]);
    const done = new Map<string, number>();
    for (const e of [...a, ...b]) if ("done" in e) done.set(e.call, (done.get(e.call) ?? 0) + 1);
    const inDoubt = resumed.stderr.includes("EffectInDoubt");
    return {
      point, at, reached: a.some((e) => e.point === point) && killed.code !== 0,
      twice: [...ran].filter(([c, n]) => baseline.has(c) && n > baseline.get(c)!).map(([c]) => c).sort(),
      unapproved: [...ran.keys()].filter((c) => !baseline.has(c)).sort(),
      lost: inDoubt ? [] : [...baseline.keys()].filter((c) => !done.get(c)).sort(),
      stopped: resumed.code !== 0, in_doubt: inDoubt,
      error: resumed.code !== 0 ? lastLine(resumed.stderr) : "",
    };
  } finally {
    rmSync(work, { recursive: true, force: true, maxRetries: 5 });
  }
}

export function choosePoints(total: number, limit: number): number[] {
  if (total <= limit) return Array.from({ length: total }, (_, i) => i + 1);
  const s = new Set<number>();
  for (let i = 0; i < limit; i++) s.add(1 + Math.round((i * (total - 1)) / (limit - 1)));
  return [...s].sort((x, y) => x - y);
}

export async function crashtest(scriptPath: string, options: CrashtestOptions = {}): Promise<Report> {
  const script = resolve(scriptPath);
  const env = options.env ?? process.env;
  const source = readFileSync(script, "utf8");
  const live = liveSigns(source, env);
  if (live.length) {
    return { error: `refused: this looks like a live account (${live.join("; ")}). Every action would really run, `
      + "many times. Use test keys, a sandbox or fakes.", live };
  }
  const watched = new Set([...actionsIn(source), ...(options.watch ?? []).filter(Boolean)]);
  const args = options.args ?? [];
  const timeout = options.timeout ?? 120;
  const work = mkdtempSync(join(tmpdir(), "c4a-crashtest-"));
  let entries: any[];
  let repeatedClean: string[];
  try {
    const journal = join(work, ".clean");
    const clean = await start(script, work, journal, 0, watched, args, false, timeout, env);
    if (clean.code !== 0) return { error: "the script failed on a clean run", stderr: clean.stderr.slice(-1500) };
    entries = readJournal(journal);
    const again = join(work, ".again");
    await start(script, work, again, 0, watched, args, true, timeout, env);
    repeatedClean = [...calls(readJournal(again)).keys()].sort();
  } finally {
    rmSync(work, { recursive: true, force: true, maxRetries: 5 });
  }
  const baseline = calls(entries);
  const total = entries.filter((e) => "point" in e).length;
  if (!baseline.size) return { error: "no action was seen. Name one: --watch yourFunction", watched: [...watched].sort() };
  const points = choosePoints(total, options.maxCrashes ?? 200);
  const results: CrashResult[] = new Array(points.length);
  let next = 0;
  const workers = Math.max(1, options.workers ?? Math.min(4, cpus().length || 1));
  await Promise.all(Array.from({ length: workers }, async () => {
    while (next < points.length) {
      const i = next++;
      results[i] = await oneCrash(script, points[i], watched, args, timeout, baseline, env);
    }
  }));
  return {
    script, actions: Object.fromEntries(baseline), crash_points: total, tested: points.length,
    reached: results.filter((r) => r.reached).length, repeats_on_clean_restart: repeatedClean, results, fix: fixHint(source),
  };
}

export function problems(report: Report): Record<"twice" | "unapproved" | "lost", Record<string, string[]>> {
  const out = { twice: {}, unapproved: {}, lost: {} } as Record<"twice" | "unapproved" | "lost", Record<string, string[]>>;
  for (const r of report.results ?? []) {
    for (const kind of ["twice", "unapproved", "lost"] as const) {
      for (const call of r[kind]) (out[kind][call] ??= []).push(r.at);
    }
  }
  return out;
}

const USAGE = "usage: call4agents crashtest --demo | <agent script> --test-accounts [--watch name,name] [--max-crashes N] "
  + "[--json] [-- script args]";

async function demo(print: (s: string) => void): Promise<number> {
  const example = fileURLToPath(new URL("../examples/crashtest-agent.mjs", import.meta.url));
  print("A small agent refunds a customer. Without the guard:\n");
  const without = await main([example, "--test-accounts"], { print, env: { ...process.env, AGENT_GUARD: "0" } });
  print("\nThe same agent with the one line added:\n");
  const guarded = await main([example, "--test-accounts"], { print, env: { ...process.env, AGENT_GUARD: "1" } });
  return without === 1 && guarded === 0 ? 0 : 1;
}

/** The command. Returns the exit code: 0 nothing repeated, 1 something ran twice or unapproved, 2 refused or failed. */
export async function main(argv: string[], io: { print?: (s: string) => void; env?: Record<string, string | undefined> } = {}): Promise<number> {
  const print = io.print ?? ((s: string) => console.log(s));
  const env = io.env ?? process.env;
  const asJson = argv.includes("--json");
  let scriptArgs: string[] = [];
  if (argv.includes("--")) {
    scriptArgs = argv.slice(argv.indexOf("--") + 1);
    argv = argv.slice(0, argv.indexOf("--"));
  }
  const testAccounts = argv.includes("--test-accounts");
  const items = argv.filter((a) => a !== "--json" && a !== "--test-accounts");
  const rest: string[] = [];
  const watch: string[] = [];
  let maxCrashes = 200;
  for (let i = 0; i < items.length; i++) {
    if (items[i] === "--watch") watch.push(...(items[++i] ?? "").split(","));
    else if (items[i] === "--max-crashes") maxCrashes = Number(items[++i] ?? "200");
    else rest.push(items[i]);
  }
  if (rest.length === 1 && rest[0] === "--demo") return demo(print);
  if (rest.length !== 1 || !existsSync(rest[0])) {
    print(USAGE);
    return 2;
  }
  if (!testAccounts) {
    print("crashtest refused: every action of the script really runs, many times. Point it at fakes, sandboxes or "
      + "test accounts, then add --test-accounts.");
    return 2;
  }
  const report = await crashtest(rest[0], { watch, args: scriptArgs, maxCrashes, env });
  if (asJson) print(JSON.stringify(report, null, 1));
  if (report.error !== undefined) {
    if (!asJson) print(`crashtest stopped: ${report.error}\n${report.stderr ?? ""}`);
    return 2;
  }
  const found = problems(report);
  const bad = Object.keys(found.twice).length > 0 || Object.keys(found.unapproved).length > 0;
  if (asJson) return bad ? 1 : 0;
  const results = report.results!;
  print(`call4agents crashtest (experimental): ${basename(rest[0])}`);
  print(`Clean run: ${Object.values(report.actions!).reduce((a, b) => a + b, 0)} action(s), ${report.crash_points} crash points. `
    + `Killed and restarted ${report.reached} time(s).`);
  for (const [call, where] of Object.entries(found.twice)) {
    print(`\n  RAN TWICE       ${call}\n    in ${where.length} of ${report.tested} crashes, first when killed ${where[0]}`);
  }
  for (const [call, where] of Object.entries(found.unapproved)) {
    print(`\n  NOT APPROVED    ${call}\n    the clean run never made this call. It ran in ${where.length} of ${report.tested} `
      + `crashes, first when killed ${where[0]}`);
  }
  for (const [call, where] of Object.entries(found.lost)) {
    print(`\n  NEVER FINISHED  ${call}\n    in ${where.length} of ${report.tested} crashes, first when killed ${where[0]}`);
  }
  const doubt = results.filter((r) => r.in_doubt).length;
  const other = results.filter((r) => r.stopped && !r.in_doubt);
  if (doubt) print(`\n  ${doubt} restart(s) stopped as in doubt instead of repeating: a person or the provider settles those.`);
  if (other.length) print(`\n  ${other.length} restart(s) ended with an error, first: ${other[0].error}`);
  if (report.repeats_on_clean_restart!.length && bad) {
    print("\n  Note: starting it again after a clean finish also repeats its actions. If this script is meant to start "
      + "a new job each time, give it a fixed run id and saved state, or these results say little.");
  }
  if (bad) {
    print(`\nThe one-line fix:\n\n${report.fix}\n\nThen run this test again.`);
    return 1;
  }
  print("\nNo action ran twice and no unapproved call ran, at the points tested.");
  print("This covers this script, this path and these crash points. It is not a proof for other paths.");
  return 0;
}

