\* SPDX-License-Identifier: Apache-2.0
\* Copyright 2026 Salah Eddine Makboul
------------------------------ MODULE Call4Agents ------------------------------
(***************************************************************************)
(* A model of what call4agents does (main after Fix 17) when workers call   *)
(* the same @c4a.effect, a person approves and denies, processes crash, and *)
(* the outside world is asked (reconcile=) or deduplicates (idempotent_for).*)
(*                                                                         *)
(* It models the CODE (call4agents/__init__.py decide_run, record_result,   *)
(* failed, wrapper; call4agents/ledger.py claim, claim_approved, give_up,   *)
(* next_attempt; call4agents/owner.py may_still_run), not the README.       *)
(* Every simplification is listed in docs/FORMAL_MODEL.md.                  *)
(*                                                                         *)
(* One step of the model = one SQLite statement or transaction of the code. *)
(* Process memory (pc, att, iter, pick) is lost on a crash; log (the run    *)
(* record) and world (the outside system) are never lost.                   *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    NW,            \* number of workers: 2 or 3
    ARG3,          \* arguments of worker 3: "b" (other arguments) or "a" (a second named action, same arguments)
    MAX_CALLS,     \* calls of workers 1 and 2; a crashed worker that comes back uses its next call
    MAX_CALLS3,    \* calls of worker 3 (kept small: the state space grows fast)
    MAX_YES,       \* how many "yes" the person may record
    MAX_NO,        \* how many "no" the person may record
    APPROVAL,      \* TRUE: @c4a.effect(approval=True) (claim_approved); FALSE: plain claim
    MODE,          \* "plain" (no reconcile, no idempotent_for), "reconcile", or "idem" (idempotent_for)
    RETRY_ON,      \* TRUE: retry_on= is set (an error that proves nothing happened)
    CRASHES,       \* TRUE: workers may crash at any step
    EFFECT_FAILS,  \* TRUE: the tool may raise, before or after its effect happened
    OWNER,         \* "processes": each worker is its own process; "threads": all workers are threads of ONE process
    START_EARLY,   \* TRUE: a proposed change: the call is marked "in progress" before its claim (closes a window)
    BUGS           \* set of seeded bugs (see the end of the file); {} for the code as it is

Bug(x) == x \in BUGS

Workers == 1..NW
KeyOf(w) == IF w = 3 THEN "k2" ELSE "k1"           \* workers 1 and 2 make the SAME call
Keys == {KeyOf(w) : w \in Workers}
ArgsOfKey(k) == IF k = "k2" THEN ARG3 ELSE "a"
ArgsOf(w) == ArgsOfKey(KeyOf(w))
ArgSet == {ArgsOf(w) : w \in Workers}
CallLimit(w) == IF w = 3 THEN MAX_CALLS3 ELSE MAX_CALLS

VARIABLES
    log,     \* the run record: a sequence of entries, append-only in the code
    world,   \* outside world: how many times each action really happened
    pc,      \* where each worker is (process memory)
    att,     \* the attempt number the worker computed (process memory)
    iter,    \* which pass of `for _ in range(2)` (process memory)
    pick,    \* split-admission bug only: the "yes" chosen in the check step
    calls,   \* calls used per worker (a bound for the model checker)
    ran,     \* ghost: set of <<key, attempt>> whose tool function was entered
    yes, no  \* "yes" / "no" answers the person has given

vars == <<log, world, pc, att, iter, pick, calls, ran, yes, no>>

E(kind, key, args, n) == [kind |-> kind, key |-> key, args |-> args, n |-> n]
\* n: the attempt (tool_intent, tool_done, tool_error, tool_abandoned, tool_retry_same_key, late_result;
\*    0 on a reconciled tool_done) or the seq of the approval (approval_used, effect_approved).

Pos(kind, key) == {i \in 1..Len(log) : log[i].kind = kind /\ log[i].key = key}
Has(kind, key) == Pos(kind, key) # {}
HasN(kind, key, n) == \E i \in Pos(kind, key) : log[i].n = n
Count(kind, key) == Cardinality(Pos(kind, key))
Max(S) == IF S = {} THEN 0 ELSE CHOOSE x \in S : \A y \in S : x >= y
Min(S) == CHOOSE x \in S : \A y \in S : x <= y
Matching(kind, a) == {i \in 1..Len(log) : log[i].kind = kind /\ log[i].args = a}   \* HMAC match = same args
Used(s) == \E i \in 1..Len(log) : log[i].kind = "approval_used" /\ log[i].n = s
Closed(k) == Pos("tool_abandoned", k) \cup Pos("tool_retry_same_key", k)
\* ledger.next_attempt: newest closed attempt + 1.  Seeded give_up_twice: 0.7.1 counted the rows instead.
NextAttempt(k) == IF Bug("give_up_twice") THEN Cardinality(Closed(k)) + 1
                  ELSE Max({log[i].n : i \in Closed(k)}) + 1

PcSet == {"idle", "readDone", "readAttempt", "claim", "claimWrite", "claimNext", "claimWriteNext",
          "claimed", "effect", "recordDone", "checkLate", "failOther", "failRetry", "readErr", "wait",
          "ownerCheck", "afterWait", "reconcile", "reconDone", "abandon"}
\* _owner.started(...) .. _owner.finished(...) in wrapper(): the call is "in progress" in its process
InProgress == {"effect", "recordDone", "checkLate", "failOther", "failRetry"}

Init ==
    /\ log = <<>>
    /\ world = [k \in Keys |-> 0]
    /\ pc = [w \in Workers |-> "idle"]
    /\ att = [w \in Workers |-> 0]
    /\ iter = [w \in Workers |-> 0]
    /\ pick = [w \in Workers |-> 0]
    /\ calls = [w \in Workers |-> 0]
    /\ ran = {}
    /\ yes = 0
    /\ no = 0

\* ---------------------------------------------------------------- helpers for process memory
SetW(w, p, a, i, s) ==
    /\ pc' = [pc EXCEPT ![w] = p]
    /\ att' = [att EXCEPT ![w] = a]
    /\ iter' = [iter EXCEPT ![w] = i]
    /\ pick' = [pick EXCEPT ![w] = s]
Idle(w) == SetW(w, "idle", 0, 0, 0)                      \* the call returned or raised
Go(w, p) == pc' = [pc EXCEPT ![w] = p] /\ UNCHANGED <<att, iter, pick>>
Fail(w, p, n) == IF p = "idle" THEN Idle(w) ELSE SetW(w, p, n, iter[w], 0)
\* the next pass of `for _ in range(2)`, or EffectInDoubt after the second pass
NextPass(w) == IF iter[w] = 0 THEN SetW(w, "readDone", 0, 1, 0) ELSE Idle(w)

\* ---------------------------------------------------------------- the owner check (owner.may_still_run)
\* Abstraction: the check may say "gone" only if the worker that wrote this attempt's intent no longer
\* holds it (it crashed, or its call ended). The code says "gone" in FEWER cases (a live pid or another
\* host counts as running until `lease` seconds); we assume no attempt runs longer than `lease`.
\* "processes": a live process holds its attempt from the claim on (the pid is alive).
\* "threads":   same process, so only _owner's in-progress set counts, which starts AFTER check() returned.
Holds(v, k, n) ==
    /\ KeyOf(v) = k /\ att[v] = n
    /\ pc[v] \in (IF OWNER = "processes" \/ START_EARLY THEN InProgress \cup {"claimed"} ELSE InProgress)
OwnerGone(w) == ~\E v \in Workers \ {w} : Holds(v, KeyOf(w), att[w])

\* ---------------------------------------------------------------- ledger.claim_approved
\* The decision the transaction takes, read from the record as it is now.
\* r: "true" (owns it), "false" (already claimed or done), "none" (needs approval). s: the "yes" to spend (0: reuse).
Decide(k, a, n) ==
    LET spent == Max(Pos("effect_approved", k))
        denied == Max(Matching("denial", a))
        approvals == Matching("approval", a)
        free == {s \in approvals : s > denied /\ (Bug("no_spend") \/ ~Used(s))}
    IN IF Has("tool_done", k) THEN [r |-> "false", s |-> 0]
       ELSE IF spent > 0 /\ ((~Bug("no_deny_in_doubt") /\ denied > spent)   \* spent = seq of effect_approved
                             \/ log[spent].n \notin approvals)
            THEN [r |-> "none", s |-> 0]
       ELSE IF HasN("tool_intent", k, n) THEN [r |-> "false", s |-> 0]
       ELSE IF spent > 0 THEN [r |-> "true", s |-> 0]                \* reuse the spent yes (a resume)
       ELSE IF free = {} THEN [r |-> "none", s |-> 0]
       ELSE [r |-> "true", s |-> Min(free)]
AdmitEntries(k, a, n, s) ==
    IF s = 0 THEN <<E("tool_intent", k, a, n)>>
    ELSE <<E("approval_used", k, a, s), E("effect_approved", k, a, s), E("tool_intent", k, a, n)>>

\* __init__.py claim(); ledger.claim (plain: the UNIQUE index one_intent)
TryClaim(w, n, falsePc, splitPc) ==
    LET k == KeyOf(w)
        a == ArgsOf(w)
        d == Decide(k, a, n)
    IN \/ /\ ~APPROVAL
          /\ IF ~Bug("no_unique_intent") /\ HasN("tool_intent", k, n)
             THEN Fail(w, falsePc, n) /\ UNCHANGED log
             ELSE log' = Append(log, E("tool_intent", k, a, n)) /\ SetW(w, "claimed", n, iter[w], 0)
       \/ /\ APPROVAL /\ Bug("split_admission")                 \* seeded: check, then write later
          /\ UNCHANGED log
          /\ IF d.r = "true" THEN SetW(w, splitPc, n, iter[w], d.s)
             ELSE IF d.r = "false" THEN Fail(w, falsePc, n) ELSE Idle(w)
       \/ /\ APPROVAL /\ ~Bug("split_admission")                \* one transaction (BEGIN IMMEDIATE)
          /\ IF d.r = "true"
             THEN log' = log \o AdmitEntries(k, a, n, d.s) /\ SetW(w, "claimed", n, iter[w], 0)
             ELSE IF d.r = "false" THEN Fail(w, falsePc, n) /\ UNCHANGED log
             ELSE Idle(w) /\ UNCHANGED log                           \* ApprovalRequired

\* seeded split_admission only: the write step; the UNIQUE indexes still refuse a duplicate row
ClaimWriteStep(w, falsePc) ==
    LET k == KeyOf(w)
        a == ArgsOf(w)
    IN IF HasN("tool_intent", k, att[w]) \/ (pick[w] > 0 /\ Used(pick[w]))
       THEN Fail(w, falsePc, att[w]) /\ UNCHANGED log
       ELSE log' = log \o AdmitEntries(k, a, att[w], pick[w]) /\ SetW(w, "claimed", att[w], iter[w], 0)

\* The outside system. With idempotent_for the request carries the effect key (the same on every attempt)
\* and the provider applies a key once.
Apply(k) == world' = [world EXCEPT ![k] = IF MODE = "idem" /\ ~Bug("provider_forgets") /\ @ >= 1 THEN @ ELSE @ + 1]

\* ledger.give_up: close attempt n ONCE, in one transaction. Seeded give_up_twice: 0.7.1's plain append.
GiveUpOK(k, n) ==
    \/ Bug("give_up_twice")
    \/ /\ ~\E i \in Closed(k) : log[i].n = n
       /\ ~HasN("tool_intent", k, n + 1)
CloseLog(kind, k, a, n) ==
    IF Bug("delete_on_abandon")     \* seeded: "clean up" the stale intent instead of appending
    THEN log' = SelectSeq(log, LAMBDA e : ~(e.kind = "tool_intent" /\ e.key = k /\ e.n = n))
    ELSE log' = Append(log, E(kind, k, a, n))

\* ---------------------------------------------------------------- worker steps
Start(w) ==                                                   \* wrapper(): a new call
    /\ pc[w] = "idle" /\ calls[w] < CallLimit(w)
    /\ calls' = [calls EXCEPT ![w] = @ + 1]
    /\ Go(w, "readDone")
    /\ UNCHANGED <<log, world, ran, yes, no>>

ReadDone(w) ==                                                \* a recorded result is replayed
    /\ pc[w] = "readDone"
    /\ IF Has("tool_done", KeyOf(w)) THEN Idle(w) ELSE Go(w, "readAttempt")
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

ReadAttempt(w) ==                                             \* attempt = lg.next_attempt(k)
    /\ pc[w] = "readAttempt"
    /\ SetW(w, "claim", NextAttempt(KeyOf(w)), iter[w], 0)
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

Claim(w) ==
    /\ pc[w] = "claim"
    /\ TryClaim(w, att[w], "readErr", "claimWrite")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

ClaimWrite(w) ==
    /\ pc[w] = "claimWrite"
    /\ ClaimWriteStep(w, "readErr")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

Begin(w) ==                                                   \* check() returned; _owner.started(running)
    /\ pc[w] = "claimed"
    /\ Go(w, "effect")
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

Effect(w) ==                                                  \* f(*args, **kwargs)
    LET k == KeyOf(w) IN
    /\ pc[w] = "effect"
    /\ ran' = ran \cup {<<k, att[w]>>}
    /\ \/ Apply(k) /\ Go(w, "recordDone")                                  \* returns
       \/ EFFECT_FAILS /\ Apply(k) /\ Go(w, "failOther")                   \* raises AFTER it happened
       \/ EFFECT_FAILS /\ RETRY_ON /\ Bug("retry_on_lies") /\ Apply(k) /\ Go(w, "failRetry")
       \/ EFFECT_FAILS /\ UNCHANGED world /\ Go(w, "failOther")            \* raises before it happened
       \/ EFFECT_FAILS /\ RETRY_ON /\ UNCHANGED world /\ Go(w, "failRetry")  \* a retry_on error: nothing happened
    /\ UNCHANGED <<log, calls, yes, no>>

RecordDone(w) ==                                              \* record_result: tool_done
    /\ pc[w] = "recordDone"
    /\ log' = Append(log, E("tool_done", KeyOf(w), ArgsOf(w), att[w]))
    /\ Go(w, "checkLate")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

CheckLate(w) ==                                               \* record_result: late_result (read + append merged)
    /\ pc[w] = "checkLate"
    /\ IF HasN("tool_abandoned", KeyOf(w), att[w])
       THEN log' = Append(log, E("late_result", KeyOf(w), ArgsOf(w), att[w]))
       ELSE UNCHANGED log
    /\ Idle(w)                                                \* _owner.finished(running), the call returns
    /\ UNCHANGED <<world, ran, calls, yes, no>>

FailOther(w) ==                                               \* failed(): tool_error, the call raises
    /\ pc[w] = "failOther"
    /\ log' = Append(log, E("tool_error", KeyOf(w), ArgsOf(w), att[w]))
    /\ Idle(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

FailRetry(w) ==                                               \* failed(): retry_on -> tool_abandoned (plain append)
    /\ pc[w] = "failRetry"
    /\ CloseLog("tool_abandoned", KeyOf(w), ArgsOf(w), att[w])
    /\ Idle(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

ReadErr(w) ==                     \* failed = an error of THIS attempt: no wait and no owner check
    LET e == Max(Pos("tool_error", KeyOf(w)))
        failed == e > 0 /\ log[e].n = att[w]
    IN /\ pc[w] = "readErr"
       /\ Go(w, IF failed THEN "afterWait" ELSE "wait")
       /\ UNCHANGED <<log, world, ran, calls, yes, no>>

Wait(w) ==                                                    \* poll for the result until the deadline
    /\ pc[w] = "wait"
    /\ \/ Has("tool_done", KeyOf(w)) /\ Idle(w)                    \* the poll sees the result: replay
       \/ ~Bug("no_timeout") /\ Go(w, IF MODE = "plain" THEN "afterWait" ELSE "ownerCheck")
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

OwnerCheck(w) ==                                              \* Fix 17: busy -> EffectInDoubt
    /\ pc[w] = "ownerCheck"
    /\ IF OwnerGone(w) \/ Bug("reconcile_live_owner") THEN Go(w, "afterWait") ELSE Idle(w)
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

AfterWait(w) ==
    LET k == KeyOf(w) IN
    /\ pc[w] = "afterWait"
    /\ CASE MODE = "plain" -> Idle(w) /\ UNCHANGED log                 \* EffectInDoubt
         [] MODE = "reconcile" -> Go(w, "reconcile") /\ UNCHANGED log
         [] MODE = "idem" ->
              \/ Idle(w) /\ UNCHANGED log                                   \* outside the window: EffectInDoubt
              \/ /\ GiveUpOK(k, att[w])                                     \* give_up(.., "tool_retry_same_key")
                 /\ CloseLog("tool_retry_same_key", k, ArgsOf(w), att[w])
                 /\ Go(w, "claimNext")
              \/ ~GiveUpOK(k, att[w]) /\ NextPass(w) /\ UNCHANGED log       \* `continue`
    /\ UNCHANGED <<world, ran, calls, yes, no>>

ClaimNext(w) ==                                               \* claim(attempt + 1); False -> EffectInDoubt
    /\ pc[w] = "claimNext"
    /\ TryClaim(w, att[w] + 1, "idle", "claimWriteNext")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

ClaimWriteNext(w) ==
    /\ pc[w] = "claimWriteNext"
    /\ ClaimWriteStep(w, "idle")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

Reconcile(w) ==                                               \* found = reconcile(**call_args)
    LET happened == world[KeyOf(w)] >= 1 IN
    /\ pc[w] = "reconcile"
    /\ \/ Idle(w)                                                      \* c4a.UNKNOWN: stays in doubt
       \/ (happened \/ Bug("reconcile_lies_found")) /\ Go(w, "reconDone")   \* returns the result
       \/ (~happened \/ Bug("reconcile_lies_none")) /\ Go(w, "abandon")     \* returns None
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

ReconDone(w) ==                                               \* tool_done (reconciled), replay
    /\ pc[w] = "reconDone"
    /\ log' = Append(log, E("tool_done", KeyOf(w), ArgsOf(w), 0))
    /\ Idle(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

Abandon(w) ==                                 \* lg.give_up(..); its result is not used; then the next pass
    /\ pc[w] = "abandon"
    /\ IF GiveUpOK(KeyOf(w), att[w])
       THEN CloseLog("tool_abandoned", KeyOf(w), ArgsOf(w), att[w])
       ELSE UNCHANGED log
    /\ NextPass(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

\* A crash: memory lost, the record and the world stay. Threads of one process die together.
Crash(w) ==
    /\ CRASHES /\ pc[w] # "idle"
    /\ IF OWNER = "threads"
       THEN /\ pc' = [v \in Workers |-> "idle"] /\ att' = [v \in Workers |-> 0]
            /\ iter' = [v \in Workers |-> 0] /\ pick' = [v \in Workers |-> 0]
       ELSE Idle(w)
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

Progress(w) ==
    \/ Start(w) \/ ReadDone(w) \/ ReadAttempt(w) \/ Claim(w) \/ ClaimWrite(w) \/ Begin(w)
    \/ Effect(w) \/ RecordDone(w) \/ CheckLate(w) \/ FailOther(w) \/ FailRetry(w) \/ ReadErr(w)
    \/ Wait(w) \/ OwnerCheck(w) \/ AfterWait(w) \/ ClaimNext(w) \/ ClaimWriteNext(w) \/ Reconcile(w)
    \/ ReconDone(w) \/ Abandon(w)

\* ---------------------------------------------------------------- the person (c4a.approve / c4a.deny)
Approve(a) ==                                                 \* ledger.record_approval
    /\ APPROVAL /\ yes < MAX_YES
    /\ log' = Append(log, E("approval", "-", a, 0))
    /\ yes' = yes + 1
    /\ UNCHANGED <<world, pc, att, iter, pick, calls, ran, no>>

Deny(a) ==                                                    \* ledger.record_denial
    /\ APPROVAL /\ no < MAX_NO
    /\ log' = Append(log, E("denial", "-", a, 0))
    /\ no' = no + 1
    /\ UNCHANGED <<world, pc, att, iter, pick, calls, ran, yes>>

Next ==
    \/ \E w \in Workers : Progress(w) \/ Crash(w)
    \/ \E a \in ArgSet : Approve(a) \/ Deny(a)

Spec == Init /\ [][Next]_vars

\* For the progress properties only: workers do not stop forever (weak fairness).
\* Crashes and the person are not fair: they may happen or not.
LiveSpec == Spec /\ \A w \in Workers : WF_vars(Progress(w))

\* ---------------------------------------------------------------- properties
TypeOK ==
    /\ pc \in [Workers -> PcSet]
    /\ world \in [Keys -> Nat]
    /\ \A w \in Workers : calls[w] \in 0..CallLimit(w)
    /\ ran \subseteq (Keys \X Nat)

\* The outside world sees each action at most once.
AtMostOnce == \A k \in Keys : world[k] <= 1

\* The tool function was entered for <<k, n>> only under an intent written by an admission that spent a
\* "yes" for exactly k's arguments on k, with no "no" for those arguments between that "yes" and the intent.
AdmittedAt(i) ==
    LET k == log[i].key
        a == log[i].args
    IN \E e \in 1..(i - 1) :
         /\ log[e].kind = "effect_approved" /\ log[e].key = k
         /\ LET s == log[e].n IN
              /\ s \in 1..(e - 1) /\ log[s].kind = "approval" /\ log[s].args = a
              /\ \E u \in 1..(e - 1) : log[u].kind = "approval_used" /\ log[u].n = s /\ log[u].key = k
              /\ ~\E j \in (s + 1)..(i - 1) : log[j].kind = "denial" /\ log[j].args = a
NoUnapprovedEffect ==
    APPROVAL => \A r \in ran : \E i \in 1..Len(log) :
                   /\ log[i].kind = "tool_intent" /\ log[i].key = r[1] /\ log[i].n = r[2]
                   /\ AdmittedAt(i)

\* Each "yes" is spent once, by one action.
OneYesOneAction ==
    \A s \in 1..Len(log) :
        /\ Cardinality({u \in 1..Len(log) : log[u].kind = "approval_used" /\ log[u].n = s}) <= 1
        /\ Cardinality({log[e].key : e \in {x \in 1..Len(log) : log[x].kind = "effect_approved" /\ log[x].n = s}}) <= 1

\* A recorded result means the action really happened.
DoneIsTrue == \A i \in 1..Len(log) : log[i].kind = "tool_done" => world[log[i].key] >= 1

\* At most one tool_done per action. Checked on its own: the code can write two (see FORMAL_MODEL.md).
DoneOnce == \A k \in Keys : Count("tool_done", k) <= 1

\* Entries are never changed or removed (an action property).
AppendOnly == [][Len(log') >= Len(log) /\ SubSeq(log', 1, Len(log)) = log]_log

\* Progress (only with LiveSpec): every call ends; no worker waits forever ...
AllCallsEnd == <>(\A w \in Workers : pc[w] = "idle" /\ calls[w] = CallLimit(w))
\* ... and, with no crash and a tool that returns, an admitted action gets its result.
AdmittedFinishes == \A k \in Keys : Has("tool_intent", k) ~> Has("tool_done", k)

\* Seeded bugs (BUGS), each a real mistake a check must catch:
\*   split_admission      the "yes" check and the intent write are two steps (no lock between them)
\*   no_spend             a used "yes" is not checked as spent (and no UNIQUE index on approval_used)
\*   no_deny_in_doubt     a "no" is not checked when an admitted action is resumed
\*   reconcile_lies_none  reconcile returns None although the action happened
\*   reconcile_lies_found reconcile returns a result although nothing happened
\*   retry_on_lies        an error listed in retry_on is raised AFTER the action happened
\*   provider_forgets     the outside API does not deduplicate the idempotency key (window too short)
\*   no_unique_intent     no UNIQUE index on tool_intent
\*   delete_on_abandon    abandon deletes the stale intent instead of appending tool_abandoned
\*   no_timeout           `wait` never ends while the action has no result (progress)
\*   reconcile_live_owner 0.7.1: no owner check before reconcile / same-key resend
\*   give_up_twice        0.7.1: abandon is a plain append and the next attempt counts rows
================================================================================
