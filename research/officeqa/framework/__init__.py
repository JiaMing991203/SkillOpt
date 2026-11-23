"""The learned action-and-stage contract framework, ported from ALFWorld into OfficeQA (rule OQA-FW-1).

`upstream/` holds the one-time byte copy of the ALFWorld sources with its MANIFEST.json (sha256, source
commit, per-file git status). Nothing here imports the sibling worktree at run time. The `fw_*` modules
are the SAME mechanisms with the ALFWorld vocabulary lifted out into a `Domain` object, so one core
serves both benchmarks and each supplies its own entities and observations.
"""
