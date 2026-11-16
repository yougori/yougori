"""Microsoft Agent Framework: an Agent on the framework's OpenAI chat client, pointed at the relay,
with this terminal's tools as its function tools. Its session keeps the conversation."""
import asyncio

NAME = "Microsoft Agent Framework"
ENGINE = "Microsoft Agent Framework · Agent with function tools"
TAGLINE = "enterprise agents and orchestration"
PLACEHOLDER = "Tell the Agent Framework agent what to do"


class Run:
    def __init__(self, kit):
        from agent_framework import Agent
        # The chat-completions client: the framework's OpenAIChatClient speaks the Responses API with
        # stored responses, which this inference does not keep (it takes the whole history each time).
        from agent_framework.openai import OpenAIChatCompletionClient
        client = OpenAIChatCompletionClient(kit.model, api_key=kit.api_key, base_url=kit.base_url)
        self.agent = Agent(client, kit.instructions("You are an agent built with Microsoft Agent Framework."),
                           name="Yougori", tools=list(kit.tools))
        self.kit = kit
        self.loop = asyncio.new_event_loop()
        self.session = self.agent.create_session()

    async def _turn(self, text):
        response = await self.agent.run(text, session=self.session, options={"max_tokens": self.kit.reply})
        return getattr(response, "text", None) or str(response)

    def turn(self, text):
        return self.loop.run_until_complete(self._turn(text))


def start(kit):
    return Run(kit)
