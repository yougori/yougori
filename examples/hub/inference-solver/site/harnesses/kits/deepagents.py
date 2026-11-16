"""Deep Agents: LangChain's agent harness for long tasks, with its own planning (write_todos), its
own file tools on the real workspace (FilesystemBackend), its subagents (task), and this terminal's
shell. Its conversation is kept by LangGraph's checkpointer."""
from kits import lc

NAME = "Deep Agents"
ENGINE = "Deep Agents · planning, file tools, subagents"
TAGLINE = "an agent harness for long tasks"
PLACEHOLDER = "Give Deep Agents a task"


def start(kit):
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend
    from langgraph.checkpoint.memory import InMemorySaver

    # Its own file tools (ls, read_file, write_file, edit_file, glob, grep) work on the workspace;
    # this terminal adds what it has no tool for: the shell, and the web when the VM reaches it.
    own = {"list_files", "read_file", "write_file", "search_files"}
    extra = [tool for tool, plain in zip(kit.tools, kit.plain_tools) if plain.__name__ not in own]
    tools = lc.tools(extra)
    agent = create_deep_agent(
        model=lc.chat_model(kit), tools=tools,
        system_prompt=kit.instructions("You are a Deep Agents agent. Plan longer tasks with write_todos, keep "
                                       "notes in files, and hand separate parts to subagents with task."),
        backend=FilesystemBackend(root_dir=str(kit.workspace), virtual_mode=False),
        checkpointer=InMemorySaver())
    run = lc.Graph(kit, agent, drawn=[t.name for t in tools], limit=150)
    run.tools_words = "planning, files, subagents and shell" + (", the web" if kit.web else "")
    return run
