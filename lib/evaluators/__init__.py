"""Multi-judge evaluator — P1-2.

Hermes plugin: dispatches a 4-judge consensus panel after every
evaluation-eligible tool call. Each judge scores against the locked
TaskSpec on its assigned axis. Majority vote → accept / reject / escalate.
"""
