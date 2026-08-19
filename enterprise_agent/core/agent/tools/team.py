"""Team collaboration tools for multi-agent coordination.

Provides:
- Async teammate spawning with independent asyncio tasks
- Message passing between agents via file-based inbox
- Broadcast communication
- Shutdown coordination with request_id handshake
- Plan approval workflow
- Work-Idle cycle with auto task claiming

Architecture:
    Lead Agent (main LangGraph)
         |
    spawn_teammate() -> TeammateRunner (asyncio task)
         |
    Work Phase: process prompt, use tools, respond to messages
         |
    idle() -> Idle Phase: poll inbox, auto-claim unclaimed tasks
         |
    Timeout or shutdown_request -> terminate
"""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from langchain_core.tools import tool

from enterprise_agent.config.settings import settings
from enterprise_agent.core.agent.tools.workspace import get_user_workspace

# Directory paths for team coordination
TEAM_DIR_NAME = ".team"
INBOX_DIR_NAME = "inbox"
CONFIG_FILE_NAME = "config.json"
VALID_MSG_TYPES = {
    "message",
    "broadcast",
    "shutdown_request",
    "shutdown_response",
    "plan_approval_request",
    "plan_approval_response",
    "auto_claimed_task"
}

# Teammate constants
IDLE_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 5
MAX_WORK_ROUNDS = 50
VALID_AGENT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
AUTONOMOUS_TEAM_TOOL_NAMES = {
    "bash",
    "read_file",
    "task_get",
    "task_list",
    "load_skill",
    "list_skills",
    "list_teammates",
    "read_tool_artifact",
    "context_status",
    "search_memory",
    # These three are intercepted by TeammateRunner and never dispatched to
    # their lead-Agent wrappers.
    "claim_task",
    "send_message",
    "idle",
}

# System prompt template for team agents (concise version)
TEAMMATE_SYSTEM_PROMPT_TEMPLATE = """You are '{name}' (role: {role}). Part of a multi-agent team.

Focus on your role. Tools are read-only/safe; report requested mutations to lead.
When done, call `idle()` to signal availability. Check inbox for messages."""


def _validate_agent_name(name: str) -> str:
    """Reject model-controlled names before they become inbox filenames."""
    value = str(name or "")
    if not VALID_AGENT_NAME.fullmatch(value):
        raise ValueError("Agent name must match [A-Za-z0-9_-]{1,64}")
    return value


