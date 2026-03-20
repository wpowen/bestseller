# Self Memory

Mistake: Missing mandatory memory files before task execution.
Wrong: Assume `.remember/memory/self.md` and `.remember/memory/project.md` always exist and proceed without fallback initialization.
Correct:
- Attempt to read both files first.
- If files do not exist, create baseline files immediately.
- Continue task execution while following user-provided rules in current conversation.
