"""Misfire — know why the build misfired.

An agentic CI failure explainer. On a red build it investigates the failure
(logs + PR diff + test history), classifies it real vs. likely flaky, and posts a
single advisory PR comment. Advisory only — it never blocks, merges, or changes
code. Runs on Claude or OpenAI (bring your own key).
"""

__version__ = "0.1.5"
