"""Vanna: questions in words answered from a database. Vanna 2.0's own agent writes the SQL and runs
it with its RunSqlTool on a SQLite database: the one the person named, else the first one in their
folder, else one made from the folder's CSV files (a table each). Each query shows on a row."""
import asyncio
import csv
import re
import sqlite3
import uuid
from pathlib import Path

from terminal_ui import screen

NAME = "Vanna"
ENGINE = "Vanna 2.0 agent · SQL with RunSqlTool on SQLite"
TAGLINE = "ask your database in words"
PLACEHOLDER = "Ask a question about your data"
WORKING = "Querying"
DATABASES = ("*.sqlite", "*.sqlite3", "*.db")


def table_name(path, taken):
    name = re.sub(r"\W+", "_", path.stem).strip("_").lower() or "data"
    if name[0].isdigit():
        name = "t_" + name
    while name in taken:
        name += "_"
    taken.add(name)
    return name


def from_csv(files, target):
    """A SQLite database holding each CSV file as a table of text columns (SQLite compares numbers in
    text as numbers when the SQL casts them)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    db, taken, made = sqlite3.connect(target), set(), []
    for path in files:
        with path.open(newline="", encoding="utf-8", errors="replace") as stream:
            rows = list(csv.reader(stream))
        if not rows:
            continue
        columns = [re.sub(r"\W+", "_", c).strip("_") or "column_%d" % i for i, c in enumerate(rows[0], 1)]
        name = table_name(path, taken)
        typed = []
        for i, column in enumerate(columns):
            values = [r[i] for r in rows[1:] if i < len(r) and r[i].strip()]
            numeric = values and all(re.fullmatch(r"-?\d+(\.\d+)?", v.strip()) for v in values[:500])
            typed.append('"%s" %s' % (column, "REAL" if numeric else "TEXT"))
        db.execute('CREATE TABLE "%s" (%s)' % (name, ", ".join(typed)))
        db.executemany('INSERT INTO "%s" VALUES (%s)' % (name, ", ".join("?" * len(columns))),
                       [(r + [""] * len(columns))[:len(columns)] for r in rows[1:]])
        made.append(name)
    db.commit()
    db.close()
    return made


def database(kit):
    """The database to ask, and how it was found, in words."""
    named = (kit.answers.get("database") or "").strip() if isinstance(kit.answers.get("database"), str) else ""
    if named:
        path = (kit.workspace / named) if not Path(named).is_absolute() else Path(named)
        if not path.is_file():
            raise ValueError("No database at %s in the folder." % named)
        return path, "%s, as you named it" % named
    for pattern in DATABASES:
        found = sorted(p for p in kit.workspace.rglob(pattern) if ".git" not in p.parts)
        if found:
            return found[0], "%s, the folder's database" % found[0].relative_to(kit.workspace)
    files = sorted(p for p in kit.workspace.rglob("*.csv") if ".git" not in p.parts)[:50]
    if files:
        target = Path.home() / ".yougori" / "vanna" / "from-csv.sqlite"
        made = from_csv(files, target)
        return target, "made from %d CSV file%s (tables %s)" % (len(made), "" if len(made) == 1 else "s", ", ".join(made))
    raise ValueError("Vanna needs data: put a SQLite database (.sqlite or .db) or CSV files in the folder, "
                     "or name the database when you start it.")


def schema(path):
    db = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        rows = db.execute("SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    finally:
        db.close()
    return "\n".join(sql for _, sql in rows if sql)


class Run:
    def __init__(self, kit):
        from vanna import Agent, AgentConfig, User
        from vanna.core.registry import ToolRegistry
        from vanna.core.user import RequestContext, UserResolver
        from vanna.integrations.local.agent_memory.in_memory import DemoAgentMemory
        from vanna.integrations.openai import OpenAILlmService
        from vanna.integrations.sqlite import SqliteRunner
        from vanna.tools import RunSqlTool

        self.path, self.found = database(kit)

        class Shown(SqliteRunner):
            async def run_sql(self, args, context):
                with screen.step("SQL", " ".join(str(getattr(args, "sql", "")).split())[:110]) as step:
                    result = await super().run_sql(args, context)
                    step.note = "%d rows" % len(result) if hasattr(result, "__len__") else ""
                    return result

        class Person(UserResolver):
            async def resolve_user(self, request_context):
                return User(id="person", username="person", group_memberships=["admin", "user"])

        from vanna.integrations.local.file_system import LocalFileSystem
        tools = ToolRegistry()
        # Vanna keeps each query's rows as a CSV file for its charts: in its home, not the person's folder.
        files = LocalFileSystem(working_directory=str(Path.home() / ".yougori" / "vanna" / "results"))
        tools.register_local_tool(RunSqlTool(sql_runner=Shown(database_path=str(self.path)), file_system=files), access_groups=[])
        self.agent = Agent(llm_service=OpenAILlmService(model=kit.model, api_key=kit.api_key, base_url=kit.base_url),
                           tool_registry=tools, user_resolver=Person(), agent_memory=DemoAgentMemory(),
                           config=AgentConfig(stream_responses=False, include_thinking_indicators=False, temperature=0.2))
        self.context = RequestContext
        self.conversation = str(uuid.uuid4())
        self.loop = asyncio.new_event_loop()
        self.schema = schema(self.path)
        self.tools_words = 'SQL queries on the database'
        self.facts = [("Database", self.found)]
        self.first = True

    async def _turn(self, text):
        if self.first:
            # Vanna's agent learns the tables from the first question's words.
            text = "The SQLite database has these tables:\n%s\n\nQuestion: %s" % (self.schema[:6000], text)
            self.first = False
        said = []
        async for component in self.agent.send_message(self.context(), text, conversation_id=self.conversation):
            simple = getattr(component, "simple_component", None)
            words = getattr(simple, "text", None) if simple is not None else None
            if words and words.strip():
                said.append(words.strip())
        # The last text is the agent's answer; the ones before it are its tools' results, already shown.
        return said[-1] if said else ""

    def turn(self, text):
        return self.loop.run_until_complete(self._turn(text))


def start(kit):
    return Run(kit)
