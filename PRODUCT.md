# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Product Purpose

Shortlink creates shareable short URLs, redirects visitors, and counts clicks. The original deliverable is a Python API and public GitHub repository with setup instructions and automated tests.

## Operating Context

The user evaluates and uses this project locally. The existing FastAPI service runs on localhost:8001 because another service occupies port 8000. The user explicitly requested both a professional dashboard and additional backend capabilities, and chose direct implementation rather than a preliminary visual mockup.

## Capabilities and Constraints

Preserve original creation, alias uniqueness, expiry, redirect, statistics, and persistence behavior. Add link management, searching, pagination, pause/resume, and daily analytics. Use the existing Python/FastAPI/SQLite codebase. Preserve existing stored records through migration. The interface is a trusted workspace without accounts; do not imply private multi-user authorization or public hosted deployment.

## Evidence on Hand

Existing API implementation, README, and automated tests. No established product visual identity, marketing claims, customer stories, or branding assets were supplied. Display real API data and honest empty states.

## Product Principles

- Creating a valid link should be the shortest path through the interface.
- Show meaningful errors beside the action that caused them.
- Manage existing links without destroying their history.
- Keep analytics explicit about time windows and UTC day boundaries.
