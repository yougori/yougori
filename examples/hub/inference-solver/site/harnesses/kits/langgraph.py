"""LangGraph: a stateful agent graph built with LangGraph's own StateGraph, a model node and a tool
node joined by its tools condition, its conversation kept by LangGraph's checkpointer."""
from kits import lc

NAME = "LangGraph"
ENGINE = "LangGraph StateGraph · model and tool nodes, checkpointed"
TAGLINE = "stateful agent graphs"
PLACEHOLDER = "Tell the LangGraph agent what to do"


def start(kit):
    from typing import Annotated, TypedDict
    from langchain_core.messages import SystemMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import START, StateGraph
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode, tools_condition

    class State(TypedDict):
        messages: Annotated[list, add_messages]

    tools = lc.tools(kit.tools)
    model = lc.chat_model(kit).bind_tools(tools)
    system = SystemMessage(kit.instructions("You are an agent built with LangGraph, working step by step with tools."))

    def agent(state: State):
        return {"messages": [model.invoke([system] + lc.trimmed(kit, state["messages"]))]}

    graph = StateGraph(State)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    run = lc.Graph(kit, graph.compile(checkpointer=InMemorySaver()), drawn=[t.name for t in tools])
    run.facts = [("Graph", "agent → tools → agent, until the model answers")]
    return run
