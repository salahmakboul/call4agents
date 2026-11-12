\* SPDX-License-Identifier: Apache-2.0
\* Copyright 2026 Salah Eddine Makboul
------------------------------ MODULE Call4Agents ------------------------------
(***************************************************************************)
(* A model of what call4agents 0.7.1 does when workers call the same       *)
(* @c4a.effect, a person approves and denies, processes crash, and the      *)
(* outside world is asked (reconcile=) or deduplicates (idempotent_for=).   *)
(*                                                                         *)
(* It models the CODE (call4agents/__init__.py decide_run, record_result,   *)
(* failed; call4agents/ledger.py claim, claim_approved), not the README.    *)
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
    MAX_CALLS,     \* calls per worker; a crashed worker that comes back uses its next call
    MAX_YES,       \* how many "yes" the person may record
    MAX_NO,        \* how many "no" the person may record
    APPROVAL,      \* TRUE: @c4a.effect(approval=True) (claim_approved); FALSE: plain claim
    MODE,          \* "plain" (no reconcile, no idempotent_for), "reconcile", or "idem" (idempotent_for)
    RETRY_ON,      \* TRUE: retry_on= is set (an error that proves nothing happened)
    CRASHES,       \* TRUE: any worker may crash at any step
    EFFECT_FAILS,  \* TRUE: the tool may raise, before or after its effect happened
    TIMING,        \* TRUE: assume `wait` is longer than any live attempt (see TimeoutAllowed)
    FIX,           \* TRUE: the proposed fix (abandon / same-key retry only for the CURRENT attempt, atomically)
    BUG            \* "none" or the name of a seeded bug (see the end of the file)

Workers == 1..NW
KeyOf(w) == IF w = 3 THEN "k2" ELSE "k1"           \* workers 1 and 2 make the SAME call
Keys == {KeyOf(w) : w \in Workers}
ArgsOfKey(k) == IF k = "k2" THEN ARG3 ELSE "a"
ArgsOf(w) == ArgsOfKey(KeyOf(w))
ArgSet == {ArgsOf(w) : w \in Workers}

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
CurrentAttempt(k) == Count("tool_abandoned", k) + Count("tool_retry_same_key", k) + 1

PcSet == {"idle", "readDone", "readAband", "readRetry", "claim", "claimWrite", "claimNext",
          "claimWriteNext", "effect", "recordDone", "checkLate", "failOther", "failRetry",
          "readErr", "wait", "afterWait", "reconcile", "reconDone", "abandon"}
InFlight == {"effect", "recordDone", "failOther", "failRetry"}   \* holds an attempt, result not yet written

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

