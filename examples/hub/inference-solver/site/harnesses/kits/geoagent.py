"""GeoAgent: geospatial analysis and maps. GeoAgent (opengeos) binds its agent, built on Strands, to a
leafmap map: it adds layers, vector and raster data, basemaps and markers with its own map tools,
and reads the person's GeoJSON, shapefiles and rasters in the folder with this terminal's tools.
After each turn the map is saved to the folder's maps/map.html, to open in any browser."""

from terminal_ui import screen

NAME = "GeoAgent"
ENGINE = "GeoAgent on Strands · leafmap map tools"
TAGLINE = "geospatial analysis and maps"
PLACEHOLDER = "Ask for a map or a geospatial analysis"
WORKING = "Mapping"


class Run:
    def __init__(self, kit):
        import leafmap.foliumap as leafmap
        from geoagent import GeoAgentConfig, auto_approve_all, for_leafmap
        self.kit = kit
        config = GeoAgentConfig(provider="openai-compatible", model=kit.model, openai_compatible_base_url=kit.base_url,
                                openai_compatible_api_key=kit.api_key, max_tokens=kit.reply, temperature=0.1)
        self.map = leafmap.Map(center=[20, 0], zoom=2)
        # The map tools change only this session's map, so each is approved; the shell and files are
        # this terminal's, shown on their rows.
        from strands import tool
        self.agent = for_leafmap(self.map, config=config, confirm=auto_approve_all, extra_tools=[tool(f) for f in kit.tools])
        self.path = kit.workspace / "maps" / "map.html"
        self.facts = [("Map", "leafmap (folium), saved to maps/map.html after each turn")]

    def turn(self, text):
        response = self.agent.chat(text)
        words = getattr(response, "answer_text", None) or getattr(response, "text", None) or str(response)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.map.to_html(str(self.path))
            screen.row("Map", "Saved maps/map.html", "done")
        except Exception as error:  # the answer stands without the file
            screen.warn("The map could not be saved: %s" % error)
        return words


def start(kit):
    return Run(kit)
