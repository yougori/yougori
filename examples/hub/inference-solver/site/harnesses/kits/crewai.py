"""CrewAI: each turn is a crew's task. A Specialist works it with the tools and a Reviewer checks the
result before it comes back, in CrewAI's own sequential process; the turns before are the task's
context."""
import os

NAME = "CrewAI"
ENGINE = "CrewAI crew · Specialist and Reviewer, sequential"
TAGLINE = "coordinated agents for business workflows"
PLACEHOLDER = "Give the crew a task"
WORKING = "The crew is working"
QUIET_ENV = {"CREWAI_DISABLE_TELEMETRY": "true", "OTEL_SDK_DISABLED": "true", "CREWAI_TRACING_ENABLED": "false",
             "CREWAI_DISABLE_TRACKING": "true", "CREWAI_STORAGE_DIR": os.path.expanduser("~/.yougori/crewai")}
KEEP = 6  # earlier turns given as context


class Run:
    def __init__(self, kit):
        from crewai import LLM, Agent
        from crewai.tools import tool
        self.kit, self.past = kit, []
        llm = LLM(model="openai/" + kit.model, base_url=kit.base_url, api_key=kit.api_key, max_tokens=kit.reply,
                  temperature=0.2, timeout=600)
        tools = [tool(function.__name__)(function) for function in kit.tools]
        where = kit.instructions("")
        self.specialist = Agent(role="Specialist", goal="Do the person's task completely, with the tools, and report what was done.",
                                backstory=where, llm=llm, tools=tools, allow_delegation=False, verbose=False, max_iter=25)
        self.reviewer = Agent(role="Reviewer", goal="Check the Specialist's work against the task and give the person the final answer.",
                              backstory="You review work for correctness and completeness before it reaches the person. " + where,
                              llm=llm, tools=[tool(f.__name__)(f) for f in kit.tools if f.__name__ in ("read_file", "list_files")],
                              allow_delegation=False, verbose=False, max_iter=8)
        self.facts = [("Crew", "Specialist (all tools) → Reviewer (reads files)")]

    def finished(self, output):
        """A row as each of the crew's tasks ends: who did it, and the first lines of what they gave."""
        from terminal_ui import screen
        who = getattr(output, "agent", "") or "Crew"
        screen.row("Crew", "%s finished" % str(who).strip(), "done")
        screen.output(str(getattr(output, "raw", "") or ""), limit=2)

    def turn(self, text):
        from crewai import Crew, Process, Task
        context = "".join("\nEarlier, the person asked: %s\nThe crew answered: %s\n" % pair for pair in self.past[-KEEP:])
        work = Task(description=(("Conversation so far:%s\n" % context if context else "") + "The person's task now: " + text),
                    expected_output="What was done and found, with any files changed, in Markdown.", agent=self.specialist)
        review = Task(description="Review the Specialist's result for the person's task: " + text +
                      "\nIf something is wrong or missing, say so plainly. Then give the final answer for the person.",
                      expected_output="The final answer for the person, in Markdown.", agent=self.reviewer, context=[work])
        crew = Crew(agents=[self.specialist, self.reviewer], tasks=[work, review], process=Process.sequential, verbose=False,
                    task_callback=self.finished)
        result = crew.kickoff()
        answer = str(getattr(result, "raw", result) or "")
        self.past.append((text, answer[:2000]))
        return answer


def start(kit):
    return Run(kit)
