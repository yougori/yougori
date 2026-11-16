"""ChemCrow: a chemistry agent. ChemCrow's own ReAct agent answers with its chemistry tools: RDKit
for molecular weight, functional groups and similarity, name and SMILES lookups (PubChem, when the
VM reaches the web) and safety checks. Tools that need a paid service's key are left out. Each tool
call shows on a row."""
import re

from terminal_ui import screen

NAME = "ChemCrow"
ENGINE = "ChemCrow ReAct agent · RDKit and chemistry tools"
TAGLINE = "chemistry questions with real tools"
PLACEHOLDER = "Ask a chemistry question"
WORKING = "Working it out"
MARKERS = re.compile(r"<\|?channel\|?>(?:thought)?\s*")


class Run:
    def __init__(self, kit):
        if not kit.web:
            # Its tools fetch chemistry data from the internet as they load (PubChem, molecule
            # catalogues); in a VM that reaches only the inference that start waits without end.
            raise ValueError("ChemCrow needs the web: its chemistry tools fetch their data as they start. "
                             "Start Yougori with --internet full (the whole internet as well).")
        import langchain
        from langchain.callbacks.base import BaseCallbackHandler
        import chemcrow.agents.chemcrow as engine
        self.kit = kit

        class Chat(langchain.chat_models.ChatOpenAI):
            # A model's thinking markers (Gemma's <|channel>thought ... <channel|>) are not part of the
            # answer ChemCrow's ReAct parser reads: they are taken out of each reply.
            def _create_chat_result(self, response):
                result = super()._create_chat_result(response)
                for generation in result.generations:
                    words = MARKERS.sub("", generation.message.content or "")
                    generation.message.content, generation.text = words, words
                return result

        def model(name, temp, api_key, streaming=False):
            # ChemCrow knows only OpenAI's model names; its model is this inference's, on the relay.
            return Chat(model_name=kit.model, temperature=temp, openai_api_key=kit.api_key, openai_api_base=kit.base_url,
                        max_tokens=kit.reply, request_timeout=600, streaming=False)
        engine._make_llm = model
        self.agent = engine.ChemCrow(model=kit.model, tools_model=kit.model, temp=0.1, max_iterations=20, verbose=False,
                                     streaming=False, openai_api_key=kit.api_key, api_keys={})
        names = [t.name for t in self.agent.agent_executor.tools]

        class Rows(BaseCallbackHandler):
            def on_tool_start(self, serialized, input_str, **kwargs):
                self.started = ((serialized or {}).get("name") or "Tool", str(input_str))

            def on_tool_end(self, output, **kwargs):
                name, given = getattr(self, "started", ("Tool", ""))
                screen.row("Tool", ("%s %s" % (name, " ".join(given.split())))[:100], "done")
                screen.output(str(output), limit=2)

            def on_tool_error(self, error, **kwargs):
                name, given = getattr(self, "started", ("Tool", ""))
                screen.row("Tool", ("%s %s" % (name, given))[:100], "failed")
                screen.output(str(error), limit=2)

        self.rows = Rows()
        self.tools_words = "RDKit and ChemCrow's chemistry tools"
        self.facts = [("Tools", ", ".join(names))]

    def turn(self, text):
        outputs = self.agent.agent_executor({"input": text}, callbacks=[self.rows])
        return outputs.get("output", "")


def start(kit):
    return Run(kit)
