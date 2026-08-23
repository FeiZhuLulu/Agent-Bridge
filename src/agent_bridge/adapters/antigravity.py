from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_bridge.adapters.base import STDIO_LIMIT, Adapter
from agent_bridge.config import AgentConfig
from agent_bridge.models import Session, Task, TurnResult, agy_effort
from agent_bridge.processes import (
    drop_pid,
    kill_tree,
    process_create_time,
    process_image_name,
    record_pid,
    resolve_command,
)
from agent_bridge.transcript import append_event
from agent_bridge.worker_env import build_worker_env

log = logging.getLogger(__name__)

_RESULT_STATUSES = {"SUCCESS", "ERROR", "CANCELED", "INTERRUPTED", "INVALID", "WAITING"}
_TOOL_SCHEMA_MARKERS = (
    "invalid tool call error",
    "invalid_signature",
    "codecontent is a required parameter",
    "convert tool call for permissions",
)


def conversation_id_of(obj: dict[str, Any]) -> str | None:
    for key in ("conversation_id", "conversationId"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value
    for nested_key in ("init", "step_update", "result"):
        nested = obj.get(nested_key)
        if isinstance(nested, dict):
            found = conversation_id_of(nested)
            if found:
                return found
    return None


def is_result_event(obj: dict[str, Any]) -> bool:
    if obj.get("event") == "result":
        return True
    if obj.get("type") in {"result", "final", "turn_complete", "completed"}:
        return True
    if obj.get("status") in _RESULT_STATUSES and ("response" in obj or "error" in obj):
        return True
    return False


def unwrap_result(obj: dict[str, Any]) -> dict[str, Any]:
    nested = obj.get("result")
    if isinstance(nested, dict) and (nested.get("status") or nested.get("response") or nested.get("error")):
        return nested
    return obj


def text_delta_of(obj: dict[str, Any]) -> str:
    step = obj.get("step_update")
    if isinstance(step, dict):
        delta = step.get("text_delta")
        if isinstance(delta, str) and delta:
            return delta
    for key in ("text_delta", "delta"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def result_text_of(obj: dict[str, Any]) -> str:
    payload = unwrap_result(obj)
    for key in ("response", "result", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = result_text_of(value)
            if nested:
                return nested
    content = payload.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def result_error_of(obj: dict[str, Any]) -> str | None:
    payload = unwrap_result(obj)
    status = str(payload.get("status") or "")
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error
    if status and status != "SUCCESS":
        return status
    return None


def is_agy_tool_schema_error(error: str | None) -> bool:
    """agy/cortex rejected a model tool call while converting it for permissions.

    This is not a Bridge write adapter. Headless agy executes tools itself; the
    model sometimes omits ``CodeContent`` on ``write_to_file``. The run can still
    recover, write files, and exit 0 with a full response.
    """
    if not error:
        return False
    text = error.lower()
    return any(marker in text for marker in _TOOL_SCHEMA_MARKERS)


def recovered_agy_tool_error(
    result: dict[str, Any],
    response: str,
    exit_code: int | None,
) -> bool:
    error = result_error_of(result)
    if not is_agy_tool_schema_error(error):
        return False
    if not (response or "").strip():
        return False
    return exit_code in (0, None)


# Tool names that can write to the workspace. Read-only tools (view_file,
# list_dir, grep_search, ...) also carry path params; counting those would
# report files the agent merely looked at. Under-matching is safe because the
# disk snapshot diff in merge_files_changed still catches every real write.
_MUTATING_TOOL_MARKERS = (
    "write",
    "edit",
    "replace",
    "delete",
    "remove",
    "move",
    "rename",
    "patch",
    "create",
)


def collect_tool_paths(obj: dict[str, Any], into: set[str]) -> None:
    step = obj.get("step_update") if isinstance(obj.get("step_update"), dict) else obj
    if not isinstance(step, dict):
        return
    info = step.get("tool_info")
    if not isinstance(info, dict):
        return
    name = str(info.get("name") or step.get("tool_name") or "").lower()
    if not any(marker in name for marker in _MUTATING_TOOL_MARKERS):
        return
    params = info.get("parameters")
    if not isinstance(params, dict):
        return
    for key in ("Path", "path", "file", "filePath", "TargetFile", "AbsolutePath"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            into.add(value)


class AgyAdapter(Adapter):
    def __init__(self, agent: AgentConfig, home: Path, env_config=None) -> None:
        super().__init__(agent, home, env_config)
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        # Session ids whose current turn was cancelled via cancel()/shutdown().
        # kill_tree makes agy stdout hit EOF, which is otherwise
        # indistinguishable from a normal end of stream.
        self._cancelled: set[str] = set()

    async def ensure_session(self, session: Session) -> None:
        return None

    def _build_cmd(self, session: Session, task: Task) -> list[str]:
        cmd = resolve_command(self.agent.command, self.agent.fallback_commands)
        model = task.model or session.model
        effort = agy_effort(task.effort or session.effort)
        # --model/--effort before -p: print mode can swallow later flags.
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        # Process cwd is the workspace, but default-cli-project can keep a
        # stale scratch root. Pin this turn to the requested folder before -p.
        if session.cwd:
            cmd += ["--add-dir", session.cwd]
        if session.native_session_id:
            cmd += ["--conversation", session.native_session_id]
        else:
            cmd += ["--new-project"]
        cmd += [
            "-p",
            task.message,
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
            "--print-timeout",
            self.agent.print_timeout or "120m",
        ]
        return cmd

    async def _drain_stderr(self, proc: asyncio.subprocess.Process, session_id: str) -> None:
        if proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log.info("[antigravity %s] %s", session_id, text)
        except (ValueError, OSError):
            log.warning("stderr drain aborted for %s", session_id, exc_info=True)

    async def run_turn(self, session: Session, task: Task) -> TurnResult:
        cmd = self._build_cmd(session, task)
        env = build_worker_env(self.agent.env, config=self.env_config, worker_context=True)
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        append_event(session.session_id, "prompt_sent", {"text": task.message, "cmd": cmd[:6]}, self.home)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=task.cwd or session.cwd,
            limit=STDIO_LIMIT,
            **kwargs,
        )
        self._procs[session.session_id] = proc
        self._cancelled.discard(session.session_id)
        if proc.pid:
            record_pid(
                self.home,
                session.session_id,
                proc.pid,
                process_create_time(proc.pid),
                process_image_name(proc.pid),
            )
        stderr_task = asyncio.create_task(self._drain_stderr(proc, session.session_id))
        text_parts: list[str] = []
        conversation_id = session.native_session_id
        usage: dict[str, Any] = {}
        files: set[str] = set()
        last_result: dict[str, Any] | None = None
        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                raw = line.decode("utf-8", errors="replace").rstrip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    append_event(session.session_id, "raw", {"text": raw[:2000]}, self.home)
                    text_parts.append(raw)
                    continue
                if not isinstance(obj, dict):
                    append_event(session.session_id, "raw", {"value": obj}, self.home)
                    continue
                cid = conversation_id_of(obj)
                if cid:
                    conversation_id = cid
                event = str(obj.get("event") or obj.get("type") or "")
                step = obj.get("step_update") if isinstance(obj.get("step_update"), dict) else {}
                step_type = str(step.get("step_type") or "")
                chunk = text_delta_of(obj)
                event_type = "raw"
                if event == "init":
                    event_type = "raw"
                elif step_type == "agent_response" or chunk:
                    event_type = "message_chunk"
                    if chunk:
                        text_parts.append(chunk)
                elif step_type in {"tool"} or event in {"tool", "tool_call"}:
                    event_type = "tool_call"
                    collect_tool_paths(obj, files)
                elif step_type == "checkpoint" or event in {"thought", "reasoning"}:
                    event_type = "thought_chunk"
                append_event(
                    session.session_id,
                    event_type,
                    {"payload": obj} if len(raw) < 4000 else {"truncated": True},
                    self.home,
                )
                payload = unwrap_result(obj) if is_result_event(obj) else obj
                if isinstance(payload.get("usage"), dict):
                    usage = payload["usage"]
                elif isinstance(step.get("usage"), dict):
                    usage = step["usage"]
                if is_result_event(obj):
                    last_result = unwrap_result(obj)
            try:
                await asyncio.wait_for(proc.wait(), timeout=60)
            except TimeoutError:
                log.warning("agy stdout closed but process lingered; killing %s", proc.pid)
                if proc.pid:
                    kill_tree(proc.pid)
                await proc.wait()
            if session.session_id in self._cancelled:
                append_event(session.session_id, "turn_end", {"stop_reason": "cancelled"}, self.home)
                return TurnResult(
                    text="".join(text_parts),
                    files_changed=sorted(files),
                    stop_reason="cancelled",
                    native_session_id=conversation_id,
                )
            exit_warnings: list[str] = []
            if proc.returncode not in (0, None):
                exit_warnings.append(f"agy exited with code {proc.returncode}")
            if last_result is not None:
                err = result_error_of(last_result)
                result_text = result_text_of(last_result) or "".join(text_parts)
                cid = conversation_id_of(last_result) or conversation_id
                if isinstance(last_result.get("usage"), dict):
                    usage = last_result["usage"]
                session.native_session_id = cid
                if err and last_result.get("status") != "SUCCESS":
                    if recovered_agy_tool_error(last_result, result_text, proc.returncode):
                        append_event(
                            session.session_id,
                            "warning",
                            {"error": err, "code": proc.returncode, "treated_as": "recovered_tool_schema"},
                            self.home,
                        )
                        append_event(session.session_id, "turn_end", {"stop_reason": "end_turn"}, self.home)
                        return TurnResult(
                            text=result_text,
                            files_changed=sorted(files),
                            stop_reason="end_turn",
                            usage=usage,
                            native_session_id=cid,
                            warnings=[err, *exit_warnings],
                        )
                    append_event(session.session_id, "error", {"error": err, "code": proc.returncode}, self.home)
                    return TurnResult(
                        text=result_text,
                        files_changed=sorted(files),
                        stop_reason="error",
                        error=err,
                        usage=usage,
                        native_session_id=cid,
                    )
                append_event(session.session_id, "turn_end", {"stop_reason": "end_turn"}, self.home)
                return TurnResult(
                    text=result_text,
                    files_changed=sorted(files),
                    stop_reason="end_turn",
                    usage=usage,
                    native_session_id=cid,
                    warnings=exit_warnings,
                )
            if proc.returncode not in (0, None) and not text_parts:
                return TurnResult(text="", stop_reason="error", error=f"agy exit {proc.returncode}")
            session.native_session_id = conversation_id
            append_event(session.session_id, "turn_end", {"stop_reason": "end_turn"}, self.home)
            return TurnResult(
                text="".join(text_parts),
                files_changed=sorted(files),
                stop_reason="end_turn",
                usage=usage,
                native_session_id=conversation_id,
                warnings=exit_warnings,
            )
        except asyncio.CancelledError:
            if proc.pid:
                kill_tree(proc.pid)
            append_event(session.session_id, "turn_end", {"stop_reason": "cancelled"}, self.home)
            return TurnResult(
                text="".join(text_parts),
                files_changed=sorted(files),
                stop_reason="cancelled",
                native_session_id=conversation_id,
            )
        except Exception:
            # e.g. readline() past STDIO_LIMIT. Without this, agy would keep
            # running with nobody reading its stdout and never get reaped.
            if proc.returncode is None and proc.pid:
                kill_tree(proc.pid)
            raise
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass
            self._procs.pop(session.session_id, None)
            self._cancelled.discard(session.session_id)
            drop_pid(self.home, session.session_id)

    async def cancel(self, session: Session) -> None:
        self._cancelled.add(session.session_id)
        proc = self._procs.get(session.session_id)
        if proc and proc.pid:
            kill_tree(proc.pid)

    async def shutdown(self, session: Session) -> None:
        await self.cancel(session)
        self._procs.pop(session.session_id, None)
