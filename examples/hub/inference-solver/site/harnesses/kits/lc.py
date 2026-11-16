"""What the LangChain-based kits share (LangGraph, Deep Agents, Open Deep Research): the chat model
on the relay, this terminal's tools as LangChain tools, and a graph's turn streamed onto the screen:
its thinking between tool calls, and a row for each call the graph's own tools make."""
import json
import uuid

from terminal_ui import screen


def chat_model(kit, **more):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=kit.model, base_url=kit.base_url, api_key=kit.api_key, max_tokens=kit.reply,
                      temperature=more.pop("temperature", 0.2), timeout=600, max_retries=2, **more)


def tools(functions):
    from langchain_core.tools import StructuredTool
    return [StructuredTool.from_function(function) for function in functions]


def text_of(content):
    """A message's content as text: a string, or the text parts of a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return "" if content is None else str(content)


def short(value, most=90):
    value = " ".join(str(value).split())
    return value if len(value) <= most else value[:most - 1] + "…"


def call_title(name, arguments):
    """A row's title for one of the graph's own tool calls."""
    if not isinstance(arguments, dict):
        return name
    for key in ("file_path", "path", "pattern", "command", "url", "query", "description", "subagent_type"):
        if arguments.get(key):
            return "%s %s" % (name, short(arguments[key], 80))
    if name == "write_todos" and isinstance(arguments.get("todos"), list):
        return "Plan: %d steps" % len(arguments["todos"])
    return name


class Graph:
    """A compiled LangGraph graph with a checkpointer, run one turn at a time on one thread. `drawn`
    names the tools that draw their own rows (this terminal's), so the stream draws only the others."""

    def __init__(self, kit, graph, drawn=(), limit=80):
        self.kit, self.graph, self.drawn, self.limit = kit, graph, set(drawn), limit
        self.thread = str(uuid.uuid4())

    def config(self):
        return {"configurable": {"thread_id": self.thread}, "recursion_limit": self.limit}

    def turn(self, text):
        answer, pending = "", {}
        for update in self.graph.stream({"messages": [{"role": "user", "content": text}]}, self.config(), stream_mode="updates"):
            for node, value in (update or {}).items():
                messages = value.get("messages", []) if isinstance(value, dict) else []
                if not isinstance(messages, list):
                    messages = [messages]
                for message in messages:
                    kind = getattr(message, "type", "")
                    if kind == "ai":
                        words = text_of(message.content).strip()
                        calls = getattr(message, "tool_calls", None) or []
                        if calls:
                            if words:
                                screen.thought(words)
                            for call in calls:
                                pending[call.get("id")] = call
                        elif words:
                            answer = words
                    elif kind == "tool":
                        call = pending.pop(getattr(message, "tool_call_id", None), None) or {}
                        name = call.get("name") or getattr(message, "name", "") or "tool"
                        if name in self.drawn:
                            continue
                        failed = getattr(message, "status", "") == "error"
                        screen.row("Tool", call_title(name, call.get("args")), "failed" if failed else "done")
                        if failed:
                            screen.output(text_of(message.content), limit=3)
        return answer

    def stopped(self):
        """A stopped turn is taken back, so the conversation stays one the model takes (no tool call
        left without its result)."""
        from langchain_core.messages import RemoveMessage
        state = self.graph.get_state(self.config())
        messages = (state.values or {}).get("messages", []) if state else []
        last = max((i for i, m in enumerate(messages) if getattr(m, "type", "") == "human"), default=None)
        if last is not None:
            self.graph.update_state(self.config(), {"messages": [RemoveMessage(id=m.id) for m in messages[last:]]})


def trimmed(kit, messages, room=None):
    """The latest messages that fit the model's window (about 3.5 characters a token), starting at
    a person's message so no tool result is left without its call."""
    room = int((room or (kit.window - kit.reply - 3000)) * 3.5)
    kept, used = [], 0
    for message in reversed(messages):
        size = len(text_of(getattr(message, "content", ""))) + len(json.dumps(getattr(message, "tool_calls", None) or [], default=str))
        if kept and used + size > room:
            break
        kept.append(message)
        used += size
    kept.reverse()
    while kept and getattr(kept[0], "type", "") != "human" and len(kept) < len(messages):
        kept.pop(0)
    return kept or messages[-1:]
