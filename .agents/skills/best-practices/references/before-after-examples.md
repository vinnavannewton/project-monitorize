# Before/After Prompt Transformation Examples

This document contains 50+ examples of prompt transformations organized by category. Each example shows the original suboptimal prompt and the optimized version following Claude Code best practices.

## Table of Contents

1. [Verification & Testing](#verification--testing)
2. [Bug Fixes & Debugging](#bug-fixes--debugging)
3. [Feature Implementation](#feature-implementation)
4. [Refactoring](#refactoring)
5. [UI & Frontend](#ui--frontend)
6. [API & Backend](#api--backend)
7. [Database](#database)
8. [Testing](#testing)
9. [Documentation](#documentation)
10. [Code Review](#code-review)
11. [DevOps & CI/CD](#devops--cicd)
12. [Security](#security)
13. [Performance](#performance)
14. [Git & Version Control](#git--version-control)

---

## Verification & Testing

### Example 1: Email Validation

**BEFORE:**
```
implement a function that validates email addresses
```

**AFTER:**
```
write a validateEmail function. test cases:
- [email protected] → true
- invalid → false
- [email protected] → false
- user@domain → false
- @domain.com → false
run the tests after implementing
```

**Why it's better:** Provides specific test cases with expected outputs, giving Claude verification criteria.

---

### Example 2: Password Strength Checker

**BEFORE:**
```
create a password strength validator
```

**AFTER:**
```
create a checkPasswordStrength function that returns 'weak', 'medium', or 'strong'. test cases:
- "abc" → weak (too short)
- "password123" → weak (common pattern)
- "MyP@ssw0rd!" → strong (mixed case, numbers, symbols, 10+ chars)
- "abcdefghij" → medium (long but no variety)
run tests after implementing. place in src/utils/validation.ts following existing util patterns.
```

---

### Example 3: Date Parsing

**BEFORE:**
```
write a date parser
```

**AFTER:**
```
write a parseDate function in src/utils/dates.ts that handles multiple formats. test cases:
- "2024-01-15" → Date object for Jan 15, 2024
- "01/15/2024" → Date object for Jan 15, 2024
- "January 15, 2024" → Date object for Jan 15, 2024
- "invalid" → null
- "" → null
follow the existing utility function patterns in @src/utils/. run tests after implementing.
```

---

## Bug Fixes & Debugging

### Example 4: Generic Bug Report

**BEFORE:**
```
fix the login bug
```

**AFTER:**
```
users report that login fails after session timeout. check the auth flow in src/auth/, especially token refresh. write a failing test that reproduces the issue, then fix it. verify by running the auth test suite.
```

**Why it's better:** Describes the symptom, points to specific location, requests a failing test first.

---

### Example 5: Build Failure

**BEFORE:**
```
the build is failing
```

**AFTER:**
```
the build fails with this error:
[paste actual error message here]

fix it and verify the build succeeds. address the root cause, don't suppress the error. run `npm run build` after fixing to confirm.
```

---

### Example 6: Undefined Error

**BEFORE:**
```
getting undefined error
```

**AFTER:**
```
getting "Cannot read property 'user' of undefined" error on line 45 of src/components/Dashboard.tsx when loading the dashboard without being logged in. check the auth context provider and add proper null handling. write a test for the unauthenticated state, then fix it.
```

---

### Example 7: Race Condition

**BEFORE:**
```
there's a race condition somewhere
```

**AFTER:**
```
users intermittently see stale data after updating their profile. I suspect a race condition between the optimistic update and the API response in src/hooks/useProfile.ts. add console logs to trace the update flow, identify the race condition, then fix it. add a test that simulates the race condition by delaying the API response.
```

---

### Example 8: Memory Leak

**BEFORE:**
```
fix the memory leak
```

**AFTER:**
```
the app's memory usage grows over time when navigating between pages. I suspect event listeners aren't being cleaned up. check src/components/ for useEffect hooks that add listeners without cleanup. profile the heap in Chrome DevTools to identify the leak source. fix any missing cleanup functions and verify memory stays stable during navigation.
```

---

## Feature Implementation

### Example 9: Calendar Widget

**BEFORE:**
```
add a calendar widget
```

**AFTER:**
```
look at how existing widgets are implemented on the home page to understand the patterns. HotDogWidget.php is a good example. follow the pattern to implement a new calendar widget that lets the user select a month and paginate forwards/backwards to pick a year. build from scratch without libraries other than the ones already used in the codebase. add tests for month selection and pagination.
```

**Why it's better:** References existing patterns, specifies exact functionality, sets constraints.

---

### Example 10: Search Feature

**BEFORE:**
```
add search functionality
```

**AFTER:**
```
add a search feature to the products page following the existing search pattern in @src/components/UserSearch.tsx. requirements:
- debounce input by 300ms
- show loading spinner during search
- display "no results" when empty
- highlight matching text in results
add tests for debouncing and empty states. place component in src/components/ProductSearch.tsx.
```

---

### Example 11: Pagination

**BEFORE:**
```
add pagination to the list
```

**AFTER:**
```
add cursor-based pagination to the /api/products endpoint and ProductList component. follow the existing pagination pattern in @src/api/users.ts and @src/components/UserList.tsx. requirements:
- 20 items per page
- "Load more" button (not page numbers)
- preserve scroll position when loading more
- handle loading and error states
add API tests and component tests. verify by loading the products page and clicking "Load more" multiple times.
```

---

### Example 12: Dark Mode

**BEFORE:**
```
add dark mode
```

**AFTER:**
```
add dark mode toggle to the settings page. implementation:
1. read src/styles/ to understand current theming approach
2. add CSS variables for dark theme colors in src/styles/themes.css
3. create a ThemeContext in src/context/ThemeContext.tsx
4. add toggle switch to Settings.tsx
5. persist preference in localStorage
6. respect system preference as default
follow existing context patterns in @src/context/AuthContext.tsx. test the toggle persists across page reloads.
```

---

### Example 13: File Upload

**BEFORE:**
```
implement file upload
```

**AFTER:**
```
implement file upload for user avatars in the profile settings. requirements:
- accept only jpg, png, webp under 5MB
- show preview before upload
- display progress bar during upload
- handle errors gracefully with user-friendly messages
follow the existing form patterns in @src/components/ProfileForm.tsx. backend endpoint is POST /api/upload. add tests for file validation and error handling. verify by uploading various file types and sizes.
```

---

## Refactoring

### Example 14: Generic Refactor

**BEFORE:**
```
make the code better
```

**AFTER:**
```
refactor utils.js to use ES2024 features while maintaining the same behavior. specifically:
- convert callbacks to async/await
- use optional chaining where applicable
- add proper TypeScript types
run the existing test suite after each change to ensure nothing breaks.
```

---

### Example 15: Extract Component

**BEFORE:**
```
refactor this component
```

**AFTER:**
```
the UserDashboard component in @src/components/UserDashboard.tsx is 500+ lines. extract these sections into separate components:
- UserStats (lines 45-120)
- RecentActivity (lines 150-250)
- QuickActions (lines 280-350)
keep the parent component as the layout orchestrator. ensure props are typed correctly. run the existing tests after each extraction to ensure nothing breaks.
```

---

### Example 16: Remove Duplication

**BEFORE:**
```
DRY up this code
```

**AFTER:**
```
src/api/users.ts and src/api/products.ts have duplicate error handling logic (lines 20-45 in each). extract a shared handleApiError function to src/api/utils.ts. requirements:
- handle 401, 403, 404, 500 errors
- return typed error objects
- log errors to our logging service
update both files to use the shared function. run the API tests after to verify behavior is unchanged.
```

---

### Example 17: Modernize Patterns

**BEFORE:**
```
update the old code
```

**AFTER:**
```
modernize src/legacy/OrderProcessor.js:
- convert from class to functional approach with hooks
- replace var with const/let
- convert promises to async/await
- add TypeScript types (rename to .ts)
- update imports from CommonJS to ES modules
make one change at a time, running tests after each to catch regressions. the existing tests are in tests/legacy/OrderProcessor.test.js (update to .ts as well).
```

---

## UI & Frontend

### Example 18: UI Improvements

**BEFORE:**
```
make the dashboard look better
```

**AFTER:**
```
[paste screenshot of target design]

implement this design for the dashboard. focus on:
- card layout with shadows
- proper spacing (use existing spacing variables from @src/styles/variables.css)
- responsive grid (2 columns on tablet, 1 on mobile)
take a screenshot of the result and compare it to the original design. list differences and fix them.
```

**Why it's better:** Includes visual reference, specifies responsive requirements, asks for verification.

---

### Example 19: Form Styling

**BEFORE:**
```
style the form
```

**AFTER:**
```
[paste screenshot of design mockup]

style the registration form to match this mockup. requirements:
- input fields: 48px height, 8px border-radius, 1px border
- labels: 14px, 500 weight, above inputs
- error states: red border, error message below
- submit button: full width, primary color from theme
use existing CSS variables from @src/styles/. verify all states: empty, filled, error, disabled. test at 320px, 768px, and 1024px widths.
```

---

### Example 20: Animation

**BEFORE:**
```
add animations
```

**AFTER:**
```
add subtle entrance animations to the card grid on the home page. requirements:
- cards fade in and slide up 20px
- stagger delay: 50ms between each card
- duration: 300ms with ease-out
- use CSS animations, not JavaScript
- respect prefers-reduced-motion
follow the existing animation patterns in @src/styles/animations.css. test with reduced motion enabled in browser settings.
```

---

### Example 21: Accessibility

**BEFORE:**
```
make it accessible
```

**AFTER:**
```
audit the navigation component in @src/components/Nav.tsx for accessibility. check and fix:
- keyboard navigation (Tab, Enter, Escape)
- ARIA labels for icon-only buttons
- focus indicators visible on all interactive elements
- screen reader announcements for menu open/close
- color contrast meets WCAG AA
test with keyboard-only navigation and VoiceOver. run `npm run test:a11y` after fixes.
```

---

## API & Backend

### Example 22: Generic API Question

**BEFORE:**
```
why does ExecutionFactory have such a weird api?
```

**AFTER:**
```
look through ExecutionFactory's git history and summarize how its api came to be. specifically:
- when was it created and by whom?
- what were the major changes and why?
- are there any related issues or PRs that explain design decisions?
```

**Why it's better:** Points to sources (git history), asks for specific investigation.

---

### Example 23: New Endpoint

**BEFORE:**
```
add an API endpoint
```

**AFTER:**
```
add a GET /api/products/:id endpoint following the pattern in @src/api/users.ts. requirements:
- return 404 if product not found
- include related category data (JOIN)
- cache response for 5 minutes
- add rate limiting (100 req/min)
- validate :id is a valid UUID
add tests for success, not found, invalid id, and rate limiting. document in the API docs.
```

---

### Example 24: Authentication

**BEFORE:**
```
add auth
```

**AFTER:**
```
read src/auth/ to understand current session handling, then add Google OAuth. implementation plan:
1. add Google OAuth credentials to .env.example
2. create callback handler in src/auth/google.ts
3. update session to store OAuth tokens
4. add "Sign in with Google" button to login page
5. handle account linking for existing users
follow the existing auth patterns. write tests for the callback handler including error cases. document the setup steps in README.
```

---

### Example 25: Webhooks

**BEFORE:**
```
implement webhooks
```

**AFTER:**
```
add a webhook system for order status updates. requirements:
- POST /api/webhooks/register endpoint to register URLs
- validate webhook URLs are HTTPS
- sign payloads with HMAC-SHA256
- retry failed deliveries 3 times with exponential backoff
- log all delivery attempts
follow the event pattern in @src/events/. store registrations in the webhooks table (create migration). add tests for registration validation, signature verification, and retry logic.
```

---

## Database

### Example 26: Migration

**BEFORE:**
```
add a new column
```

**AFTER:**
```
add an 'archived_at' nullable timestamp column to the products table. steps:
1. create migration in db/migrations/
2. update Product model in src/models/Product.ts
3. update ProductRepository to filter out archived by default
4. add 'includeArchived' option to list queries
follow the migration pattern in @db/migrations/20240101_add_user_status.sql. run migration locally and verify with a query. add test for archive filtering.
```

---

### Example 27: Query Optimization

**BEFORE:**
```
the query is slow
```

**AFTER:**
```
the getOrdersWithProducts query in src/repositories/OrderRepository.ts takes 3+ seconds for users with many orders. current query is on line 45. profile the query with EXPLAIN ANALYZE:
- identify missing indexes
- check for N+1 queries
- consider pagination
add any needed indexes via migration. target: under 100ms for 1000 orders. run the performance test in tests/performance/orders.test.ts before and after.
```

---

### Example 28: Seeding

**BEFORE:**
```
add test data
```

**AFTER:**
```
create a database seed script in db/seeds/development.ts that creates:
- 10 users with varied roles (2 admin, 3 manager, 5 regular)
- 50 products across 5 categories
- 100 orders with realistic date distribution over past 90 days
- proper relationships between entities
use Faker.js for realistic data. follow the seed pattern in @db/seeds/categories.ts. add npm script "db:seed" to package.json. verify by running seed and checking counts.
```

---

## Testing

### Example 29: Generic Test Request

**BEFORE:**
```
add tests for foo.py
```

**AFTER:**
```
write a test for foo.py covering the edge case where the user is logged out. avoid mocks. test cases:
- logged_out_user returns 401
- expired_session redirects to login
- invalid_token raises AuthError
follow the test patterns in @tests/auth/. run the new tests after implementing.
```

**Why it's better:** Specifies exact edge case, provides test cases, states constraints (no mocks).

---

### Example 30: Integration Tests

**BEFORE:**
```
add integration tests
```

**AFTER:**
```
add integration tests for the order checkout flow in tests/integration/checkout.test.ts. test the complete flow:
1. add items to cart
2. apply discount code
3. enter shipping info
4. process payment (use test Stripe key)
5. verify order created in database
6. verify confirmation email sent (mock email service only)
cover error cases: invalid card, out of stock, expired discount. use the test database setup in @tests/setup.ts.
```

---

### Example 31: Snapshot Tests

**BEFORE:**
```
add snapshot tests
```

**AFTER:**
```
add snapshot tests for the ProductCard component covering these variants:
- default state
- on sale (with discount badge)
- out of stock (with overlay)
- loading state
place in tests/components/ProductCard.snapshot.test.tsx. use the existing snapshot config in @jest.config.js. run and commit the initial snapshots.
```

---

## Documentation

### Example 32: API Docs

**BEFORE:**
```
document the API
```

**AFTER:**
```
add OpenAPI/Swagger documentation for the products API endpoints. include:
- GET /api/products (list with pagination params)
- GET /api/products/:id (single product)
- POST /api/products (create, admin only)
- PUT /api/products/:id (update)
- DELETE /api/products/:id (soft delete)
document request/response schemas, auth requirements, and error responses. follow the format in @docs/api/users.yaml. validate the spec with `npm run docs:validate`.
```

---

### Example 33: README

**BEFORE:**
```
update the readme
```

**AFTER:**
```
update README.md with the new authentication flow. add:
- environment variables needed (.env.example reference)
- setup steps for Google OAuth credentials
- how to run locally with OAuth disabled (for development)
- troubleshooting section for common auth errors
keep existing sections intact. follow the documentation style of existing README sections.
```

---

## Code Review

### Example 34: Generic Review

**BEFORE:**
```
review my code
```

**AFTER:**
```
review the changes in @src/services/PaymentService.ts for:
- security issues (especially around handling card data)
- error handling completeness
- edge cases not covered
- consistency with existing service patterns in @src/services/
- test coverage gaps
provide specific line references for any issues found.
```

---

### Example 35: PR Review

**BEFORE:**
```
review this PR
```

**AFTER:**
```
review PR #123 for the new notification system. focus on:
- does the implementation match the spec in @docs/specs/notifications.md?
- are there race conditions in the real-time updates?
- is the database schema migration reversible?
- are error states handled in the UI?
- is test coverage sufficient for the critical paths?
provide actionable feedback with code suggestions where applicable.
```

---

## DevOps & CI/CD

### Example 36: CI Setup

**BEFORE:**
```
set up CI
```

**AFTER:**
```
add GitHub Actions workflow for CI in .github/workflows/ci.yml. the workflow should:
- run on push to main and all PRs
- install dependencies with npm ci
- run linting (npm run lint)
- run type checking (npm run typecheck)
- run tests with coverage (npm run test:coverage)
- fail if coverage drops below 80%
- cache node_modules between runs
follow the workflow pattern in @.github/workflows/deploy.yml for caching strategy.
```

---

### Example 37: Docker

**BEFORE:**
```
dockerize the app
```

**AFTER:**
```
create Dockerfile and docker-compose.yml for local development. requirements:
- multi-stage build for smaller production image
- node:20-alpine base
- separate services for app, postgres, redis
- mount source code for hot reloading in dev
- health checks for all services
- .dockerignore to exclude node_modules, .git
follow the patterns in @infrastructure/docker/ if present. document docker commands in README. verify with `docker-compose up` and test the app works.
```

---

## Security

### Example 38: Security Audit

**BEFORE:**
```
check for security issues
```

**AFTER:**
```
audit the user input handling in @src/api/ for security vulnerabilities:
- SQL injection in raw queries
- XSS in rendered user content
- CSRF protection on state-changing endpoints
- authentication bypass possibilities
- sensitive data in logs or error messages
- hardcoded secrets or credentials
provide specific file:line references for each issue with remediation steps. prioritize by severity.
```

---

### Example 39: Input Validation

**BEFORE:**
```
add validation
```

**AFTER:**
```
add input validation to the user registration endpoint in src/api/users.ts. validate:
- email: valid format, not already registered
- password: min 8 chars, at least 1 number and 1 special char
- username: 3-20 chars, alphanumeric and underscores only, not taken
return specific error messages for each validation failure. use the validation patterns in @src/utils/validators.ts. add tests for each validation rule including edge cases.
```

---

## Performance

### Example 40: Performance Investigation

**BEFORE:**
```
the page is slow
```

**AFTER:**
```
the product listing page takes 5+ seconds to load. investigate:
1. run Lighthouse audit and report scores
2. check network waterfall for blocking requests
3. profile React components for unnecessary re-renders
4. check API response times in Network tab
5. identify the top 3 performance bottlenecks
then create an action plan with estimated impact for each fix. start with the highest-impact fix.
```

---

### Example 41: Bundle Optimization

**BEFORE:**
```
reduce bundle size
```

**AFTER:**
```
analyze and reduce the JavaScript bundle size. steps:
1. run `npm run build` and report current bundle sizes
2. use webpack-bundle-analyzer to identify large dependencies
3. implement code splitting for routes
4. lazy load heavy components (charts, editors)
5. check for duplicate dependencies
target: main bundle under 200KB gzipped. document changes and new bundle sizes.
```

---

## Git & Version Control

### Example 42: Commit History

**BEFORE:**
```
look at the git history
```

**AFTER:**
```
trace the evolution of the payment processing module. check git history for:
- when was src/payments/ first created?
- what were the major refactors and why (check commit messages and linked PRs)?
- who are the main contributors?
- are there any reverted changes that might explain current quirks?
summarize the key decisions and their rationale.
```

---

### Example 43: Merge Conflict

**BEFORE:**
```
fix the merge conflict
```

**AFTER:**
```
resolve the merge conflict in src/components/Header.tsx between feature/new-nav and main. context:
- feature/new-nav adds a mobile menu
- main updated the logo and added a search bar
- we want both changes
resolve to keep both features working together. run `npm run test` and `npm run typecheck` after resolving to verify nothing broke.
```

---

### Example 44: Branch Cleanup

**BEFORE:**
```
clean up branches
```

**AFTER:**
```
identify branches that can be deleted:
- list branches merged into main more than 30 days ago
- list branches with no commits in 60+ days
- exclude branches matching: release/*, hotfix/*, main, develop
show the list for review before deleting. after approval, delete the remote branches with `git push origin --delete`.
```

---

## Complex Multi-Step Examples

### Example 45: Full Feature Implementation

**BEFORE:**
```
add user notifications
```

**AFTER:**
```
implement a user notification system. phase this work:

PHASE 1 - Explore:
- read existing user-related code in src/users/ and src/components/
- check if we have any notification patterns already
- identify where notifications should integrate

PHASE 2 - Plan:
- design the notification data model
- plan the API endpoints needed
- sketch the UI components
- write the plan to NOTIFICATIONS_PLAN.md for review

PHASE 3 - Implement (after plan approval):
- create database migration for notifications table
- add API endpoints (list, mark read, delete)
- create NotificationBell component for header
- create NotificationList dropdown
- add real-time updates with WebSocket
- write tests for each component

PHASE 4 - Verify:
- run all tests
- manually test the full flow
- check mobile responsiveness
- verify WebSocket reconnection handling
```

---

### Example 46: Legacy System Migration

**BEFORE:**
```
migrate from the old system
```

**AFTER:**
```
migrate OrderProcessor from the legacy PHP system to our Node.js backend.

STEP 1 - Understand:
- read legacy/OrderProcessor.php and document its behavior
- identify all inputs, outputs, and side effects
- note any business logic that isn't obvious from code
- list all database tables it touches

STEP 2 - Plan:
- map PHP functions to TypeScript equivalents
- design the new data models
- identify what can be simplified vs must be preserved exactly
- create migration plan document

STEP 3 - Implement:
- create new OrderProcessor in src/services/
- write comprehensive tests based on legacy behavior
- implement each method, running tests after each
- add logging to match legacy log format

STEP 4 - Validate:
- run both systems in parallel with same inputs
- compare outputs for 100 test orders
- document any differences and justify or fix them
```

---

### Example 47: Debugging Session

**BEFORE:**
```
users can't checkout
```

**AFTER:**
```
users report "checkout failed" error intermittently. investigate:

1. GATHER INFO:
- check error logs in CloudWatch for checkout-related errors
- identify error patterns (time of day, user segments, browsers)
- get sample user IDs who experienced the issue

2. REPRODUCE:
- set up local environment to match production
- attempt to reproduce with sample user data
- add detailed logging to checkout flow if needed

3. IDENTIFY ROOT CAUSE:
- trace the checkout flow for a failing case
- identify where it diverges from success path
- determine if it's frontend, backend, or third-party issue

4. FIX:
- write a test that reproduces the failure
- implement the fix
- verify the test passes
- check for similar issues elsewhere in codebase

5. VERIFY:
- deploy to staging
- test with previously failing scenarios
- monitor error rates after production deploy
```

---

### Example 48: API Versioning

**BEFORE:**
```
version the API
```

**AFTER:**
```
implement API versioning for our REST endpoints.

REQUIREMENTS:
- support v1 (current) and v2 (new) simultaneously
- v1 endpoints remain unchanged for 6 months
- v2 endpoints use new response format
- deprecation warnings in v1 responses

IMPLEMENTATION:
1. read current API structure in src/api/
2. create src/api/v1/ and src/api/v2/ directories
3. move current handlers to v1/
4. create v2/ handlers with new format
5. update router to handle /api/v1/* and /api/v2/*
6. add deprecation headers to v1 responses
7. update API docs for both versions

TESTING:
- ensure all existing tests pass for v1
- add tests for v2 endpoints
- test version routing
- verify deprecation headers present

Follow the existing routing patterns in @src/router.ts.
```

---

### Example 49: Performance Critical Fix

**BEFORE:**
```
make the search faster
```

**AFTER:**
```
the product search takes 8+ seconds for queries with common terms. optimize:

PROFILE FIRST:
1. run EXPLAIN ANALYZE on the search query
2. identify slow operations (full table scan, missing index, etc.)
3. check query plan for the WHERE clause and JOINs

OPTIMIZE:
- add appropriate indexes (document which ones and why)
- consider full-text search index for product names/descriptions
- implement search result caching (5 minute TTL)
- add pagination if not present (100 results max)

VERIFY:
- run EXPLAIN ANALYZE again, compare before/after
- measure response times for common queries
- target: under 200ms for 95th percentile
- load test with 100 concurrent searches

Document the changes and performance improvements in a PR description.
```

---

### Example 50: Complete Testing Suite

**BEFORE:**
```
add tests for the payment module
```

**AFTER:**
```
create comprehensive tests for src/services/PaymentService.ts

UNIT TESTS (tests/unit/PaymentService.test.ts):
- calculateTotal with various inputs (items, discounts, tax)
- validateCard (valid cards, expired, invalid number)
- formatCurrency (different locales)

INTEGRATION TESTS (tests/integration/payment.test.ts):
- full checkout flow with test Stripe API
- refund processing
- webhook handling for payment events
- idempotency for duplicate requests

EDGE CASES:
- zero amount orders
- maximum order value
- currency conversion
- partial refunds
- network timeout handling
- invalid API responses

MOCKING STRATEGY:
- mock Stripe only for unit tests
- use Stripe test mode for integration
- mock database for unit, real DB for integration

Run each test file as you create it. Target 90%+ coverage for the payment module.
```
