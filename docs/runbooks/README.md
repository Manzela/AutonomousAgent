# Runbooks

Operational procedures for running and recovering AutonomousAgent.

## Index

| Runbook | When to use |
|---|---|
| [telegram-bot-setup.md](telegram-bot-setup.md) | One-time: create the Telegram bot for the messaging gateway |
| [phase1-acceptance.md](phase1-acceptance.md) | End of Phase 1: validate the local deployment works end-to-end |
| [recovery.md](recovery.md) | Stack is broken, panic was invoked, or you need to restore from a snapshot |

## Conventions

Every runbook:
- States its prerequisites at the top
- Lists steps in order with expected output for each
- Has a clear "pass criteria" or "expected end state"
- Says what to do if a step fails
