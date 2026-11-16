"""Strands Agents: the AWS agent SDK's model-driven loop. One Strands Agent keeps the conversation,
calls the tools as it decides, and answers; its OpenAI model provider points at the relay."""
NAME = "Strands Agents"
ENGINE = "Strands Agents · model-driven agent loop"
TAGLINE = "a model-driven agent SDK"
PLACEHOLDER = "Tell the Strands agent what to do"


class Run:
    def __init__(self, kit):
        from strands import Agent, tool
        from strands.models.openai import OpenAIModel
        model = OpenAIModel(client_args={"api_key": kit.api_key, "base_url": kit.base_url}, model_id=kit.model,
                            params={"max_tokens": kit.reply, "temperature": 0.2})
        self.agent = Agent(model=model, tools=[tool(function) for function in kit.tools], callback_handler=None,
                           system_prompt=kit.instructions("You are an agent built with Strands Agents."))

    def turn(self, text):
        result = self.agent(text)
        message = getattr(result, "message", None) or {}
        parts = message.get("content", []) if isinstance(message, dict) else []
        words = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        return words or str(result)


def start(kit):
    return Run(kit)
