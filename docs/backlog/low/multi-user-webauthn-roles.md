# Multi-User WebAuthn with Roles

## Summary
Extend WebAuthn from single-owner to support multiple registered users with different permission levels (admin, family member, guest).

## Context / Motivation
D1 implements single-user WebAuthn (owner-only). As Alfred expands to household use, family members may want their own identities with tailored preferences and permission boundaries (e.g., family can control lights but not access financial integrations).

## Acceptance Criteria
- Multiple WebAuthn credentials can be registered, each mapped to a distinct identity
- Role-based permission model (admin, family, guest) with configurable clearance levels
- IdentityGate resolves to the correct identity per credential
- Each identity has its own preference profile in semantic memory
- Admin can manage (invite/revoke) other users
