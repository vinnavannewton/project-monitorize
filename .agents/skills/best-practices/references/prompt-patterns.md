# Prompt Transformation Patterns

This document contains reusable templates and patterns for transforming prompts. Use these as building blocks when optimizing user prompts.

## Table of Contents

1. [Core Pattern Templates](#core-pattern-templates)
2. [Verification Patterns](#verification-patterns)
3. [Context Patterns](#context-patterns)
4. [Scoping Patterns](#scoping-patterns)
5. [Phasing Patterns](#phasing-patterns)
6. [Constraint Patterns](#constraint-patterns)
7. [Investigation Patterns](#investigation-patterns)

---

## Core Pattern Templates

### The Complete Prompt Template

```
[WHAT] - Clear description of what to do
[WHERE] - Specific files/locations involved
[HOW] - Constraints, patterns to follow, approaches to use/avoid
[VERIFY] - How to confirm success (tests, commands, visual check)
```

**Example:**
```
Implement user email verification [WHAT]
in src/auth/verification.ts [WHERE]
following the existing auth patterns in @src/auth/login.ts, without external libraries [HOW]
run the auth test suite and verify a test user can complete the flow [VERIFY]
```

---

### The Bug Fix Template

```
[SYMPTOM] - What users experience
[LOCATION] - Where to look
[REPRODUCE] - How to trigger the bug (if known)
[FIX APPROACH] - Suggested investigation/fix
[VERIFY] - How to confirm the fix works
```

**Example:**
```
Users see "undefined" instead of their username after login [SYMPTOM]
Check the user context provider in src/context/UserContext.tsx and the login handler in src/api/auth.ts [LOCATION]
Happens when logging in after session expires [REPRODUCE]
Write a failing test for the expired session case, then fix the null handling [FIX APPROACH]
Run the auth test suite and manually verify the login flow [VERIFY]
```

---

### The Feature Template

```
[GOAL] - What the feature should do
[CONTEXT] - Existing code to reference
[REQUIREMENTS] - Specific behaviors/acceptance criteria
[CONSTRAINTS] - What to avoid or limitations
[VERIFY] - How to test the feature
```

**Example:**
```
Add a "remember me" option to the login form [GOAL]
Follow the existing form patterns in @src/components/LoginForm.tsx [CONTEXT]
Requirements:
- Checkbox below password field
- If checked, extend session to 30 days
- Store preference in localStorage
- Default to unchecked [REQUIREMENTS]
No external libraries, use existing cookie utilities [CONSTRAINTS]
Add tests for both checked and unchecked states, verify session duration in DevTools [VERIFY]
```

---

### The Refactor Template

```
[TARGET] - What to refactor
[GOAL] - Why/what improvement
[APPROACH] - Specific changes to make
[PRESERVE] - What must stay the same
[VERIFY] - How to ensure nothing broke
```

**Example:**
```
Refactor the OrderProcessor class in src/services/OrderProcessor.ts [TARGET]
Convert from class-based to functional approach for better testability [GOAL]
- Extract pure functions for calculations
- Use dependency injection for services
- Convert methods to exported functions [APPROACH]
All existing tests must continue to pass, API signatures unchanged [PRESERVE]
Run the full test suite after each change, check coverage remains above 80% [VERIFY]
```

---

## Verification Patterns

### Test Case Pattern

```
[action]. test cases:
- [input1] → [expected output1]
- [input2] → [expected output2]
- [edge case] → [expected handling]
run the tests after implementing.
```

**Example:**
```
write a slugify function. test cases:
- "Hello World" → "hello-world"
- "Already-Slugged" → "already-slugged"
- "Multiple   Spaces" → "multiple-spaces"
- "" → ""
- "Special $#@ Chars!" → "special-chars"
run the tests after implementing.
```

---

### Visual Verification Pattern

```
[paste screenshot/mockup]
implement this design.
take a screenshot of the result and compare it to the original.
list any differences and fix them.
verify at [breakpoints] widths.
```

**Example:**
```
[paste mockup]
implement this card design for the product listing.
take a screenshot of the result and compare it to the mockup.
list any differences and fix them.
verify at 320px, 768px, and 1200px widths.
```

---

### Build Verification Pattern

```
[describe problem/change].
[investigation/fix approach].
run [build command] to verify success.
address the root cause, don't suppress errors.
```

**Example:**
```
the TypeScript build fails with "Property 'user' does not exist on type 'Session'".
add the user property to the Session interface in src/types/auth.ts.
run `npm run build` to verify success.
address the root cause, don't suppress errors with @ts-ignore.
```

---

### Regression Verification Pattern

```
[make change].
run the existing test suite after each change.
if any tests fail, investigate why before proceeding.
[final verification step].
```

---

## Context Patterns

### File Reference Pattern

```
look at @[file path] to understand [what].
follow the same pattern to [action].
```

**Example:**
```
look at @src/components/UserCard.tsx to understand the card component pattern.
follow the same pattern to create a ProductCard component.
```

---

### Git History Pattern

```
look through [file/module]'s git history and [action]:
- when was it [created/changed]?
- what were the major changes and why?
- are there related issues or PRs?
summarize [specific aspect].
```

---

### Codebase Search Pattern

```
search the codebase for [pattern/usage].
identify all places where [condition].
[action based on findings].
```

**Example:**
```
search the codebase for uses of the deprecated `oldApiCall` function.
identify all places where it's imported or called.
update each usage to use `newApiCall` instead, following the migration guide in @docs/api-migration.md.
```

---

### Pattern Discovery Pattern

```
look at how [similar feature] is implemented in [location].
understand the patterns used for [specific aspects].
follow these patterns to implement [new feature].
```

---

## Scoping Patterns

### Single Responsibility Pattern

```
[action] for [specific case only].
do not [out of scope action].
[verify within scope].
```

**Example:**
```
add validation for the email field only.
do not change other form fields or validation logic.
test email validation with valid, invalid, and edge case inputs.
```

---

### Edge Case Specification Pattern

```
[action] covering these cases:
- [normal case]
- [edge case 1]
- [edge case 2]
- [error case]
[verify each case].
```

**Example:**
```
implement the calculateDiscount function covering these cases:
- standard percentage discount (10% off $100 = $90)
- discount exceeds price (cap at $0, no negative)
- zero quantity (return 0)
- invalid discount code (throw DiscountError)
test each case explicitly.
```

---

### Exclusion Pattern

```
[action].
specifically:
- do [included action 1]
- do [included action 2]
- do NOT [excluded action]
- avoid [constraint]
```

**Example:**
```
refactor the utility functions in src/utils/.
specifically:
- do convert to TypeScript
- do add JSDoc comments
- do NOT change function signatures
- avoid adding new dependencies
```

---

## Phasing Patterns

### Explore-Plan-Implement Pattern

```
PHASE 1 - EXPLORE:
read [files/areas] and understand [aspects].

PHASE 2 - PLAN:
create a plan for [implementation].
write the plan to [location] for review.

PHASE 3 - IMPLEMENT (after approval):
implement following the plan.
[specific steps].

PHASE 4 - VERIFY:
[verification steps].
```

---

### Incremental Change Pattern

```
make changes incrementally:
1. [first change] - run tests
2. [second change] - run tests
3. [third change] - run tests
if any step fails, investigate before proceeding.
```

---

### Investigation-First Pattern

```
before making changes:
1. [gather information]
2. [analyze findings]
3. [propose approach]
then, with understanding:
4. [implement]
5. [verify]
```

---

### Parallel Workstream Pattern

```
this task has independent parts that can be done in parallel:

WORKSTREAM A:
- [task A1]
- [task A2]

WORKSTREAM B:
- [task B1]
- [task B2]

after both complete:
- [integration step]
- [final verification]
```

---

## Constraint Patterns

### Dependency Constraint Pattern

```
[action].
do not add new dependencies.
use only libraries already in package.json.
build from scratch if needed.
```

---

### Compatibility Constraint Pattern

```
[action].
maintain backward compatibility:
- existing API signatures must not change
- existing tests must continue to pass
- deprecated methods should log warnings but still work
```

---

### Performance Constraint Pattern

```
[action].
performance requirements:
- response time under [X]ms for [operation]
- memory usage under [X]MB
- support [X] concurrent [operations]
measure before and after, document improvements.
```

---

### Style Constraint Pattern

```
[action].
follow existing code style:
- match patterns in @[similar file]
- use project's naming conventions
- follow the linter configuration
run `npm run lint` before committing.
```

---

## Investigation Patterns

### Debugging Investigation Pattern

```
[symptom description].

INVESTIGATE:
1. check [likely location 1]
2. check [likely location 2]
3. add logging to trace [flow]

IDENTIFY:
- what is the actual vs expected behavior?
- when did this start happening?
- what changed recently?

FIX:
- write a failing test first
- implement the fix
- verify the test passes
```

---

### Performance Investigation Pattern

```
[performance symptom].

PROFILE:
1. run [profiling tool/command]
2. identify bottlenecks
3. measure baseline metrics

ANALYZE:
- what operations are slow?
- what resources are constrained?
- where are the quick wins?

OPTIMIZE:
- implement top 3 improvements
- measure after each change
- document improvements
```

---

### Root Cause Analysis Pattern

```
[problem description].

don't just fix the symptom, find the root cause:
1. what is the immediate error?
2. what caused that error?
3. what caused THAT?
4. continue until you find the root

fix at the appropriate level.
verify the fix addresses the root, not just the symptom.
```

---

## Combination Examples

### Full Bug Fix (combining patterns)

```
Users see a blank screen when loading the dashboard after being idle for 30+ minutes. [SYMPTOM]

INVESTIGATE:
- check browser console for errors
- check network tab for failed requests
- look at src/context/AuthContext.tsx for session handling [LOCATION]

REPRODUCE:
- log in, wait 30 minutes (or manually expire the session token)
- refresh the dashboard page [REPRODUCE]

FIX:
- write a failing test for the expired session case
- add proper error handling for 401 responses
- implement token refresh or redirect to login [FIX APPROACH]

VERIFY:
- test passes
- manually verify: expire session, refresh page, should redirect to login gracefully [VERIFY]
```

---

### Full Feature (combining patterns)

```
Add export functionality to the reports page. [GOAL]

CONTEXT:
- look at @src/components/ReportsList.tsx for current report display
- check @src/api/reports.ts for data fetching patterns [CONTEXT]

REQUIREMENTS:
- "Export" dropdown button in report header
- Options: CSV, PDF, Excel
- Show progress indicator during export
- Download file when complete [REQUIREMENTS]

CONSTRAINTS:
- use existing UI components from @src/components/ui/
- use the pdf-lib library already in dependencies
- no server-side generation (client-side only) [CONSTRAINTS]

APPROACH:
1. add ExportButton component
2. implement CSV export first (simplest)
3. add PDF export
4. add Excel export
5. add loading states [PHASING]

VERIFY:
- add tests for each export format
- manually test with a report containing 1000+ rows
- verify files open correctly in respective applications [VERIFY]
```