\* ---------------------------------------------------------------- ledger.claim_approved (ledger.py 274-310)
\* The decision the transaction takes, read from the record as it is now.
\* r: "true" (owns it), "false" (already claimed or done), "none" (needs approval). s: the "yes" to spend (0: reuse).
Decide(k, a, n) ==
    LET spent == Max(Pos("effect_approved", k))                    \* 286
        denied == Max(Matching("denial", a))                       \* 287
        approvals == Matching("approval", a)                       \* 288
        free == {s \in approvals : s > denied /\ (BUG = "no_spend" \/ ~Used(s))}   \* 295-298
    IN IF Has("tool_done", k) THEN [r |-> "false", s |-> 0]          \* 284
       ELSE IF spent > 0 /\ ((BUG # "no_deny_in_doubt" /\ denied > spent)   \* spent = seq of effect_approved
                             \/ log[spent].n \notin approvals)
            THEN [r |-> "none", s |-> 0]                             \* 289-291
       ELSE IF HasN("tool_intent", k, n) THEN [r |-> "false", s |-> 0]   \* 292
       ELSE IF spent > 0 THEN [r |-> "true", s |-> 0]                \* reuse the spent yes (a resume)
       ELSE IF free = {} THEN [r |-> "none", s |-> 0]                \* 302-303
       ELSE [r |-> "true", s |-> Min(free)]                          \* 299-301
AdmitEntries(k, a, n, s) ==
    IF s = 0 THEN <<E("tool_intent", k, a, n)>>
    ELSE <<E("approval_used", k, a, s), E("effect_approved", k, a, s), E("tool_intent", k, a, n)>>

\* __init__.py claim() 386-398, and ledger.claim 262-268 (plain: the UNIQUE index one_intent, ledger.py 207)
TryClaim(w, n, falsePc, splitPc) ==
    LET k == KeyOf(w)
        a == ArgsOf(w)
        d == Decide(k, a, n)
    IN \/ /\ ~APPROVAL
          /\ IF BUG # "no_unique_intent" /\ HasN("tool_intent", k, n)
             THEN Fail(w, falsePc, n) /\ UNCHANGED log
             ELSE log' = Append(log, E("tool_intent", k, a, n)) /\ SetW(w, "effect", n, iter[w], 0)
       \/ /\ APPROVAL /\ BUG = "split_admission"                 \* seeded: check, then write later
          /\ UNCHANGED log
          /\ IF d.r = "true" THEN SetW(w, splitPc, n, iter[w], d.s)
             ELSE IF d.r = "false" THEN Fail(w, falsePc, n) ELSE Idle(w)
       \/ /\ APPROVAL /\ BUG # "split_admission"                 \* one transaction (BEGIN IMMEDIATE)
          /\ IF d.r = "true"
             THEN log' = log \o AdmitEntries(k, a, n, d.s) /\ SetW(w, "effect", n, iter[w], 0)
             ELSE IF d.r = "false" THEN Fail(w, falsePc, n) /\ UNCHANGED log
             ELSE Idle(w) /\ UNCHANGED log                           \* ApprovalRequired

\* seeded split_admission only: the write step; the UNIQUE indexes still refuse a duplicate row
ClaimWriteStep(w, falsePc) ==
    LET k == KeyOf(w)
        a == ArgsOf(w)
    IN IF HasN("tool_intent", k, att[w]) \/ (pick[w] > 0 /\ Used(pick[w]))
       THEN Fail(w, falsePc, att[w]) /\ UNCHANGED log
       ELSE log' = log \o AdmitEntries(k, a, att[w], pick[w]) /\ SetW(w, "effect", att[w], iter[w], 0)

\* The outside system. With idempotent_for the request carries the effect key (the same on every attempt)
\* and the provider applies a key once.
Apply(k) == world' = [world EXCEPT ![k] = IF MODE = "idem" /\ BUG # "provider_forgets" /\ @ >= 1 THEN @ ELSE @ + 1]

\* Abandon or same-key retry. Current code: a plain append. FIX: one transaction that writes it only if
\* this attempt is still the current one and there is no result.
CasOK(k, n) == ~FIX \/ (CurrentAttempt(k) = n /\ ~Has("tool_done", k))
AbandonLog(k, a, n) ==
    IF BUG = "delete_on_abandon"     \* seeded: "clean up" the stale intent instead of appending
    THEN log' = SelectSeq(log, LAMBDA e : ~(e.kind = "tool_intent" /\ e.key = k /\ e.n = n))
    ELSE log' = Append(log, E("tool_abandoned", k, a, n))

\* timing assumption: under TIMING, `wait` ends only when no other live worker holds an attempt of this
\* key and no result exists (a result written before the deadline is seen by the poll, 442-446)
TimeoutAllowed(w) ==
    /\ BUG # "no_timeout"
    /\ \/ ~TIMING
       \/ /\ ~Has("tool_done", KeyOf(w))
          /\ ~\E v \in Workers \ {w} : KeyOf(v) = KeyOf(w) /\ pc[v] \in InFlight

\* ---------------------------------------------------------------- worker steps
Start(w) ==                                                   \* wrapper() 536-541
    /\ pc[w] = "idle" /\ calls[w] < MAX_CALLS
    /\ calls' = [calls EXCEPT ![w] = @ + 1]
    /\ Go(w, "readDone")
    /\ UNCHANGED <<log, world, ran, yes, no>>

ReadDone(w) ==                                                \* 432-434: a recorded result is replayed
    /\ pc[w] = "readDone"
    /\ IF Has("tool_done", KeyOf(w)) THEN Idle(w) ELSE Go(w, "readAband")
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

ReadAband(w) ==                                               \* 435, first COUNT
    /\ pc[w] = "readAband"
    /\ SetW(w, "readRetry", Count("tool_abandoned", KeyOf(w)), iter[w], 0)
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

ReadRetry(w) ==                                               \* 435, second COUNT
    /\ pc[w] = "readRetry"
    /\ SetW(w, "claim", att[w] + Count("tool_retry_same_key", KeyOf(w)) + 1, iter[w], 0)
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

Claim(w) ==                                                   \* 436-437
    /\ pc[w] = "claim"
    /\ TryClaim(w, att[w], "readErr", "claimWrite")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

ClaimWrite(w) ==
    /\ pc[w] = "claimWrite"
    /\ ClaimWriteStep(w, "readErr")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

Effect(w) ==                                                  \* f(*args) 546; failed() 548
    LET k == KeyOf(w) IN
    /\ pc[w] = "effect"
    /\ ran' = ran \cup {<<k, att[w]>>}
    /\ \/ Apply(k) /\ Go(w, "recordDone")                                  \* returns
       \/ EFFECT_FAILS /\ Apply(k) /\ Go(w, "failOther")                   \* raises AFTER it happened
       \/ EFFECT_FAILS /\ RETRY_ON /\ BUG = "retry_on_lies" /\ Apply(k) /\ Go(w, "failRetry")
       \/ EFFECT_FAILS /\ UNCHANGED world /\ Go(w, "failOther")            \* raises before it happened
       \/ EFFECT_FAILS /\ RETRY_ON /\ UNCHANGED world /\ Go(w, "failRetry")  \* a retry_on error: nothing happened
    /\ UNCHANGED <<log, calls, yes, no>>

RecordDone(w) ==                                              \* record_result 480
    /\ pc[w] = "recordDone"
    /\ log' = Append(log, E("tool_done", KeyOf(w), ArgsOf(w), att[w]))
    /\ Go(w, "checkLate")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

CheckLate(w) ==                                               \* record_result 481-485 (read + append merged)
    /\ pc[w] = "checkLate"
    /\ IF HasN("tool_abandoned", KeyOf(w), att[w])
       THEN log' = Append(log, E("late_result", KeyOf(w), ArgsOf(w), att[w]))
       ELSE UNCHANGED log
    /\ Idle(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

FailOther(w) ==                                               \* failed() 493-494: tool_error, call raises
    /\ pc[w] = "failOther"
    /\ log' = Append(log, E("tool_error", KeyOf(w), ArgsOf(w), att[w]))
    /\ Idle(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

FailRetry(w) ==                                               \* failed() 489-490: retry_on -> tool_abandoned
    /\ pc[w] = "failRetry"
    /\ IF CasOK(KeyOf(w), att[w]) THEN AbandonLog(KeyOf(w), ArgsOf(w), att[w]) ELSE UNCHANGED log
    /\ Idle(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

ReadErr(w) ==                                                 \* 438-441: an error of THIS attempt skips the wait
    LET e == Max(Pos("tool_error", KeyOf(w))) IN
    /\ pc[w] = "readErr"
    /\ Go(w, IF e > 0 /\ log[e].n = att[w] THEN "afterWait" ELSE "wait")
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

Wait(w) ==                                                    \* 442-446
    /\ pc[w] = "wait"
    /\ \/ Has("tool_done", KeyOf(w)) /\ Idle(w)                    \* the poll sees the result: replay
       \/ TimeoutAllowed(w) /\ Go(w, "afterWait")                  \* the deadline passes
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

AfterWait(w) ==                                               \* 447-457
    LET k == KeyOf(w) IN
    /\ pc[w] = "afterWait"
    /\ CASE MODE = "plain" -> Idle(w) /\ UNCHANGED log                 \* EffectInDoubt
         [] MODE = "reconcile" -> Go(w, "reconcile") /\ UNCHANGED log
         [] MODE = "idem" ->
              \/ Idle(w) /\ UNCHANGED log                                   \* outside the window: EffectInDoubt
              \/ /\ CasOK(k, att[w])                                        \* 452: tool_retry_same_key
                 /\ log' = Append(log, E("tool_retry_same_key", k, ArgsOf(w), att[w]))
                 /\ Go(w, "claimNext")
              \/ ~CasOK(k, att[w]) /\ Idle(w) /\ UNCHANGED log
    /\ UNCHANGED <<world, ran, calls, yes, no>>

ClaimNext(w) ==                                               \* 453-455: claim(attempt + 1)
    /\ pc[w] = "claimNext"
    /\ TryClaim(w, att[w] + 1, "idle", "claimWriteNext")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

ClaimWriteNext(w) ==
    /\ pc[w] = "claimWriteNext"
    /\ ClaimWriteStep(w, "idle")
    /\ UNCHANGED <<world, ran, calls, yes, no>>

Reconcile(w) ==                                               \* 458-461: reconcile(**args)
    LET happened == world[KeyOf(w)] >= 1 IN
    /\ pc[w] = "reconcile"
    /\ \/ Idle(w)                                                      \* c4a.UNKNOWN: stays in doubt
       \/ (happened \/ BUG = "reconcile_lies_found") /\ Go(w, "reconDone")   \* returns the result
       \/ (~happened \/ BUG = "reconcile_lies_none") /\ Go(w, "abandon")     \* returns None
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

ReconDone(w) ==                                               \* 462-463
    /\ pc[w] = "reconDone"
    /\ log' = Append(log, E("tool_done", KeyOf(w), ArgsOf(w), 0))
    /\ Idle(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

Abandon(w) ==                                                 \* 464-466, then the loop (431) or 467
    /\ pc[w] = "abandon"
    /\ IF CasOK(KeyOf(w), att[w])
       THEN /\ AbandonLog(KeyOf(w), ArgsOf(w), att[w])
            /\ IF iter[w] = 0 THEN SetW(w, "readDone", 0, 1, 0) ELSE Idle(w)
       ELSE UNCHANGED log /\ Idle(w)
    /\ UNCHANGED <<world, ran, calls, yes, no>>

Crash(w) ==                                                   \* os._exit at any point: memory lost
    /\ CRASHES /\ pc[w] # "idle"
    /\ Idle(w)
    /\ UNCHANGED <<log, world, ran, calls, yes, no>>

Progress(w) ==
    \/ Start(w) \/ ReadDone(w) \/ ReadAband(w) \/ ReadRetry(w) \/ Claim(w) \/ ClaimWrite(w)
    \/ Effect(w) \/ RecordDone(w) \/ CheckLate(w) \/ FailOther(w) \/ FailRetry(w) \/ ReadErr(w)
    \/ Wait(w) \/ AfterWait(w) \/ ClaimNext(w) \/ ClaimWriteNext(w) \/ Reconcile(w) \/ ReconDone(w)
    \/ Abandon(w)

\* ---------------------------------------------------------------- the person (c4a.approve / c4a.deny)
Approve(a) ==                                                 \* ledger.record_approval 425-428
    /\ APPROVAL /\ yes < MAX_YES
    /\ log' = Append(log, E("approval", "-", a, 0))
    /\ yes' = yes + 1
    /\ UNCHANGED <<world, pc, att, iter, pick, calls, ran, no>>

Deny(a) ==                                                    \* ledger.record_denial 430-434
    /\ APPROVAL /\ no < MAX_NO
    /\ log' = Append(log, E("denial", "-", a, 0))
    /\ no' = no + 1
    /\ UNCHANGED <<world, pc, att, iter, pick, calls, ran, yes>>

Next ==
    \/ \E w \in Workers : Progress(w) \/ Crash(w)
    \/ \E a \in ArgSet : Approve(a) \/ Deny(a)

Spec == Init /\ [][Next]_vars

\* Progress property only: workers do not stop forever (weak fairness); crashes and the person are not fair.
LiveSpec == Spec /\ \A w \in Workers : WF_vars(Progress(w))

\* ---------------------------------------------------------------- properties
TypeOK ==
    /\ pc \in [Workers -> PcSet]
    /\ world \in [Keys -> Nat]
    /\ calls \in [Workers -> 0..MAX_CALLS]
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

\* At most one tool_done per action (checked separately: the current code can write two, see the doc).
DoneOnce == \A k \in Keys : Count("tool_done", k) <= 1

\* If an action happened twice, the record says so (late_result). Checked to show when a double is silent.
NoSilentDouble == \A k \in Keys : world[k] > 1 => Has("late_result", k)

\* Entries are never changed or removed.
AppendOnly == [][Len(log') >= Len(log) /\ SubSeq(log', 1, Len(log)) = log]_log

\* Liveness (only with LiveSpec): every call ends (no worker waits forever) ...
AllCallsEnd == <>(\A w \in Workers : pc[w] = "idle" /\ calls[w] = MAX_CALLS)
\* ... and, with no crash and a tool that returns, an admitted action gets its result.
AdmittedFinishes == \A k \in Keys : Has("tool_intent", k) ~> Has("tool_done", k)

\* Seeded bugs (BUG = ...), each a real mistake that a check must catch:
\*   split_admission     the "yes" check and the intent write are two steps (no lock between them)
\*   no_spend            a used "yes" is not marked/checked as spent
\*   no_deny_in_doubt    a "no" is not checked when an admitted action is resumed
\*   reconcile_lies_none reconcile returns None although the action happened
\*   reconcile_lies_found reconcile returns a result although nothing happened
\*   retry_on_lies       an error listed in retry_on is raised AFTER the action happened
\*   provider_forgets    the outside API does not deduplicate the idempotency key (window too short)
\*   no_unique_intent    no UNIQUE index on tool_intent
\*   delete_on_abandon   abandon deletes the stale intent instead of appending tool_abandoned
\*   no_timeout          `wait` never ends while the action has no result (liveness)
================================================================================
