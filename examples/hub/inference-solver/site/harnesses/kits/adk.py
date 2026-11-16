"""Google Agent Development Kit: an ADK LlmAgent on ADK's own Runner and session service, its model
through ADK's LiteLLM connector to the relay. The session keeps the conversation."""
import asyncio

NAME = "Google ADK"
ENGINE = "Agent Development Kit · LlmAgent on ADK's Runner"
TAGLINE = "Google's Agent Development Kit"
PLACEHOLDER = "Tell the ADK agent what to do"
QUIET_ENV = {"GOOGLE_GENAI_USE_VERTEXAI": "FALSE", "ADK_DISABLE_TELEMETRY": "1", "LITELLM_LOCAL_MODEL_COST_MAP": "True"}
APP, USER = "yougori", "person"


class Run:
    def __init__(self, kit):
        from google.adk.agents import LlmAgent
        from google.adk.models.lite_llm import LiteLlm
        from google.adk.runners import InMemoryRunner
        model = LiteLlm(model="openai/" + kit.model, api_base=kit.base_url, api_key=kit.api_key, max_tokens=kit.reply)
        agent = LlmAgent(name="yougori_agent", model=model, tools=list(kit.tools),
                         instruction=kit.instructions("You are an agent built with Google's Agent Development Kit."))
        self.runner = InMemoryRunner(agent=agent, app_name=APP)
        self.loop = asyncio.new_event_loop()
        self.session = self.loop.run_until_complete(self.runner.session_service.create_session(app_name=APP, user_id=USER))

    async def _turn(self, text):
        from google.genai import types
        message = types.Content(role="user", parts=[types.Part(text=text)])
        answer = []
        async for event in self.runner.run_async(user_id=USER, session_id=self.session.id, new_message=message):
            content = getattr(event, "content", None)
            parts = getattr(content, "parts", None) or []
            words = "".join(getattr(part, "text", None) or "" for part in parts if not getattr(part, "thought", False))
            if words and event.is_final_response():
                answer.append(words)
        return "\n".join(answer)

    def turn(self, text):
        return self.loop.run_until_complete(self._turn(text))


def start(kit):
    return Run(kit)
