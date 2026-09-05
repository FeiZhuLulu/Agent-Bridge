from __future__ import annotations

import json
import os
import sys

CONVERSATION_ID = "conv-fake-agy"


def _fail(reason: str) -> int:
    print(reason, file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    args = argv[1:]
    if os.environ.get("FAKE_AGY_MODE") == "early_exit":
        print("fake agy refused to start", file=sys.stderr, flush=True)
        return 3

    if "--input-format" not in args or args[args.index("--input-format") + 1] != "stream-json":
        return _fail("missing --input-format stream-json")
    if "--output-format" not in args or args[args.index("--output-format") + 1] != "stream-json":
        return _fail("missing --output-format stream-json")
    if "-p" not in args:
        return _fail("missing -p")
    if args[args.index("-p") + 1] != "":
        return _fail("-p must be followed by an empty string")

    raw = sys.stdin.readline()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _fail(f"stdin is not JSON: {exc}")
    if not isinstance(obj, dict):
        return _fail("stdin JSON must be an object")
    if obj.get("event") != "user":
        return _fail('event must be "user"')
    message = obj.get("message")
    if not isinstance(message, dict):
        return _fail("message must be an object")
    if message.get("role") != "user":
        return _fail('message.role must be "user"')
    content = message.get("content")
    if not isinstance(content, str):
        return _fail("message.content must be a string")

    report = os.environ.get("FAKE_AGY_REPORT")
    if report:
        with open(report, "w", encoding="utf-8") as handle:
            handle.write(str(len(content)))

    echo = "echo:" + content[:16]
    print(json.dumps({"event": "init", "conversation_id": CONVERSATION_ID}))
    print(
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": CONVERSATION_ID,
                    "step_type": "agent_response",
                    "text_delta": echo,
                },
            }
        )
    )
    print(
        json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": CONVERSATION_ID,
                    "status": "SUCCESS",
                    "response": echo,
                    "usage": {"total_tokens": 1},
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
