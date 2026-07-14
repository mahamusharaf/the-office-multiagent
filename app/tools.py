"""
tools.py

Expanded tool set for the redesigned startup simulation.
Agents now have real actions with real consequences:
- Communication (talk, post)
- Work actions (update tasks, fix bugs, report issues)
- Escalation (raise concerns, argue)
- Nuclear option (quit — Riko only, but technically anyone)
"""
from app.personalities import AGENTS

AGENT_NAMES = list(AGENTS.keys())

OFFICE_TOOLS = [
    # ── Communication ──────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "post_public_message",
            "description": (
                "Post a message to the shared office channel, visible to everyone. "
                "Use for announcements, venting, celebrations, or anything the whole team should hear."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Your message. Be yourself — don't sanitize your personality."
                    }
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "talk_to_agent",
            "description": (
                "Send a direct message to one specific coworker. "
                "Use for private conversations, asking for help, venting, or saying something "
                "you wouldn't want the whole office to hear."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": AGENT_NAMES,
                        "description": "Who you're messaging. Cannot be yourself."
                    },
                    "message": {
                        "type": "string",
                        "description": "Your message. Private — only they see it."
                    }
                },
                "required": ["target", "message"]
            }
        }
    },

    # ── Work actions ───────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": (
                "Update the status of a task assigned to you. "
                "Use this to mark tasks done, flag them as blocked, or move them to in_progress. "
                "Only update tasks that are actually assigned to you."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID (e.g. T001, T003)"
                    },
                    "new_status": {
                        "type": "string",
                        "enum": ["in_progress", "done", "blocked"],
                        "description": "The new status for this task."
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional note about what happened or why it's blocked."
                    }
                },
                "required": ["task_id", "new_status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "report_bug",
            "description": (
                "Report a bug you've found. This adds it to the shared bug tracker. "
                "Be specific — vague bug reports are useless. "
                "If you know who caused it, say so."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What the bug is. Be specific."
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "description": "How bad is this? Critical = prod is broken or data loss."
                    },
                    "caused_by": {
                        "type": "string",
                        "enum": AGENT_NAMES + ["unknown"],
                        "description": "Who you think introduced this bug. Use 'unknown' if unsure."
                    }
                },
                "required": ["description", "severity", "caused_by"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fix_bug",
            "description": (
                "Attempt to fix a known bug. "
                "Success is not guaranteed — complex bugs take multiple attempts. "
                "High severity bugs are harder to fix."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "bug_id": {
                        "type": "string",
                        "description": "The bug ID to fix (e.g. BUG001)"
                    },
                    "approach": {
                        "type": "string",
                        "description": "How you're fixing it. Brief technical description."
                    }
                },
                "required": ["bug_id", "approach"]
            }
        }
    },

    # ── Escalation ─────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "raise_concern",
            "description": (
                "Formally raise a concern about the sprint, a teammate, or the project. "
                "This is more serious than a public message — it signals something is actually wrong. "
                "Use when you're genuinely worried, not just venting."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "concern": {
                        "type": "string",
                        "description": "What you're concerned about. Be direct."
                    },
                    "about": {
                        "type": "string",
                        "enum": AGENT_NAMES + ["sprint", "codebase", "deadline", "process"],
                        "description": "What or who this concern is about."
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["minor", "serious", "critical"],
                        "description": "How serious is this concern?"
                    }
                },
                "required": ["concern", "about", "severity"]
            }
        }
    },

    # ── Idle ───────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "idle",
            "description": (
                "Do nothing this tick. Only valid if you genuinely have nothing to act on. "
                "If there are open bugs, blocked tasks, or a deadline approaching — "
                "idling is the wrong choice."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why you're doing nothing. Be honest."
                    }
                },
                "required": ["reason"]
            }
        }
    },

    # ── Nuclear option ─────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "quit",
            "description": (
                "Quit the company. This is the nuclear option — only do this if your morale "
                "is critical and you genuinely cannot take it anymore. "
                "This triggers a company crisis and cannot be undone this sprint."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why you're quitting. Make it count."
                    }
                },
                "required": ["reason"]
            }
        }
    },
]