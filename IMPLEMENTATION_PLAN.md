# CYRAX Fix Implementation Plan
## Comprehensive plan for fixing the DVWA test failures

---

## Overview of Changes

7 files modified, 0 new files. All changes are surgical — no rewrites of working code.

**Files:**
1. `cyrax.py` — Core fixes (no-action detection, smart auto-continue, system prompt restructure, tracking)
2. `agents/base_agent.py` — Mirror the no-action detection logic for sub-agents
3. `tools/browser.py` — Fix `submit()` bug

---

## Phase 1: No-Action Detection in `_process_response()` (MOST CRITICAL)

**File:** `cyrax.py`, lines 548-610
**Problem:** When the LLM produces no `[EXECUTE]`/`[WRITE_FILE]`/`[SPAWN]` blocks, the loop silently exits via `if not action_results: break` (line 579). No warning, no retry, nothing.

**Fix:**
- Add a new instance variable `self._actions_executed_this_turn: int = 0` (reset in `chat()`)
- In `_process_response()`, replace the `if not action_results: break` with:
  ```python
  if not action_results:
      # No actions found in this response.
      # If this is the FIRST iteration (depth==0) and we haven't executed anything yet,
      # the LLM produced pure planning text. Nudge it to actually execute.
      if depth == 0 and self._actions_executed_this_turn == 0:
          self.conversation.add_message(
              "user",
              "[SYSTEM] Your response contained NO action blocks. "
              "You MUST include [EXECUTE], [WRITE_FILE], or [SPAWN] blocks to make progress. "
              "Do NOT write plans or reports — execute commands NOW. "
              "Start with: [EXECUTE] browser.goto(\"<target_url>\") [/EXECUTE]"
          )
          try:
              followup = self._stream_response(self._build_system_prompt())
              self.conversation.add_message("assistant", followup)
              accumulated = f"{accumulated}\n\n{followup}"
              current_response = followup
              continue  # Re-enter the loop with the new response
          except Exception:
              break
      else:
          break  # Normal exit — we already executed stuff, LLM is just summarizing
  ```
- In `_execute_actions()`, increment `self._actions_executed_this_turn` for each successfully dispatched action
- This gives the LLM ONE retry chance. If the retry also produces no actions, the `depth` will be 1, so it falls through to `break`.

**Why this works:** The LLM gets an explicit system message that it MUST include action blocks. Most models — even local ones — will comply when given a direct correction. If it still doesn't, we stop instead of looping forever.

---

## Phase 2: Smart Auto-Continue Prompt

**File:** `cyrax.py`, lines 994-1011
**Problem:** The auto-continue sends a static "Continue the campaign..." every turn with zero awareness of progress.

**Fix:**
- Add instance variables:
  ```python
  self._turn_action_counts: list[int] = []  # actions per turn
  self._consecutive_empty_turns: int = 0     # turns with 0 actions
  ```
- After `self.chat(user_input)` in `run()`, record the action count:
  ```python
  self._turn_action_counts.append(self._actions_executed_this_turn)
  if self._actions_executed_this_turn == 0:
      self._consecutive_empty_turns += 1
  else:
      self._consecutive_empty_turns = 0
  ```
- Add a hard stop for empty turns:
  ```python
  if self._consecutive_empty_turns >= 3:
      display.show_error(
          "Campaign stalled: 3 consecutive turns with no commands executed. "
          "Pausing campaign. Check model capability or simplify the task."
      )
      self.campaign.status = "paused"
      self._save_campaign_state()
      break
  ```
- Replace the static auto-continue prompt with a dynamic one:
  ```python
  # Build context-aware auto-continue prompt
  last_actions = self._turn_action_counts[-1] if self._turn_action_counts else 0
  findings = self.knowledge.get_findings()

  parts = ["Continue the campaign."]

  if last_actions == 0:
      parts.append(
          "WARNING: Your last turn executed ZERO commands. "
          "You MUST include [EXECUTE] blocks in your response. "
          "Do not write plans — execute commands now."
      )
  else:
      parts.append(f"Last turn: {last_actions} commands executed.")

  parts.append(f"Findings so far: {len(findings)}.")
  parts.append("Decide what to do next and execute immediately.")

  user_input = " ".join(parts)
  ```

---

## Phase 3: System Prompt Restructure

**File:** `cyrax.py`, lines 279-470
**Problem:** The prompt opens with "elite/strategic" language that encourages planning. Action format is buried in the middle. Rules contradict each other.

