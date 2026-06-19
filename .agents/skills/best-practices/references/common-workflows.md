# Common Workflow Prompts

This document contains optimized prompts for common development workflows. Use these as templates when transforming prompts for specific task types.

## Table of Contents

1. [Codebase Understanding](#codebase-understanding)
2. [Bug Fixing](#bug-fixing)
3. [Feature Development](#feature-development)
4. [Testing](#testing)
5. [Refactoring](#refactoring)
6. [Code Review](#code-review)
7. [Documentation](#documentation)
8. [Git Operations](#git-operations)
9. [DevOps Tasks](#devops-tasks)
10. [Database Operations](#database-operations)

---

## Codebase Understanding

### Quick Overview

```
give me an overview of this codebase:
- main technologies and frameworks
- high-level architecture
- key directories and their purposes
- entry points (main files, API routes)
```

### Understand a Module

```
explain the [module name] module in @[path]:
- what problem does it solve?
- what are the main components/classes/functions?
- how does it integrate with other parts of the codebase?
- what are the key data flows?
```

### Trace a Flow

```
trace the [flow name] from start to finish:
1. where does it start? (user action, API call, etc.)
2. what components/services does it pass through?
3. what data transformations happen?
4. where does it end? (database, response, side effect)
list the files involved in order.
```

### Find Related Code

```
find all code related to [feature/concept]:
- search for [relevant terms]
- identify the main files that implement it
- find tests for this functionality
- note any configuration or environment dependencies
```

### Understand a Decision

```
look through git history for @[file/directory] and explain:
- when was this approach chosen?
- what alternatives were considered (check PRs/issues)?
- why was this decision made?
- are there any TODO comments or known limitations?
```

---

## Bug Fixing

### Investigate and Fix

```
[describe symptom in detail]

INVESTIGATE:
1. reproduce the issue using: [steps or conditions]
2. check [likely locations]
3. add logging if needed to trace the flow
4. identify the root cause, not just the symptom

FIX:
1. write a failing test that reproduces the bug
2. implement the fix
3. verify the test passes
4. check for similar issues elsewhere

VERIFY:
- run the full test suite
- manually test the fix
- confirm no regressions
```

### Debug Build Failure

```
the build fails with this error:
[paste actual error]

investigate:
1. what file/line is causing the error?
2. what changed recently that might have caused this?
3. is this a type error, syntax error, or dependency issue?

fix the root cause (don't use @ts-ignore or suppress the error).
run `[build command]` to verify the fix.
```

### Debug Runtime Error

```
getting this error at runtime:
[paste error with stack trace]

investigate:
1. what is the immediate cause?
2. what input/state leads to this error?
3. where should validation/handling be added?

fix:
1. add proper error handling or validation
2. write a test for this case
3. verify the error no longer occurs
```

### Performance Issue

```
[describe performance symptom]

PROFILE:
1. measure current performance: [how]
2. identify the bottleneck using [tools/approach]
3. document baseline metrics

OPTIMIZE:
1. implement the most impactful fix first
2. measure improvement
3. repeat if needed

TARGET: [specific metric goal]
verify with [benchmark/test]
```

---

## Feature Development

### New Feature (Full Workflow)

```
implement [feature description]

PHASE 1 - UNDERSTAND:
- read @[related code] to understand existing patterns
- identify where the new feature integrates
- note any dependencies or constraints

PHASE 2 - PLAN:
- list the components/files that need to be created/modified
- define the data model if applicable
- identify edge cases to handle
- write the plan to [location] for review

PHASE 3 - IMPLEMENT:
- follow existing patterns from @[example file]
- [specific implementation steps]
- run tests after each significant change

PHASE 4 - VERIFY:
- add tests for: [specific test cases]
- manual testing: [testing steps]
- verify [success criteria]
```

### Add UI Component

```
create a [component name] component following existing patterns.

REFERENCE: @[similar component path]

REQUIREMENTS:
- [visual/behavior requirement 1]
- [visual/behavior requirement 2]
- responsive at [breakpoints]
- accessible (keyboard navigation, ARIA)

IMPLEMENTATION:
1. create component in @[path]
2. add styles following @[style patterns]
3. add to storybook if applicable
4. write tests for: [test cases]

VERIFY:
- visual check at all breakpoints
- keyboard navigation works
- screen reader announces correctly
```

### Add API Endpoint

```
add [HTTP method] /api/[path] endpoint.

REFERENCE: follow the pattern in @[similar endpoint]

REQUIREMENTS:
- input validation: [validation rules]
- authentication: [auth requirements]
- response format: [describe response]
- error handling: [error cases]

IMPLEMENTATION:
1. add route handler in @[router location]
2. add validation middleware/logic
3. implement business logic in @[service location]
4. add to API documentation

TEST:
- success case
- validation errors
- auth errors
- not found (if applicable)
```

### Add Database Feature

```
add [database feature description].

MIGRATION:
1. create migration in @[migrations path]
2. [describe schema changes]
3. make migration reversible

MODEL:
1. update model in @[model path]
2. add types in @[types path]
3. update repository methods

VERIFY:
1. run migration locally
2. verify with a query
3. run existing tests (no regressions)
4. add new tests for the feature
```

---

## Testing

### Add Unit Tests

```
add unit tests for @[file path].

COVERAGE:
- [function 1]: test [cases]
- [function 2]: test [cases]
- edge cases: [list]
- error cases: [list]

APPROACH:
- follow patterns in @[existing test file]
- mock [external dependencies]
- use [test data approach]

TARGET: [coverage percentage]% coverage for this file
run tests after implementing each test case.
```

### Add Integration Tests

```
add integration tests for [feature/flow].

TEST THE COMPLETE FLOW:
1. [step 1]
2. [step 2]
3. [step 3]

SCENARIOS:
- happy path: [describe]
- error case 1: [describe]
- error case 2: [describe]
- edge case: [describe]

SETUP:
- use @[test setup file] for database/fixtures
- mock only [external services]
- use real [internal services]
```

### Add E2E Tests

```
add end-to-end tests for [user flow].

USER JOURNEY:
1. user [action 1]
2. user [action 2]
3. user [action 3]
4. verify [final state]

TEST CASES:
- complete flow succeeds
- [error scenario 1]
- [error scenario 2]

IMPLEMENTATION:
- use [E2E framework] in @[test directory]
- follow patterns in @[existing E2E test]
- use test fixtures for data
```

### Fix Failing Tests

```
the following tests are failing:
[paste test output]

INVESTIGATE:
1. run each test individually to reproduce
2. identify if it's a test problem or code problem
3. check recent changes that might have caused this

FIX:
- if test is wrong: update test to match correct behavior
- if code is wrong: fix code, not the test
- run full suite to check for ripple effects
```

---

## Refactoring

### Extract Component/Function

```
extract [what to extract] from @[source file] into [new location].

IDENTIFY:
- lines [X-Y] in source file
- what inputs does it need?
- what does it return/produce?

EXTRACT:
1. create new [file/function/component] at @[path]
2. move the code, add proper types
3. update imports in original file
4. export from new location

VERIFY:
- all tests still pass
- no behavior changes
- lint passes
```

### Consolidate Duplicates

```
consolidate duplicate [code type] across:
- @[file 1]: lines [X-Y]
- @[file 2]: lines [X-Y]
- @[file 3]: lines [X-Y]

CREATE:
1. shared utility in @[new location]
2. parameterize differences
3. add proper types

UPDATE:
1. replace each duplicate with shared utility
2. run tests after each replacement

VERIFY:
- behavior unchanged (tests pass)
- no more duplicates (search confirms)
```

### Modernize Code

```
modernize @[file path]:
- convert [old pattern] to [new pattern]
- update syntax to [standard/version]
- add TypeScript types if missing

CHANGES TO MAKE:
1. [specific change 1]
2. [specific change 2]
3. [specific change 3]

CONSTRAINTS:
- maintain same public API
- all existing tests must pass
- make one change at a time, test after each
```

---

## Code Review

### Review for Quality

```
review @[file/PR] for code quality:
- naming clarity and consistency
- function/method size and complexity
- proper error handling
- appropriate comments (not too many, not too few)
- following project conventions from @[CLAUDE.md or style guide]

provide specific line references for any issues.
```

### Review for Security

```
review @[file/module] for security:
- input validation completeness
- SQL injection vulnerabilities
- XSS vulnerabilities
- authentication/authorization checks
- sensitive data handling
- error messages that leak information

rate each issue by severity (critical/high/medium/low).
provide fix suggestions.
```

### Review for Performance

```
review @[file/module] for performance:
- unnecessary re-renders (React)
- N+1 queries
- missing indexes (if database)
- unoptimized loops
- memory leaks (event listeners, subscriptions)
- large bundle imports

estimate impact of each issue.
suggest fixes with expected improvement.
```

---

## Documentation

### Document API

```
add documentation for @[API file]:

FOR EACH ENDPOINT:
- HTTP method and path
- description of what it does
- request parameters/body (with types)
- response format (with types)
- possible error codes
- authentication requirements
- example request/response

format as [OpenAPI/JSDoc/markdown].
follow existing docs in @[existing docs].
```

### Document Component

```
add documentation for @[component file]:
- what the component does
- props with types and descriptions
- usage examples
- accessibility considerations
- related components

add as JSDoc comments and/or storybook stories.
```

### Document Function

```
add JSDoc documentation to functions in @[file]:
- @description - what it does
- @param - each parameter with type
- @returns - return type and meaning
- @throws - errors that can be thrown
- @example - usage example

follow the documentation style in @[similar documented file].
```

---

## Git Operations

### Create Meaningful Commit

```
review the current changes and create a commit:
1. run `git diff` to see all changes
2. group related changes if needed
3. write a descriptive commit message:
   - first line: type(scope): brief description
   - blank line
   - body: explain WHY, not just WHAT
4. commit the changes
```

### Create PR

```
create a pull request for the current changes:

1. verify all changes are committed
2. push to remote
3. create PR with:
   - clear title summarizing the change
   - description explaining:
     - what changed and why
     - how to test
     - any breaking changes
     - related issues
4. request appropriate reviewers
```

### Resolve Merge Conflict

```
resolve merge conflict between [branch A] and [branch B]:

1. understand what each branch changed:
   - [branch A] changed: [what]
   - [branch B] changed: [what]

2. determine correct resolution:
   - keep both changes? how do they combine?
   - keep one? which is correct?
   - need new code? what's the right merge?

3. resolve the conflict
4. run tests to verify nothing broke
5. commit the resolution with clear message
```

---

## DevOps Tasks

### Set Up CI Pipeline

```
add CI pipeline in .github/workflows/ci.yml:

TRIGGERS:
- push to main
- all pull requests

JOBS:
1. install dependencies (cache node_modules)
2. lint (npm run lint)
3. type check (npm run typecheck)
4. test with coverage (npm run test:coverage)
5. build (npm run build)

REQUIREMENTS:
- fail if any step fails
- fail if coverage below [X]%
- add status checks to PR

follow patterns from @[existing workflow file].
```

### Create Dockerfile

```
create Dockerfile for the application:

REQUIREMENTS:
- multi-stage build (builder + production)
- use [base image]
- optimize for small final image
- proper layer caching for dependencies
- non-root user for security
- health check endpoint

create docker-compose.yml for local development with:
- app service with hot reloading
- [database service]
- [other services]

test with `docker-compose up` and verify app works.
```

### Add Monitoring

```
add monitoring/logging to @[service/app]:

LOGGING:
- structured JSON logs
- include: timestamp, level, message, request ID
- log levels: error, warn, info, debug
- sensitive data redaction

METRICS:
- request duration
- error rate
- [custom metrics]

ALERTS:
- error rate > [threshold]
- latency > [threshold]

follow patterns in @[existing instrumented service].
```

---

## Database Operations

### Create Migration

```
create database migration for: [describe change]

MIGRATION:
1. create migration file in @[migrations directory]
2. name: [timestamp]_[descriptive_name]
3. implement:
   - up: [changes to apply]
   - down: [how to reverse]

VERIFY:
1. run migration locally
2. verify with query
3. run rollback
4. verify rollback worked
5. run migration again
```

### Optimize Query

```
optimize slow query in @[repository/file]:

CURRENT QUERY: [describe or paste]
CURRENT PERFORMANCE: [time/explain output]

INVESTIGATE:
1. run EXPLAIN ANALYZE
2. identify missing indexes
3. check for N+1 queries
4. look for unnecessary columns/joins

OPTIMIZE:
1. add indexes if needed (via migration)
2. rewrite query if needed
3. add pagination if missing

TARGET: [performance goal]
measure and document improvement.
```

### Add Seed Data

```
create seed script for [purpose] in @[seeds directory]:

DATA TO CREATE:
- [X] records of [type 1]
- [X] records of [type 2]
- proper relationships between entities
- realistic data (use Faker if available)

REQUIREMENTS:
- idempotent (safe to run multiple times)
- clean up option
- environment-aware (don't run in production)

add `npm run db:seed` script to package.json.
verify by running and checking database.
```
