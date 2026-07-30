"""Bridge to live Claude Code CLI sessions on this machine.

Domain package: discovery (which sessions are alive), transcript (what they said),
terminal (how to type into them). No dependency on coworker.tools or the engine —
the tool adapter in coworker/tools/claude_sessions.py wires these together.
"""