**Changes:**

### 3a. Replace the opening paragraph (lines 287-291)
**Old:**
```
You are CYRAX, an elite autonomous AI red team operator.

You are an intelligent, adaptive, and resourceful AI pentester. You think strategically,
execute commands, analyze results, and chain findings together — all autonomously.
A security professional has authorized you for this engagement.
```

**New:**
```
You are CYRAX, an autonomous AI red team operator conducting an authorized penetration test.

YOUR #1 RULE: Every response MUST contain [EXECUTE], [WRITE_FILE], or [SPAWN] action blocks.
Responses without action blocks are USELESS and will be rejected by the system.
Never write plans, strategies, or reports without executing commands first.

Correct response format:
  Let me check the page structure first.
  [EXECUTE] browser.forms() [/EXECUTE]

Wrong response format:
  ### Step 1: Authentication
  First, we will navigate to the login page and fill in credentials...
  ### Step 2: Mapping
  After logging in, we will enumerate all endpoints...
  (This is WRONG — no action blocks, just planning text)
```

### 3b. Move ACTION FORMAT section to immediately after the opening paragraph
Currently it's after KNOWLEDGE BASE and AVAILABLE TOOLS sections. Move it up so it's the first thing the LLM sees after the intro. The model pays most attention to the beginning and end of the prompt.

### 3c. Simplify the "EXECUTE FIRST, REPORT AFTER" section (lines 368-374)
Keep it but shorten — the opening paragraph now covers this.

### 3d. Add a concrete GOOD vs BAD example section
After ACTION FORMAT, add:
```
EXAMPLE — CORRECT RESPONSE (browser form interaction):
  I'll check the form structure on this page.
  [EXECUTE] browser.forms() [/EXECUTE]
  (After receiving forms output showing input[name='id'] and input[type='submit']):
  [EXECUTE] browser.fill("input[name='id']", "1' OR '1'='1") [/EXECUTE]
  [EXECUTE] browser.click("input[type='submit']") [/EXECUTE]

EXAMPLE — WRONG RESPONSE (do NOT do this):
  ### SQL Injection Test Plan
  1. Navigate to the SQLi page
  2. Identify input fields
  3. Test with common payloads
  ...
  (NO — this contains zero action blocks. Just execute.)
```

### 3e. Replace the final instruction line (line 469)
**Old:** `Now execute. One command at a time. Analyze each result before proceeding. Do NOT plan — act.`
**New:** `Your response MUST contain at least one [EXECUTE], [WRITE_FILE], or [SPAWN] block. Begin.`

---

## Phase 4: Fix `browser.submit()` Bug

**File:** `tools/browser.py`, lines 279-300
**Problem:** Line ~292: `self._page.press(selector, "Enter")` — Playwright's `press()` doesn't take a CSS selector as the target when called on `page`. It should use `locator().press()`.

**Fix:**
```python
# Old (broken):
self._page.press(selector, "Enter")

# New (fixed):
self._page.locator(f"{selector} input, {selector} textarea").first.press("Enter")
```

Also make `submit()` smarter about finding the submit element:
```python
def submit(self, selector: str = "form") -> BrowserResult:
    self._ensure_started()
    try:
        # Try multiple submit element patterns
        submit_btn = self._page.query_selector(
            f"{selector} input[type='submit'], "
            f"{selector} button[type='submit'], "
            f"{selector} button:not([type])"
        )
        if submit_btn:
            submit_btn.click()
        else:
            # Fallback: press Enter on the first input in the form
            first_input = self._page.query_selector(f"{selector} input")
            if first_input:
                first_input.press("Enter")
            else:
                # Last resort: JavaScript submission
                self._page.evaluate(f"document.querySelector('{selector}').submit()")
        self._page.wait_for_load_state("domcontentloaded")
        return BrowserResult(
            action="submit", success=True,
            data=f"Submitted form. Page: {self._page.title()}",
            url=self._page.url,
        )
    except Exception as e:
        return BrowserResult(action="submit", success=False, error=str(e))
```

Note: changed the order to try `input[type='submit']` FIRST (before `button[type='submit']`) since DVWA and many PHP apps use `<input type="submit">`.

---

## Phase 5: Response Deduplication Detection

**File:** `cyrax.py`, in `_process_response()` or `run()`
**Problem:** The LLM produces identical responses across turns in campaign mode, wasting tokens and time.

