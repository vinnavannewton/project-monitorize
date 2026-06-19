# Prompt Anti-Patterns to Avoid

This document catalogs common prompt mistakes and how to fix them. When transforming prompts, actively look for and correct these anti-patterns.

## Table of Contents

1. [Vagueness Anti-Patterns](#vagueness-anti-patterns)
2. [Missing Context Anti-Patterns](#missing-context-anti-patterns)
3. [Verification Anti-Patterns](#verification-anti-patterns)
4. [Scope Anti-Patterns](#scope-anti-patterns)
5. [Instruction Anti-Patterns](#instruction-anti-patterns)
6. [Session Anti-Patterns](#session-anti-patterns)

---

## Vagueness Anti-Patterns

### Anti-Pattern: The Generic Request

**BAD:**
```
fix the bug
```

**WHY IT FAILS:** No information about what bug, where it is, what symptoms, or how to verify it's fixed.

**GOOD:**
```
users report login fails after session timeout. check the auth flow in src/auth/, especially token refresh. write a failing test that reproduces the issue, then fix it.
```

---

### Anti-Pattern: The Ambiguous Improvement

**BAD:**
```
make the code better
```

**WHY IT FAILS:** "Better" is subjective. Better performance? Readability? Type safety? Fewer lines?

**GOOD:**
```
refactor utils.js to use ES2024 features while maintaining the same behavior. specifically: convert callbacks to async/await, use optional chaining. run the test suite after each change.
```

---

### Anti-Pattern: The Undefined Problem

**BAD:**
```
something's wrong with the API
```

**WHY IT FAILS:** No error message, no endpoint, no reproduction steps.

**GOOD:**
```
the GET /api/users endpoint returns 500 with this error: [paste error]. I can reproduce by calling the endpoint without an auth header. check src/api/users.ts line 45 where the request is handled.
```

---

### Anti-Pattern: The Wishful Feature

**BAD:**
```
add a nice login page
```

**WHY IT FAILS:** "Nice" is undefined. No design reference, no requirements, no patterns to follow.

**GOOD:**
```
create a login page with email and password fields. follow the form patterns in @src/components/SignupForm.tsx. include: validation feedback, remember me checkbox, forgot password link. test at 320px and 1024px widths.
```

---

### Anti-Pattern: The Partial Error

**BAD:**
```
getting an error
```

**WHY IT FAILS:** Which error? What file? What line? What action triggered it?

**GOOD:**
```
getting "TypeError: Cannot read property 'map' of undefined" at src/components/UserList.tsx:45 when loading the users page without being logged in. check the data fetching and add proper null handling.
```

---

## Missing Context Anti-Patterns

### Anti-Pattern: The Locationless Request

**BAD:**
```
update the validation logic
```

**WHY IT FAILS:** Validation is everywhere. Which validation? Which file? Which form?

**GOOD:**
```
update the email validation in @src/utils/validators.ts to also check for common disposable email domains. the domain list is in @src/config/blocked-domains.json.
```

---

### Anti-Pattern: The Pattern-Free Feature

**BAD:**
```
add a new component
```

**WHY IT FAILS:** No reference to existing patterns, no example of similar components.

**GOOD:**
```
add a ProductCard component following the patterns in @src/components/UserCard.tsx. include: image, title, price, and "Add to cart" button. use the same CSS modules approach.
```

---

### Anti-Pattern: The Orphan Request

**BAD:**
```
implement user authentication
```

**WHY IT FAILS:** No context about existing auth, no framework info, no session strategy preference.

**GOOD:**
```
read src/auth/ to understand current session handling. add Google OAuth following the existing patterns. use the session strategy already in place. test the complete flow from login to protected page access.
```

---

### Anti-Pattern: The Technology Vacuum

**BAD:**
```
add a database
```

**WHY IT FAILS:** Which database? What schema? What connection library? What patterns?

**GOOD:**
```
add PostgreSQL using the existing Prisma setup. create a new Product model with: id, name, price, description, createdAt. follow the User model in @prisma/schema.prisma for patterns. add a migration and seed some test data.
```

---

### Anti-Pattern: The Assumed Knowledge

**BAD:**
```
do the same thing for products
```

**WHY IT FAILS:** Assumes Claude remembers what was done and where.

**GOOD:**
```
create a ProductRepository following the same pattern as UserRepository in @src/repositories/UserRepository.ts. include methods for: findAll, findById, create, update, delete. use the same database connection approach.
```

---

## Verification Anti-Patterns

### Anti-Pattern: The Trust-and-Ship

**BAD:**
```
implement email validation
```

**WHY IT FAILS:** No way to verify correctness. Plausible-looking code might not handle edge cases.

**GOOD:**
```
implement validateEmail function. test cases: [email protected] → true, invalid → false, [email protected] → false, empty string → false. run the tests after implementing.
```

---

### Anti-Pattern: The Visual Guess

**BAD:**
```
make the dashboard look good
```

**WHY IT FAILS:** No design reference to compare against.

**GOOD:**
```
[paste screenshot] implement this design. take a screenshot of the result and compare to the original. list differences and fix them.
```

---

### Anti-Pattern: The Symptom Suppression

**BAD:**
```
make the error go away
```

**WHY IT FAILS:** Encourages suppressing errors rather than fixing root causes.

**GOOD:**
```
the build fails with this error: [paste error]. fix the root cause, don't suppress the error with @ts-ignore. run the build to verify it succeeds.
```

---

### Anti-Pattern: The Unchecked Refactor

**BAD:**
```
refactor the utilities
```

**WHY IT FAILS:** Refactoring without verification often introduces regressions.

**GOOD:**
```
refactor utils.js to use modern JavaScript features. maintain the same behavior. run the existing test suite after each change to ensure nothing breaks. add tests for any untested functions before refactoring them.
```

---

### Anti-Pattern: The Deployment Prayer

**BAD:**
```
should be ready to deploy
```

**WHY IT FAILS:** No verification steps. "Should be" isn't certainty.

**GOOD:**
```
verify the changes are ready for deployment:
1. run the full test suite
2. run the linter
3. run the type checker
4. build for production
5. test the build locally
list any issues found.
```

---

## Scope Anti-Patterns

### Anti-Pattern: The Kitchen Sink

**BAD:**
```
fix the login bug, also update the styling, and add some tests, and maybe refactor the auth module
```

**WHY IT FAILS:** Too many unrelated tasks mixed together. Context gets polluted.

**GOOD:**
Split into separate prompts, use `/clear` between:
1. "fix the login bug in src/auth/. write a failing test first, then fix it."
2. (new session) "update the login page styling to match this mockup: [paste]"
3. (new session) "add tests for the auth module covering: login, logout, token refresh"

---

### Anti-Pattern: The Infinite Scope

**BAD:**
```
add tests for everything
```

**WHY IT FAILS:** Unscoped. Will read hundreds of files filling context.

**GOOD:**
```
add tests for @src/services/PaymentService.ts covering:
- calculateTotal with various inputs
- validateCard (valid/expired/invalid)
- processPayment (success/failure)
target 80% coverage for this file.
```

---

### Anti-Pattern: The Implied Requirements

**BAD:**
```
add user management
```

**WHY IT FAILS:** What does "user management" include? List users? Edit? Delete? Roles?

**GOOD:**
```
add user management to the admin panel:
- list users with pagination (20 per page)
- view user details
- edit user email and role
- soft-delete user (no hard delete)
follow the admin patterns in @src/admin/ProductManagement.tsx
```

---

### Anti-Pattern: The Unbounded Investigation

**BAD:**
```
figure out why the app is slow
```

**WHY IT FAILS:** Could lead to reading the entire codebase.

**GOOD:**
```
the product listing page takes 5+ seconds to load. profile using Chrome DevTools:
1. identify the slowest network requests
2. check for blocking resources
3. look for long JavaScript execution
report the top 3 bottlenecks with suggested fixes.
```

---

### Anti-Pattern: The Feature Creep

**BAD:**
```
add a search feature with autocomplete and fuzzy matching and recent searches and trending suggestions
```

**WHY IT FAILS:** Combines multiple features. Should be phased.

**GOOD:**
Start with MVP:
```
add basic search to the products page:
- text input with search button
- filter products by name (case-insensitive contains)
- show "no results" when empty
follow the existing input patterns in @src/components/forms/
```
Then iterate in follow-up prompts.

---

## Instruction Anti-Patterns

### Anti-Pattern: The Dictation

**BAD:**
```
open src/utils.js, go to line 45, change the if statement to check for null, then save the file, then open tests/utils.test.js and add a test
```

**WHY IT FAILS:** Micromanaging Claude instead of delegating.

**GOOD:**
```
update the getUserById function in src/utils.js to handle null user IDs gracefully. add a test for the null case. run the tests after.
```

---

### Anti-Pattern: The Contradictory Instructions

**BAD:**
```
add comprehensive tests but keep it simple and quick
```

**WHY IT FAILS:** Contradictory. Comprehensive takes time. Quick isn't comprehensive.

**GOOD:**
Choose one:
- "add tests covering the critical paths: login, checkout, account creation"
- "add comprehensive tests for the payment module including all edge cases"

---

### Anti-Pattern: The Unsaid Constraint

**BAD:**
```
add a date picker
```
(User actually wanted no external dependencies, but didn't say so)

**WHY IT FAILS:** Claude might add a library when user wanted vanilla implementation.

**GOOD:**
```
add a date picker to the form. build from scratch without external libraries. use only the utilities already in the codebase. follow the existing form input patterns.
```

---

### Anti-Pattern: The Vague Rejection

**BAD:**
```
that's not quite right
```

**WHY IT FAILS:** No specific feedback about what's wrong or what's expected.

**GOOD:**
```
the date format should be MM/DD/YYYY not YYYY-MM-DD. also the validation should reject dates in the past. update the function and its tests.
```

---

### Anti-Pattern: The Suppressed Error

**BAD:**
```
add a try/catch to stop the error
```

**WHY IT FAILS:** Encourages hiding problems instead of fixing them.

**GOOD:**
```
the function throws when receiving null. add proper null validation at the start of the function. if null, return a sensible default or throw a descriptive error. add a test for the null case.
```

---

## Session Anti-Patterns

### Anti-Pattern: The Eternal Session

**BAD:**
Working on multiple unrelated tasks without clearing:
```
> fix the login bug
[work happens]
> also add the search feature
[more work]
> and refactor the utilities
[context is now full of three unrelated things]
```

**WHY IT FAILS:** Context fills with irrelevant information from previous tasks.

**GOOD:**
```
> fix the login bug...
[work completes]
> /clear
> add the search feature...
```

---

### Anti-Pattern: The Correction Spiral

**BAD:**
```
> do X
> no, I meant Y
> that's not right either, try Z
> still wrong, maybe A?
> let me explain again...
```

**WHY IT FAILS:** Context polluted with failed approaches. Claude gets confused.

**GOOD:**
After 2 failed corrections, `/clear` and write a better initial prompt:
```
> /clear
> implement [clear description with specific requirements and verification]. follow patterns in @[similar code].
```

---

### Anti-Pattern: The Overstuffed CLAUDE.md

**BAD:**
A 2000-line CLAUDE.md with every possible instruction.

**WHY IT FAILS:** Claude ignores important rules lost in the noise.

**GOOD:**
Keep CLAUDE.md concise:
- Commands Claude can't guess
- Style rules that differ from defaults
- Critical project-specific conventions
Move details to linked documents or skills.

---

### Anti-Pattern: The Context Hog

**BAD:**
```
read all the files in src/ and then tell me about the architecture
```

**WHY IT FAILS:** Reads entire codebase into context, leaving no room for actual work.

**GOOD:**
```
read the main entry point and top-level directories to understand the architecture. don't read every file - just enough to explain the main patterns.
```
Or use subagents:
```
use a subagent to investigate the codebase architecture and report a summary.
```

---

### Anti-Pattern: The Lost History

**BAD:**
```
do what we discussed earlier
```

**WHY IT FAILS:** After compaction, earlier discussion might be summarized or lost.

**GOOD:**
Be explicit about what was decided:
```
implement the user notification system using WebSocket as we decided. the spec is in @NOTIFICATIONS_SPEC.md. start with the backend WebSocket handler.
```
Or use ledger files to track state across sessions.

---

## Summary: Quick Fix Reference

| Anti-Pattern | Quick Fix |
|--------------|-----------|
| Generic request | Add symptom + location + verification |
| Ambiguous improvement | Specify exact changes |
| Locationless request | Add file paths with `@` |
| Pattern-free feature | Reference similar existing code |
| Trust-and-ship | Add test cases with expected outputs |
| Visual guess | Paste screenshot for comparison |
| Kitchen sink | Split tasks, `/clear` between |
| Infinite scope | Bound to specific files/functions |
| Dictation | Delegate outcome, not steps |
| Vague rejection | Specify what's wrong and expected |
| Eternal session | `/clear` between unrelated tasks |
| Correction spiral | After 2 fails, `/clear` + better prompt |