class AsyncMessageBus:
    """Async file-based message passing between agents.

    Each agent has an inbox as a JSONL file that can be read and drained.
    Thread-safe for concurrent access.
    """

    def __init__(self, team_dir: Path = None):
        if team_dir is None:
            team_dir = get_user_workspace() / TEAM_DIR_NAME
        self.team_dir = team_dir
        self.inbox_dir = self.team_dir / INBOX_DIR_NAME
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, asyncio.Lock] = {}

    def _get_lock(self, name: str) -> asyncio.Lock:
        """Get or create lock for inbox access."""
        name = _validate_agent_name(name)
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    def _inbox_path(self, name: str) -> Path:
        name = _validate_agent_name(name)
        path = (self.inbox_dir / f"{name}.jsonl").resolve()
        if not path.is_relative_to(self.inbox_dir.resolve()):
            raise ValueError("Agent inbox path escapes the team directory")
        return path

    async def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict = None
    ) -> str:
        """Send a message to another agent asynchronously.

        Args:
            sender: Sender's name
            to: Recipient's name
            content: Message content
            msg_type: Message type (message, broadcast, shutdown_request, etc.)
            extra: Additional metadata

        Returns:
            Confirmation message
        """
        if msg_type not in VALID_MSG_TYPES:
            return f"Error: Invalid msg_type '{msg_type}'"
        sender = _validate_agent_name(sender)
        to = _validate_agent_name(to)

        msg = {
            "type": msg_type,
            "from": sender,
            "content": content,
            "timestamp": time.time(),
            "datetime": datetime.now(timezone.utc).isoformat()
        }
        if extra:
            # Coordination metadata may add request IDs, but cannot forge the
            # authenticated envelope chosen by this method.
            for key, value in extra.items():
                if key not in msg:
                    msg[key] = value

        inbox_path = self._inbox_path(to)
        lock = self._get_lock(to)

        async with lock:
            with open(inbox_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg) + "\n")

        return f"Sent {msg_type} to {to}"

    async def read_inbox(self, name: str) -> List[dict]:
        """Read and drain inbox for an agent asynchronously.

        Args:
            name: Agent name

        Returns:
            List of messages (inbox is cleared after reading)
        """
        name = _validate_agent_name(name)
        inbox_path = self._inbox_path(name)
        lock = self._get_lock(name)

        async with lock:
            if not inbox_path.exists():
                return []

            messages = []
            content = inbox_path.read_text(encoding="utf-8")
            for line in content.strip().splitlines():
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

            # Clear inbox after reading
            inbox_path.write_text("", encoding="utf-8")

        return messages

    async def broadcast(self, sender: str, content: str, names: List[str]) -> str:
        """Broadcast message to multiple recipients asynchronously.

        Args:
            sender: Sender's name
            content: Message content
            names: List of recipient names

        Returns:
            Count of recipients
        """
        count = 0
        for name in names:
            if name != sender:
                await self.send(sender, name, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"


class TeammateConfig:
    """Manages team configuration persistence."""

    def __init__(self, team_dir: Path = None):
        if team_dir is None:
            team_dir = get_user_workspace() / TEAM_DIR_NAME
        self.team_dir = team_dir
        self.team_dir.mkdir(exist_ok=True)
        self.config_path = self.team_dir / CONFIG_FILE_NAME
        self._lock = asyncio.Lock()

    async def load(self) -> dict:
        """Load team configuration."""
        async with self._lock:
            if self.config_path.exists():
                return json.loads(self.config_path.read_text(encoding="utf-8"))
            return {"team_name": "default", "members": []}

    async def save(self, config: dict) -> None:
        """Save team configuration."""
        async with self._lock:
            self.config_path.write_text(
                json.dumps(config, indent=2),
                encoding="utf-8"
            )

    async def find_member(self, name: str) -> Optional[dict]:
        """Find member by name."""
        name = _validate_agent_name(name)
        config = await self.load()
        for member in config.get("members", []):
            if member.get("name") == name:
                return member
        return None

    async def update_member_status(self, name: str, status: str) -> None:
        """Update member status."""
        name = _validate_agent_name(name)
        config = await self.load()
        for member in config.get("members", []):
            if member.get("name") == name:
                member["status"] = status
                break
        await self.save(config)

    async def add_member(self, name: str, role: str, status: str = "working") -> None:
        """Add new member."""
        name = _validate_agent_name(name)
        config = await self.load()
        member = {"name": name, "role": role, "status": status}
        config["members"].append(member)
        await self.save(config)

    async def remove_member(self, name: str) -> None:
        """Remove member."""
        name = _validate_agent_name(name)
        config = await self.load()
        config["members"] = [
            m for m in config.get("members", [])
            if m.get("name") != name
        ]
        await self.save(config)

    async def get_member_names(self) -> List[str]:
        """Get list of member names."""
        config = await self.load()
        return [m.get("name") for m in config.get("members", [])]


class TeammateRunner:
    """Runs an autonomous teammate agent in an asyncio task.

    Implements work-idle cycle:
    1. Work Phase: Process initial prompt, respond to messages, use tools
    2. Call idle() to enter Idle Phase
    3. Idle Phase: Poll inbox, auto-claim unclaimed tasks
    4. Resume Work Phase if new work arrives
    5. Shutdown after timeout or shutdown_request
    """

    def __init__(
        self,
        name: str,
        role: str,
        bus: AsyncMessageBus,
        config: TeammateConfig
    ):
        self.name = _validate_agent_name(name)
        self.role = role
        self.bus = bus
        self.config = config
        self.task: Optional[asyncio.Task] = None
        self.messages: List[Dict] = []
        self.shutdown_requested = False
        self.request_id: Optional[str] = None

    async def start(self, prompt: str) -> str:
        """Start teammate with initial prompt.

        Args:
            prompt: Initial work prompt

        Returns:
            Start confirmation
        """
        # Check if already running
        member = await self.config.find_member(self.name)
        if member and member.get("status") in ("working", "idle"):
            return f"Error: '{self.name}' is already running (status: {member['status']})"

        # Add or update member
        if member:
            await self.config.update_member_status(self.name, "working")
        else:
            await self.config.add_member(self.name, self.role, "working")

        # Initialize messages with system prompt
        system_prompt = TEAMMATE_SYSTEM_PROMPT_TEMPLATE.format(
            name=self.name, role=self.role
        )
        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        # Start asyncio task
        self.task = asyncio.create_task(self._run_loop())

        return f"Spawned '{self.name}' (role: {self.role}) as async task"

    async def stop(self) -> str:
        """Stop teammate gracefully."""
        if self.task and not self.task.done():
            self.shutdown_requested = True
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        await self.config.update_member_status(self.name, "shutdown")
        return f"Teammate '{self.name}' stopped"

    async def _run_loop(self) -> None:
        """Main teammate loop: Work Phase -> Idle Phase -> repeat."""

        try:
            while not self.shutdown_requested:
                # === WORK PHASE ===
                await self._work_phase()

                if self.shutdown_requested:
                    break

                # === IDLE PHASE ===
                await self.config.update_member_status(self.name, "idle")
                resume = await self._idle_phase()

                if not resume:
                    # Timeout - shutdown
                    break

                # Resume work phase
                await self.config.update_member_status(self.name, "working")

        except asyncio.CancelledError:
            # Graceful shutdown
            pass

        finally:
            await self.config.update_member_status(self.name, "shutdown")

    async def _work_phase(self) -> None:
        """Work phase: process messages and use tools."""
        from enterprise_agent.core.agent.context import get_context_manager
        from enterprise_agent.core.agent.llm_factory import get_llm
        from enterprise_agent.core.agent.tools import ALL_TOOLS

        llm = get_llm()
        autonomous_tools = [
            tool for tool in ALL_TOOLS if tool.name in AUTONOMOUS_TEAM_TOOL_NAMES
        ]
        llm_with_tools = llm.bind_tools(autonomous_tools)

        ctx_mgr = get_context_manager()

        for round_num in range(MAX_WORK_ROUNDS):
            if self.shutdown_requested:
                return

            # Check inbox for new messages
            inbox_messages = await self.bus.read_inbox(self.name)
            for msg in inbox_messages:
                if msg.get("type") == "shutdown_request":
                    self.shutdown_requested = True
                    self.request_id = msg.get("request_id")
                    # Send shutdown response
                    await self.bus.send(
                        self.name, "lead",
                        f"Shutdown acknowledged. Request ID: {self.request_id}",
                        "shutdown_response",
                        {"request_id": self.request_id}
                    )
                    return

                # Add message to conversation
                self.messages.append({
                    "role": "user",
                    "content": json.dumps(msg)
                })

            # Apply microcompact
            self.messages = ctx_mgr.microcompact(self.messages, keep_last=settings.MICROCOMPACT_KEEP_LAST)

            # Call LLM
            try:
                from enterprise_agent.core.execution.interrupt_control import (
                    is_current_task_cancel_requested,
                )

                if await is_current_task_cancel_requested():
                    return
                response = await llm_with_tools.ainvoke(self.messages)
            except Exception as e:
                # Error - may need to shutdown
                print(f"[{self.name}] LLM error: {e}")
                return

            self.messages.append({"role": "assistant", "content": response.content})

            # Check for idle request or tool calls
            idle_requested = False
            tool_results = []

            if hasattr(response, "tool_calls") and response.tool_calls:
                for tool_call in response.tool_calls:
                    if await is_current_task_cancel_requested():
                        return
                    tool_name = tool_call.get("name")
                    tool_input = tool_call.get("args", {})
                    tool_id = tool_call.get("id", "")

                    if tool_name == "idle":
                        idle_requested = True
                        tool_results.append({
                            "role": "tool",
                            "content": "Entering idle phase.",
                            "tool_call_id": tool_id
                        })
                    elif tool_name == "claim_task":
                        # Claim task
                        result = await self._claim_task(tool_input.get("task_id"))
                        tool_results.append({
                            "role": "tool",
                            "content": result,
                            "tool_call_id": tool_id
                        })
                    elif tool_name == "send_message":
                        # Send message via bus
                        to = tool_input.get("to")
                        content = tool_input.get("content")
                        result = await self.bus.send(self.name, to, content)
                        tool_results.append({
                            "role": "tool",
                            "content": result,
                            "tool_call_id": tool_id
                        })
                    else:
                        # Execute regular tool
                        result = await self._execute_tool(tool_name, tool_input)
                        raw_output = (
                            json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
                            if isinstance(result, (dict, list))
                            else str(result)
                        )
                        from enterprise_agent.core.agent.tool_artifacts import (
                            ToolArtifactStore,
                            format_tool_output,
                        )
                        from enterprise_agent.core.agent.tools.contracts import (
                            normalize_tool_result,
                        )
                        from enterprise_agent.core.agent.tools.workspace import (
                            get_current_session_id,
                            get_current_user_id,
                        )

                        normalized = normalize_tool_result(
                            tool_name=tool_name,
                            tool_call_id=tool_id or tool_name,
                            raw_result=result,
                            duration_ms=0,
                            attempt_count=1,
                        )
                        receipt = None
                        artifact_error = None
                        if len(raw_output) > 100:
                            try:
                                receipt = ToolArtifactStore(
                                    user_id=get_current_user_id(),
                                ).save(
                                    raw_output,
                                    trace_id=(
                                        f"team-{get_current_session_id() or self.name}"
                                    ),
                                    tool_call_id=tool_id or tool_name,
                                )
                            except Exception:
                                logging.exception("Teammate tool artifact persistence failed")
                                artifact_error = "artifact_write_failed"
                        if (
                            artifact_error
                            and len(raw_output) > settings.TOOL_OUTPUT_MAX_CHARS
                        ):
                            model_output = (
                                "Error: artifact_write_failed; large teammate output "
                                "was not continued."
                            )
                            tool_results.append({
                                "role": "tool",
                                "content": model_output,
                                "tool_call_id": tool_id,
                                "artifact": {"storage_status": "failed"},
                            })
                            continue
                        if (
                            receipt is not None
                            or len(raw_output) > settings.TOOL_OUTPUT_MAX_CHARS
                            or artifact_error
                        ):
                            model_output = format_tool_output(
                                raw_output,
                                receipt=receipt,
                                status=normalized.status.value,
                                error_code=normalized.error_code,
                                exit_code=normalized.exit_code,
                                artifact_error=artifact_error,
                            )[0]
                        else:
                            model_output = raw_output
                        tool_message = {
                            "role": "tool",
                            "content": model_output,
                            "tool_call_id": tool_id
                        }
                        if receipt is not None:
                            tool_message["artifact"] = receipt.to_dict()
                        tool_results.append(tool_message)

            if tool_results:
                self.messages.append({"role": "user", "content": tool_results})

            # Check stop reason
            if response.stop_reason != "tool_use" or idle_requested:
                # End work phase
                return

    async def _idle_phase(self) -> bool:
        """Idle phase: poll for messages and auto-claim tasks.

        Returns:
            True if should resume work, False if timeout/shutdown
        """
        timeout = IDLE_TIMEOUT_SECONDS
        poll_interval = POLL_INTERVAL_SECONDS
        polls = timeout // poll_interval

        for _ in range(polls):
            if self.shutdown_requested:
                return False

            await asyncio.sleep(poll_interval)

            # Check inbox
            inbox_messages = await self.bus.read_inbox(self.name)
            for msg in inbox_messages:
                if msg.get("type") == "shutdown_request":
                    self.shutdown_requested = True
                    self.request_id = msg.get("request_id")
                    return False

                # Add message to conversation
                self.messages.append({
                    "role": "user",
                    "content": json.dumps(msg)
                })

            if inbox_messages:
                return True  # Resume work

            # Check for unclaimed tasks
            unclaimed = await self._find_unclaimed_tasks()
            if unclaimed:
                task = unclaimed[0]
                await self._claim_task(task["id"])

                # Inject identity and auto-claimed message
                if len(self.messages) <= settings.MICROCOMPACT_KEEP_LAST:
                    identity_msg = f"<identity>You are '{self.name}', role: {self.role}.</identity>"
                    self.messages.insert(0, {"role": "user", "content": identity_msg})
                    self.messages.insert(1, {"role": "assistant", "content": f"I am {self.name}. Continuing."})

                desc = task.get('description', '')
                claimed_msg = (
                    f"<auto-claimed>Task #{task['id']}: {task['subject']}\n{desc}</auto-claimed>"
                )
                self.messages.append({"role": "user", "content": claimed_msg})
                self.messages.append({"role": "assistant", "content": f"Claimed task #{task['id']}. Working on it."})

                return True  # Resume work

        return False  # Timeout

    async def _claim_task(self, task_id: int) -> str:
        """Claim a task by ID."""
        from enterprise_agent.core.agent.tools.task import get_task_manager

        tm = get_task_manager()
        return tm.claim(task_id, self.name)

    async def _find_unclaimed_tasks(self) -> List[dict]:
        """Find unclaimed tasks that are not blocked."""
        from enterprise_agent.core.agent.tools.task import get_task_manager

        tm = get_task_manager()
        tasks_dir = tm.tasks_dir

        unclaimed = []
        for f in sorted(tasks_dir.glob("task_*.json")):
            try:
                task = json.loads(f.read_text(encoding="utf-8"))
                if task.get("status") == "pending":
                    if not task.get("owner"):
                        if not task.get("blockedBy"):
                            unclaimed.append(task)
            except (json.JSONDecodeError, Exception):
                pass

        return unclaimed

    async def _execute_tool(self, tool_name: str, tool_input: Dict) -> str:
        """Execute only tools that cannot bypass lead-Agent governance."""
        from enterprise_agent.core.agent.tools import get_tool_by_name
        from enterprise_agent.core.agent.tools.contracts import RiskLevel, resolve_tool_risk

        if tool_name not in AUTONOMOUS_TEAM_TOOL_NAMES:
            return f"Blocked: autonomous teammate tool is not allowed: {tool_name}"
        if tool_name in {"claim_task", "send_message", "idle"}:
            return f"Blocked: {tool_name} must use the teammate coordination handler"
        try:
            risk = resolve_tool_risk(tool_name, tool_input)
        except (KeyError, ValueError):
            return f"Blocked: autonomous teammate tool has no safe contract: {tool_name}"
        if risk is not RiskLevel.SAFE:
            return (
                "Blocked: autonomous teammates may execute only policy-classified "
                f"safe tools; return '{tool_name}' to the lead Agent."
            )

        tool = get_tool_by_name(tool_name)
        if not tool:
            return f"Unknown tool: {tool_name}"

        try:
            if hasattr(tool, "ainvoke"):
                return await tool.ainvoke(tool_input)
            else:
                return tool.invoke(tool_input)
        except Exception:
            logging.exception("Autonomous teammate tool execution failed")
            return "Error: teammate_tool_execution_failed"


class TeammateManager:
    """Manages multiple autonomous teammate agents."""

    def __init__(self, workdir: Path = None):
        self.workdir = workdir or get_user_workspace()
        self.team_dir = self.workdir / TEAM_DIR_NAME
        self.bus = AsyncMessageBus(self.team_dir)
        self.config = TeammateConfig(self.team_dir)
        self.runners: Dict[str, TeammateRunner] = {}

    async def spawn(self, name: str, role: str, prompt: str) -> str:
        """Spawn a teammate agent.

        Args:
            name: Unique name for teammate
            role: Role description
            prompt: Initial work prompt

        Returns:
            Spawn confirmation
        """
        name = _validate_agent_name(name)
        # Create runner
        runner = TeammateRunner(name, role, self.bus, self.config)
        self.runners[name] = runner

        # Start
        return await runner.start(prompt)

    async def shutdown(self, name: str) -> str:
        """Shutdown a teammate.

        Args:
            name: Teammate name

        Returns:
            Shutdown confirmation
        """
        runner = self.runners.get(name)
        if not runner:
            return f"Unknown teammate: {name}"

        # Send shutdown request
        request_id = str(uuid.uuid4())[:8]
        await self.bus.send(
            "lead", name,
            "Please shut down.",
            "shutdown_request",
            {"request_id": request_id}
        )

        # Wait for response (with timeout)
        await asyncio.sleep(2)

        # Force stop if still running
        await runner.stop()

        return f"Shutdown request {request_id} sent to '{name}'"

    async def list_all(self) -> str:
        """List all team members and status."""
        config = await self.config.load()
        members = config.get("members", [])

        if not members:
            return "No teammates."

        lines = [f"Team: {config.get('team_name', 'default')}"]
        for m in members:
            lines.append(f"  {m.get('name')} ({m.get('role')}): {m.get('status')}")

        return "\n".join(lines)

    async def get_member_names(self) -> List[str]:
        """Get list of member names."""
        return await self.config.get_member_names()


class PlanApprovalManager:
    """Manages plan approval workflow."""

    def __init__(self, bus: AsyncMessageBus = None, team_dir: Path = None):
        self.bus = bus or AsyncMessageBus(team_dir)
        self.plan_requests: Dict[str, dict] = {}

    async def submit_plan(
        self,
        from_agent: str,
        plan_content: str,
        request_id: str = None
    ) -> str:
        """Submit a plan for approval."""
        if not request_id:
            request_id = str(uuid.uuid4())[:8]

        self.plan_requests[request_id] = {
            "from": from_agent,
            "plan": plan_content,
            "status": "pending",
            "submitted_at": time.time()
        }

        # Send to lead for approval
        await self.bus.send(
            from_agent, "lead",
            plan_content,
            "plan_approval_request",
            {"request_id": request_id}
        )

        return f"Plan submitted with request_id: {request_id}"

    async def review(
        self,
        request_id: str,
        approve: bool,
        feedback: str = ""
    ) -> str:
        """Review a plan request."""
        request = self.plan_requests.get(request_id)
        if not request:
            return f"Error: Unknown plan request_id '{request_id}'"

        status = "approved" if approve else "rejected"
        request["status"] = status

        await self.bus.send(
            "lead", request["from"],
            feedback,
            "plan_approval_response",
            {"request_id": request_id, "approve": approve, "feedback": feedback}
        )

        return f"Plan {status} for '{request['from']}'"


# Per-user instances cache
_message_buses: Dict[int, AsyncMessageBus] = {}
_teammate_managers: Dict[int, TeammateManager] = {}
_plan_managers: Dict[int, PlanApprovalManager] = {}


def get_message_bus() -> AsyncMessageBus:
    """Get or create AsyncMessageBus instance for current user."""
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id
    user_id = get_current_user_id()

    if user_id not in _message_buses:
        _message_buses[user_id] = AsyncMessageBus(get_user_workspace() / TEAM_DIR_NAME)
    return _message_buses[user_id]


def get_teammate_manager() -> TeammateManager:
    """Get or create TeammateManager instance for current user."""
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id
    user_id = get_current_user_id()

    if user_id not in _teammate_managers:
        _teammate_managers[user_id] = TeammateManager()
    return _teammate_managers[user_id]


def get_plan_manager() -> PlanApprovalManager:
    """Get or create PlanApprovalManager instance for current user."""
    from enterprise_agent.core.agent.tools.workspace import get_current_user_id
    user_id = get_current_user_id()

    if user_id not in _plan_managers:
        _plan_managers[user_id] = PlanApprovalManager(get_message_bus())
    return _plan_managers[user_id]


# === Tool Definitions ===
# All tools are async to avoid event loop conflicts in LangGraph


@tool
async def spawn_teammate(name: str, role: str, prompt: str) -> str:
    """Spawn an autonomous teammate with its own asyncio task, tools, and inbox.

    Use for TRUE multi-agent collaboration (not simulation). Each teammate
    runs independently and communicates via message passing.

    Use when: (1) Multiple agents working concurrently on different sub-tasks
              (2) Specialized roles: coder, reviewer, tester working in parallel
              (3) Task too large for one agent — split across teammates

    Example:
        spawn_teammate("coder", "Code Generator", "Build Flask REST API")
        spawn_teammate("reviewer", "Code Reviewer", "Review coder's output")
        # Then: list_teammates() for status, read_inbox() for messages

    Args:
        name: Unique name (e.g., "coder", "reviewer")
        role: Role description
        prompt: Work prompt for the teammate

    Returns:
        Confirmation with teammate's name and status
    """
    tm = get_teammate_manager()
    return await tm.spawn(name, role, prompt)


@tool
async def list_teammates() -> str:
    """List all teammates and their status.

    Returns:
        Formatted list of teammates
    """
    tm = get_teammate_manager()
    return await tm.list_all()


@tool
async def send_message(to: str, content: str, msg_type: str = "message") -> str:
    """Send a message to a teammate.

    Args:
        to: Recipient name
        content: Message content
        msg_type: Message type (message, broadcast, shutdown_request, etc.)

    Returns:
        Send confirmation
    """
    bus = get_message_bus()
    return await bus.send("lead", to, content, msg_type)


@tool
async def read_inbox() -> str:
    """Read and clear the lead's inbox.

    Returns:
        JSON string of messages
    """
    bus = get_message_bus()
    messages = await bus.read_inbox("lead")
    return json.dumps(messages, indent=2)


@tool
async def broadcast(content: str) -> str:
    """Broadcast message to all teammates.

    Args:
        content: Message content to broadcast

    Returns:
        Broadcast confirmation
    """
    bus = get_message_bus()
    tm = get_teammate_manager()
    names = await tm.get_member_names()
    return await bus.broadcast("lead", content, names)


@tool
async def shutdown_request(teammate: str) -> str:
    """Request a teammate to shut down.

    Args:
        teammate: Name of the teammate to shut down

    Returns:
        Shutdown confirmation
    """
    tm = get_teammate_manager()
    return await tm.shutdown(teammate)


@tool
async def plan_approval(request_id: str, approve: bool, feedback: str = "") -> str:
    """Approve or reject a teammate's plan.

    Args:
        request_id: Plan request ID
        approve: Whether to approve
        feedback: Optional feedback

    Returns:
        Approval result
    """
    pm = get_plan_manager()
    return await pm.review(request_id, approve, feedback)


@tool
def idle() -> str:
    """Signal that agent is entering idle state.

    Used by teammates when done with current work.
    Triggers idle phase: poll for messages and auto-claim tasks.

    Returns:
        Idle confirmation
    """
    return "Entering idle state. Will poll for messages and auto-claim tasks."