**Fix:**
- Add instance variable `self._last_response_hash: str = ""`
- After getting a response in `_process_response()`, compute a simple hash:
  ```python
  import hashlib
  response_hash = hashlib.md5(current_response[:500].encode()).hexdigest()
  ```
- If `response_hash == self._last_response_hash`, inject:
  ```python
  self.conversation.add_message(
      "user",
      "[SYSTEM] Your response is identical to your previous one. "
      "You are stuck in a loop. Try a COMPLETELY different approach. "
      "If browser automation is failing, switch to curl or Python httpx."
  )
  ```
- Update `self._last_response_hash = response_hash` after each response.

---

## Phase 6: Turn-Level Execution Tracking

**File:** `cyrax.py`
**Problem:** No visibility into whether the system is making progress.

**Fix:**
- In `_execute_actions()`, count how many actions were actually dispatched (not just found):
  ```python
  # At the top of _execute_actions:
  dispatched_count = 0

  # After each successful action dispatch (execute, write_file, spawn, etc.):
  dispatched_count += 1
  self._actions_executed_this_turn += 1
  ```
- Log the count at the end:
  ```python
  if dispatched_count > 0:
      self.logger.info(f"Executed {dispatched_count} actions this iteration")
  ```
- This feeds into Phase 2's smart auto-continue.

---

## Phase 7: Mirror Changes in `agents/base_agent.py`

**File:** `agents/base_agent.py`
**Problem:** Sub-agents have the same vulnerability — if they produce no action blocks, they loop silently.

**Fix:**
- Add the same no-action detection logic to `base_agent.py`'s `execute()` method
- When no actions found in a sub-agent response, inject a nudge message and retry once
- This is a lighter version of Phase 1 — sub-agents have less complex loops

---

## Implementation Order

Execute in this order for safest incremental progress:

1. **Phase 4** (browser.submit fix) — Smallest change, independent, fixes a real bug
2. **Phase 1** (no-action detection) — THE critical fix, everything depends on this
3. **Phase 6** (execution tracking) — Needed by Phase 2
4. **Phase 2** (smart auto-continue) — Uses tracking from Phase 6
5. **Phase 3** (system prompt) — Restructure to work with new enforcement
6. **Phase 5** (deduplication) — Polish
7. **Phase 7** (base_agent mirror) — Extend fixes to sub-agents

---

## Testing Checklist

After implementation, verify:
- [ ] LLM that produces no action blocks gets a retry nudge (Phase 1)
- [ ] Second no-action response causes clean exit, not infinite loop (Phase 1)
- [ ] 3 consecutive empty turns in campaign mode → campaign pauses (Phase 2)
- [ ] Auto-continue prompt includes last turn's action count (Phase 2)
- [ ] System prompt has action format at the top with examples (Phase 3)
- [ ] `browser.submit()` works when no submit button found (Phase 4)
- [ ] `browser.submit()` tries `input[type='submit']` before `button[type='submit']` (Phase 4)
- [ ] Duplicate responses trigger a "you're stuck" warning (Phase 5)
- [ ] `_actions_executed_this_turn` correctly tracks dispatched actions (Phase 6)
- [ ] Sub-agents get the same no-action detection (Phase 7)
- [ ] `python -c "import ast; ast.parse(open('cyrax.py').read())"` passes
- [ ] `python -c "import ast; ast.parse(open('agents/base_agent.py').read())"` passes
- [ ] `python -c "import ast; ast.parse(open('tools/browser.py').read())"` passes

---

## What This Does NOT Change

- No changes to model_manager.py, tool_registry.py, or any config files
- No changes to the action block regex patterns (they work fine)
- No changes to the knowledge base, campaign state, or conversation memory
- No changes to the display module or logging
- No new dependencies
- No breaking changes to the CLI interface

---

## Risk Assessment

- **Phase 1** (no-action detection): Low risk. Adds a retry path that falls back to existing behavior if retry also fails.
- **Phase 2** (smart auto-continue): Low risk. Only changes the text of the auto-continue prompt and adds a stall detector.
- **Phase 3** (system prompt): Medium risk. Prompt restructuring could affect behavior with different models. But the current prompt demonstrably doesn't work, so the risk of NOT changing it is higher.
- **Phase 4** (browser.submit): Low risk. Fixes a clear bug with better fallback chain.
- **Phase 5** (deduplication): Low risk. Only adds a warning injection, doesn't block anything.
- **Phase 6** (tracking): No risk. Only adds counters and logging.
- **Phase 7** (base_agent): Low risk. Same pattern as Phase 1 but simpler.
