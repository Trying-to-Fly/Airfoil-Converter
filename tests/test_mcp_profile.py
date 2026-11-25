"""Phase 2 through the server: sections cut, made, split, morphed, measured, placed and laid
along a law, UIUC files read and fetched — all through a real client session.

No test reaches the network: fetch_uiuc is given a stand-in opener.
"""

import json
import math
import os
import shutil
import urllib.error

import pytest

pytest.importorskip("mcp.server.mcpserver")

from airfoil_converter import parser  # noqa: E402
from airfoil_converter.mcp import server as mcp_server, tools_profile  # noqa: E402

from mcp_tools import (  # noqa: E402
    anyio_backend,  # noqa: F401
    call,
    refused,
    rib_spec,
    session,
    workspace_with_sample,
)

pytestmark = pytest.mark.anyio

UIUC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uiuc")


@pytest.fixture
def workspace(tmp_path):
    folder = workspace_with_sample(tmp_path / "ws")
    for name in ("sd7037.dat", "clarky.dat"):
        shutil.copy(os.path.join(UIUC, name), os.path.join(folder, name))
    return folder


def files_under(folder):
    return sorted(os.path.relpath(os.path.join(root, name), folder)
                  for root, _, names in os.walk(folder) for name in names)


# -- the tools there are ----------------------------------------------------------


async def test_fetching_is_offered_only_when_the_network_is_allowed(workspace):
    async with session(workspace) as client:
        offline = {tool.name for tool in (await client.list_tools()).tools}
        info = await call(client, "converter_info")
    async with session(workspace, allow_network=True, opener=lambda *a: None) as client:
        online = {tool.name for tool in (await client.list_tools()).tools}
        online_info = await call(client, "converter_info")
    assert "fetch_uiuc" not in offline and online - offline == {"fetch_uiuc"}
    assert info["network"] is False and info["capabilities"]["fetch_uiuc"] is False
    assert online_info["network"] is True and online_info["capabilities"]["fetch_uiuc"] is True
    assert "naca4" in info["capabilities"]["profiles"]


def test_the_command_line_takes_the_network_flag():
    assert mcp_server.build_parser().parse_args(["--workspace", "."]).allow_network is False
    args = mcp_server.build_parser().parse_args(["--workspace", ".", "--allow-network"])
    assert args.allow_network is True


# -- a blunt trailing edge -----------------------------------------------------------


async def test_a_blunt_cut_says_where_it_fell_and_how_far_it_grew(workspace):
    async with session(workspace) as client:
        kept = await call(client, "blunt_trailing_edge", section="sd7037-il.csv",
                          thickness_mm=0.5, keep_chord=True)
        cut = await call(client, "blunt_trailing_edge", section="sd7037-il.csv",
                         thickness_mm=0.5)
    assert kept["cut"] and kept["scale"] > 1.0
    assert kept["scale"] * kept["cut_chord_mm"] == pytest.approx(kept["span_mm"], abs=1e-9)
    assert kept["te_thickness_mm"] == pytest.approx(0.5, abs=1e-6)
    assert kept["finished_chord_mm"] == pytest.approx(kept["span_mm"], abs=1e-6)
    assert 0.9 < kept["fraction"] < 1.0
    assert kept["station_mm"] == pytest.approx(kept["fraction"] * kept["span_mm"], abs=1e-6)
    # The chord frame's chord, true nose to the middle of the trailing edge.
    assert kept["chord_mm"] == pytest.approx(174.981, abs=1e-3)
    assert cut["scale"] == 1.0
    assert cut["finished_chord_mm"] == pytest.approx(cut["cut_chord_mm"], abs=1e-6)
    assert cut["te_thickness_mm"] == pytest.approx(0.5, abs=1e-6)
    assert "points" not in cut


async def test_a_section_inline_is_the_same_section_as_its_file(workspace):
    async with session(workspace) as client:
        read = await call(client, "read_section", source="sd7037-il.csv", include_points=True)
        by_file = await call(client, "blunt_trailing_edge", section="sd7037-il.csv",
                             thickness_mm=0.4, keep_chord=True, include_points=True)
        inline = await call(client, "blunt_trailing_edge", section=read["airfoil"]["points"],
                            thickness_mm=0.4, keep_chord=True, include_points=True)
    for key in ("station_mm", "cut_chord_mm", "chord_mm", "span_mm", "scale", "points"):
        assert inline[key] == by_file[key], key
    assert inline["section"]["source"] is None and inline["section"]["units"] == "mm"


async def test_a_section_in_fractions_needs_its_chord_in_mm(workspace):
    async with session(workspace) as client:
        text = await refused(client, "blunt_trailing_edge", section="sd7037.dat",
                             thickness_mm=0.5)
        sized = await call(client, "blunt_trailing_edge", section="sd7037.dat",
                           thickness_mm=0.5, keep_chord=True, chord_mm=175)
    assert "fractions of its chord" in text and "chord_mm" in text
    # chord_mm is the chord frame's chord, exactly as given; the points span a hair less.
    assert sized["chord_mm"] == pytest.approx(175.0, abs=1e-9)
    assert sized["span_mm"] < 175.0
    assert sized["te_thickness_mm"] == pytest.approx(0.5, abs=1e-6)


# -- NACA sections, and saving a profile --------------------------------------------------


async def test_a_naca_0012_is_twelve_percent_thick_at_thirty_percent(workspace):
    async with session(workspace) as client:
        answer = await call(client, "naca4", digits="0012")
        sized = await call(client, "naca4", digits="NACA 0012", chord_mm=200, include_points=True)
    assert answer["name"] == "NACA 0012" and answer["point_count"] == 321
    assert answer["units"] == parser.CHORD_FRACTIONS
    assert answer["max_thickness"] == pytest.approx(0.12, abs=1e-3)
    assert answer["max_thickness_at"] == pytest.approx(0.30, abs=1e-2)
    assert answer["nose_radius"] == pytest.approx(1.1019 * 0.12 ** 2, rel=0.01)
    assert answer["te_gap"] == 0.0 and answer["sharp"]
    assert sized["max_thickness_mm"] == pytest.approx(24.0, abs=0.2)
    xs = [p[0] for p in sized["points"]]
    assert max(xs) - min(xs) == pytest.approx(200.0)
    assert files_under(workspace) == ["clarky.dat", "sd7037-il.csv", "sd7037.dat"]


async def test_a_saved_naca_section_exports_once_it_has_a_chord(workspace):
    async with session(workspace) as client:
        saved = await call(client, "naca4", digits="2412", save_as="profiles/naca2412.dat")
        read = await call(client, "read_section", source=saved["saved"])
        text = await refused(client, "export_rib", project="part.SLDPRT", source=saved["saved"])
        rib = await call(client, "export_rib", project="part.SLDPRT", source=saved["saved"],
                         name="naca", spec=rib_spec(150))
    assert saved["saved"] == "profiles/naca2412.dat" and saved["changed"]
    assert read["kind"] == "dat" and read["name"] == "NACA 2412" and read["point_count"] == 321
    assert "fractions of its chord" in text and "target chord" in text
    assert rib["chord_mm"] == pytest.approx(150.0, abs=1e-6)


@pytest.mark.parametrize("digits, said", [("24", "four digits"), ("2012", "nose")])
async def test_a_naca_name_that_is_not_one_is_refused(workspace, digits, said):
    async with session(workspace) as client:
        assert said in await refused(client, "naca4", digits=digits)


# -- UIUC coordinate files -----------------------------------------------------------------


async def test_a_uiuc_file_reads_as_fractions_of_its_chord(workspace):
    async with session(workspace) as client:
        selig = await call(client, "read_section", source="sd7037.dat")
        lednicer = await call(client, "read_section", source="clarky.dat")
    assert selig["kind"] == "dat" and selig["units"] == parser.CHORD_FRACTIONS
    assert selig["chord"] == 1.0 and selig["chord_mm"] is None and selig["te_gap_mm"] is None
    assert selig["metadata"]["Format"] == parser.DAT_SELIG
    assert selig["max_thickness_mm"] is None
    assert selig["max_thickness_percent"] == pytest.approx(9.2, abs=0.1)
    assert lednicer["metadata"]["Format"] == parser.DAT_LEDNICER
    assert lednicer["max_thickness_percent"] == pytest.approx(11.7, abs=0.2)


async def test_a_uiuc_file_without_a_target_chord_is_refused_clearly(workspace):
    async with session(workspace) as client:
        for tool, arguments in (
            ("finish_section", dict(source="sd7037.dat")),
            ("export_rib", dict(project="part.SLDPRT", source="sd7037.dat")),
        ):
            text = await refused(client, tool, **arguments)
            assert "is given in fractions of its chord" in text
            assert "Set a target chord in millimetres" in text
        finished = await call(client, "finish_section", source="sd7037.dat",
                              spec={"target_chord": 175})
        rib = await call(client, "export_rib", project="part.SLDPRT", source="sd7037.dat",
                         name="sd", spec=rib_spec(175))
    assert files_under(os.path.join(workspace)) == [
        "clarky.dat", "part.airfoils.json", "sd7037-il.csv", "sd7037.dat", "sd_airfoil.sldcrv",
    ]
    # The chord the points span: UIUC's runs from the origin, a hair ahead of the nose.
    assert finished["chord_mm"] == pytest.approx(175.0, abs=0.1)
    assert finished["scale"] == 175.0
    assert [c["role"] for c in finished["curves"]] == ["airfoil"]  # no camber line to write
    assert rib["chord_mm"] == finished["chord_mm"]


# -- fetching from UIUC -------------------------------------------------------------------


class Opener:
    """A stand-in for the network: says what it was asked, answers what it is told."""

    def __init__(self, body=None, final=None, error=None):
        self.asked = []
        self.body, self.final, self.error = body, final, error

    def __call__(self, url, timeout, limit):
        self.asked.append((url, timeout, limit))
        if self.error is not None:
            raise self.error
        return (self.final or url), self.body


def sd7037_bytes():
    with open(os.path.join(UIUC, "sd7037.dat"), "rb") as handle:
        return handle.read()


async def test_a_fetched_file_is_read_then_kept_beside_a_note_of_where_it_came_from(workspace):
    opener = Opener(sd7037_bytes())
    async with session(workspace, allow_network=True, opener=opener) as client:
        answer = await call(client, "fetch_uiuc", name="sd7037")
        exported = await call(client, "finish_section", source=answer["saved"],
                              spec={"target_chord": 200})
    url = "https://m-selig.web.engr.illinois.edu/ads/coord/sd7037.dat"
    assert opener.asked == [(url, tools_profile.FETCH_TIMEOUT, tools_profile.FETCH_LIMIT)]
    assert answer["saved"] == "uiuc/sd7037.dat" and answer["kind"] == "dat"
    assert answer["point_count"] == 61 and answer["url"] == url
    with open(os.path.join(workspace, "uiuc", "sd7037.dat"), "rb") as handle:
        assert handle.read() == sd7037_bytes()
    with open(os.path.join(workspace, answer["note"])) as handle:
        note = json.load(handle)
    assert note["url"] == url and note["retrieved"] == answer["retrieved"]
    assert note["bytes"] == len(sd7037_bytes())
    assert exported["scale"] == 200.0


@pytest.mark.parametrize("name", [
    "../sd7037", "sd7037.dat", "a/b", "sd7037?x=1", "sd7037#", "", " sd7037", "-x",
    "%2e%2e", "x" * 65, "sd7037\n",
])
async def test_a_name_that_could_change_the_address_is_refused_before_asking(workspace, name):
    opener = Opener(sd7037_bytes())
    async with session(workspace, allow_network=True, opener=opener) as client:
        text = await refused(client, "fetch_uiuc", name=name)
    assert "is not a UIUC file name" in text
    assert opener.asked == []


@pytest.mark.parametrize("opener, said", [
    (Opener(error=urllib.error.HTTPError("u", 404, "Not Found", {}, None)), "has no nothere.dat"),
    (Opener(error=urllib.error.URLError("timed out")), "Could not fetch"),
    (Opener(b"x", final="https://elsewhere.example/nothere.dat"), "off m-selig"),
    (Opener(b"x", final="http://m-selig.web.engr.illinois.edu/ads/coord/nothere.dat"),
     "off m-selig"),
    (Opener(b"0 0\n" * (tools_profile.FETCH_LIMIT // 4 + 1)), "larger than"),
    (Opener(b"<html><body>Not a coordinate file</body></html>\n"), "not a UIUC coordinate file"),
    (Opener(b"sd7037\n0 0\n1 2 3\n"), "not an x y pair"),
])
async def test_a_fetch_that_goes_wrong_keeps_nothing(workspace, opener, said):
    async with session(workspace, allow_network=True, opener=opener) as client:
        text = await refused(client, "fetch_uiuc", name="nothere")
    assert said in text
    assert files_under(workspace) == ["clarky.dat", "sd7037-il.csv", "sd7037.dat"]


@pytest.mark.parametrize("planted", ["sd7037.dat.tmp", "sd7037.dat", "sd7037.source.json"])
async def test_a_fetch_is_not_written_through_a_planted_link(workspace, tmp_path, planted):
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    os.makedirs(os.path.join(workspace, "uiuc"))
    os.symlink(str(victim), os.path.join(workspace, "uiuc", planted))
    opener = Opener(sd7037_bytes())
    async with session(workspace, allow_network=True, opener=opener) as client:
        text = await refused(client, "fetch_uiuc", name="sd7037")
    assert "symbolic link" in text
    assert victim.read_text() == "untouched"
    assert opener.asked == []  # checked before the network was asked


def test_a_redirect_is_followed_only_on_the_uiuc_host_over_https():
    import urllib.request

    handler = tools_profile._StayOnHost()
    request = urllib.request.Request(tools_profile.UIUC_URL.format(name="sd7037"))
    for bad in ("https://evil.example/sd7037.dat", "http://m-selig.web.engr.illinois.edu/x",
                "file:///etc/passwd", "https://m-selig.web.engr.illinois.edu.evil.example/x"):
        with pytest.raises(urllib.error.URLError):
            handler.redirect_request(request, None, 302, "Found", {}, bad)
    moved = handler.redirect_request(request, None, 302, "Found", {},
                                     "https://m-selig.web.engr.illinois.edu/ads/coord/SD7037.dat")
    assert moved.full_url.endswith("/ads/coord/SD7037.dat")


# -- camber and thickness, morphs, noses -----------------------------------------------------


async def naca_points(client, digits):
    return (await call(client, "naca4", digits=digits, include_points=True))["points"]


async def test_a_split_gives_camber_and_thickness_on_one_grid(workspace):
    async with session(workspace) as client:
        points = await naca_points(client, "2412")
        split = await call(client, "split_section", section=points, include_points=True)
        by_file = await call(client, "split_section", section="sd7037-il.csv", stations=41,
                             extra_fractions=[0.3])
    assert split["stations"] == len(split["x"]) == len(split["camber"]) == profile_stations()
    assert split["x"][0] == 0.0 and split["x"][-1] == 1.0
    assert split["max_camber"] == pytest.approx(0.02, abs=2e-3)
    assert split["max_camber_at"] == pytest.approx(0.4, abs=0.05)
    assert split["max_thickness"] == pytest.approx(0.12, abs=2e-3)
    assert split["section"]["source"] is None
    assert by_file["stations"] == 42 and "x" not in by_file
    assert by_file["chord_mm"] == pytest.approx(175.0, abs=0.05)
    assert by_file["max_thickness_mm"] == pytest.approx(16.1, abs=0.1)


def profile_stations():
    from airfoil_converter import profile

    return profile.STATIONS


async def test_a_morph_holds_its_trailing_edge_and_ends_at_its_two_sections(workspace):
    async with session(workspace) as client:
        symmetric = await naca_points(client, "0012")
        ends = []
        for weight in (0.0, 1.0):
            ends.append(await call(client, "morph_sections", first="sd7037-il.csv",
                                   second=symmetric, weight=weight))
        middle = await call(client, "morph_sections", first="sd7037-il.csv", second=symmetric,
                            weight=0.5, te_thickness_mm=0.4, chord_mm=175,
                            save_as="morph.dat")
        first = await call(client, "split_section", section="sd7037-il.csv")
        second = await call(client, "split_section", section=symmetric)
        rib = await call(client, "finish_section", source="morph.dat",
                         spec={"target_chord": 175, "export_camber": False})
        text = await refused(client, "morph_sections", first="sd7037-il.csv", second=symmetric,
                             weight=0.5, te_thickness_mm=0.4)
    assert ends[0]["max_thickness"] == pytest.approx(first["max_thickness"], abs=1e-9)
    assert ends[1]["max_thickness"] == pytest.approx(second["max_thickness"], abs=1e-9)
    assert ends[1]["max_camber"] == pytest.approx(0.0, abs=1e-9)
    assert middle["te_thickness_mm"] == pytest.approx(0.4, abs=1e-9)
    assert middle["saved"] == "morph.dat"
    assert rib["te_thickness_mm"] == pytest.approx(0.4, abs=1e-6)
    assert "needs the chord" in text


async def test_a_nose_radius_comes_in_mm_where_the_section_has_them(workspace):
    async with session(workspace) as client:
        csv = await call(client, "nose_radius", section="sd7037-il.csv", sweep_deg=30)
        dat = await call(client, "nose_radius", section="sd7037.dat")
        sized = await call(client, "nose_radius", section="sd7037.dat", chord_mm=175)
    assert csv["normal_radius"] == pytest.approx(csv["radius"] / math.cos(math.radians(30)))
    assert csv["radius_mm"] == pytest.approx(csv["radius"])
    assert dat["radius_mm"] is None
    assert sized["radius_mm"] == pytest.approx(dat["ratio"] * 175, abs=1e-6)
    assert csv["ratio"] == pytest.approx(dat["ratio"], rel=0.02)


# -- placing a section ------------------------------------------------------------------------


async def test_a_placed_section_written_reads_back_on_its_plane(workspace):
    async with session(workspace) as client:
        placed = await call(client, "place_section", section="sd7037.dat",
                            leading_edge=[10, 20, 30], chord_direction=[0, 0, -1],
                            up_direction=[0, 1, 0], chord_mm=200, twist_deg=3, pivot=0.25,
                            write="placed/rib.sldcrv")
        read = await call(client, "read_section", source="placed/rib.sldcrv")
        text = await refused(client, "place_section", section="sd7037.dat",
                             leading_edge=[0, 0, 0], chord_direction=[1, 0, 0],
                             up_direction=[0, 1, 0])
        bad = await refused(client, "place_section", section="sd7037-il.csv",
                            leading_edge=[0, 0, 0], chord_direction=[1, 0, 0],
                            up_direction=[0, 1, 0], write="placed/rib.csv")
    assert placed["written"] == {"file": "placed/rib.sldcrv", "changed": True}
    assert placed["chord_mm"] == 200.0
    # The quarter chord stays where the untwisted section had it.
    assert placed["pivot_point"] == pytest.approx([10.0, 20.0, 30.0 - 50.0])
    assert read["chord_mm"] == pytest.approx(200.0, abs=0.05)
    normal = read["plane"]["normal"]
    assert abs(normal[0]) == pytest.approx(1.0, abs=1e-6)
    assert "fractions of its chord" in text
    assert ".sldcrv or .txt" in bad


# -- a law ----------------------------------------------------------------------------------


def station(z, chord, **extra):
    """A section on the plane x = -z..., spanning along -x, nose toward +z."""
    out = {"leading_edge": [-z, 0, 0], "chord_direction": [0, 0, -1],
           "up_direction": [0, 1, 0], "chord": chord}
    out.update(extra)
    return out


async def test_a_law_writes_sections_and_guides_and_a_loft_plan_like_a_wings(workspace):
    async with session(workspace) as client:
        symmetric = await naca_points(client, "0012")
        built = await call(client, "build_law", name="winglet", folder="law",
                           first="sd7037-il.csv", second=symmetric, te_thickness_mm=0.4,
                           stations=[station(0, 175, weight=0), station(50, 150, weight=0.5),
                                     station(100, 120, weight=1, twist=-2, pivot=0.25),
                                     station(120, 100, profile="sd7037.dat")],
                           include_points=True)
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="root", spec=rib_spec(200))
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="tip", spec=rib_spec(120, x=-300, z=-20))
        wing = await call(client, "build_wing", project="part.SLDPRT", ribs=["root", "tip"],
                          name="winglet")
        clash = await refused(client, "build_law", name="winglet", project="part.SLDPRT",
                              folder="other", stations=[station(0, 175, profile="sd7037.dat"),
                                                        station(50, 150, profile="sd7037.dat")])
        listed = await call(client, "list_project", project="part.SLDPRT")
    assert set(built["loft_plan"]) == set(wing["loft_plan"])
    assert built["recorded"] is False and built["folder"] == "law"
    assert built["station_count"] == 4 and len(built["sections"]) == 4
    assert built["te_thickness_mm"] == pytest.approx(0.4, abs=1e-9)
    assert all(s["te_thickness_mm"] == pytest.approx(0.4, abs=1e-9) for s in built["stations"])
    plan = built["loft_plan"]
    assert plan["profiles"] == ["winglet_s01", "winglet_s02", "winglet_s03", "winglet_s04"]
    assert plan["profiles_once_joined"] == [p + "_joined" for p in plan["profiles"]]
    assert plan["guides"][0] == "winglet_le" and {"winglet_te_upper", "winglet_te_lower"} <= set(
        plan["guides"])
    for name, path in plan["files"].items():
        assert os.path.isfile(os.path.join(workspace, path)), name
    # Every guide meets every section at one of its points.
    curves = {c["feature"]: c["points"] for c in built["curves"]}
    for guide in plan["guides"]:
        for k, section in enumerate(plan["profiles"]):
            hit = min(math.dist(curves[guide][k], p) for p in curves[section]
                      + curves[section + "_te"])
            assert hit < 1e-6, (guide, section)
    assert "already a curve of a record" in clash
    assert not os.path.isdir(os.path.join(workspace, "other"))
    assert [w["name"] for w in listed["wings"]] == ["winglet"]  # the law recorded nothing


async def test_a_law_is_not_written_through_a_planted_link(workspace, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched")
    os.makedirs(os.path.join(workspace, "law"))
    os.symlink(str(victim), os.path.join(workspace, "law", "w_le.sldcrv.tmp"))
    async with session(workspace) as client:
        text = await refused(client, "build_law", name="w", folder="law",
                             stations=[station(0, 175, profile="sd7037.dat"),
                                       station(50, 150, profile="sd7037.dat")])
    assert "symbolic link" in text
    assert victim.read_text() == "untouched"
    assert os.listdir(os.path.join(workspace, "law")) == ["w_le.sldcrv.tmp"]


@pytest.mark.parametrize("stations, said", [
    ([station(0, 175, profile="sd7037.dat")], "at least two stations"),
    ([station(0, 175), station(50, 150, profile="sd7037.dat")], "needs a profile or a morph"),
    ([station(0, 175, weight=0), station(50, 150, weight=1)], "two sections it morphs"),
    ([{"chord": 100}, station(50, 150, profile="sd7037.dat")], "needs its leading edge"),
    ([station(0, 175, profile="../x.dat"), station(50, 150, profile="sd7037.dat")], "outside"),
])
async def test_a_law_that_cannot_be_is_refused_with_the_reason(workspace, stations, said):
    async with session(workspace) as client:
        text = await refused(client, "build_law", name="w", stations=stations)
    assert said in text
    assert files_under(workspace) == ["clarky.dat", "sd7037-il.csv", "sd7037.dat"]


# -- writing only where it was asked, and only over its own ----------------------------------


def snapshot_all(folder):
    taken = {}
    for root, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(root, name)
            if not os.path.islink(path):
                with open(path, "rb") as handle:
                    taken[os.path.relpath(path, folder)] = handle.read()
    return taken


async def test_a_link_at_the_name_asked_for_is_never_written_through(workspace):
    """A link inside the workspace, at the very name a tool is told to write, pointing at a
    project's record or a source: resolving the whole path would write over its target."""
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="root", spec=rib_spec(200))
        os.symlink(os.path.join(workspace, "part.airfoils.json"),
                   os.path.join(workspace, "placed.sldcrv"))
        os.symlink(os.path.join(workspace, "sd7037-il.csv"),
                   os.path.join(workspace, "naca.dat"))
        before = snapshot_all(workspace)
        placing = await refused(client, "place_section", section="sd7037-il.csv",
                                leading_edge=[0, 0, 0], chord_direction=[1, 0, 0],
                                up_direction=[0, 1, 0], write="placed.sldcrv", overwrite=True)
        saving = await refused(client, "naca4", digits="0012", save_as="naca.dat",
                               overwrite=True)
        morphing = await refused(client, "morph_sections", first="sd7037-il.csv",
                                 second="sd7037.dat", weight=0.5, save_as="naca.dat",
                                 overwrite=True)
    for text in (placing, saving, morphing):
        assert "symbolic link" in text
    assert snapshot_all(workspace) == before


async def test_the_extension_checked_is_the_name_written(workspace):
    async with session(workspace) as client:
        text = await refused(client, "place_section", section="sd7037-il.csv",
                             leading_edge=[0, 0, 0], chord_direction=[1, 0, 0],
                             up_direction=[0, 1, 0], write="out/rib.sldcrv/..")
        assert "folder" in text or ".sldcrv or .txt" in text
        text = await refused(client, "naca4", digits="0012", save_as="profiles/")
        assert ".dat" in text


async def test_a_file_the_tool_did_not_write_is_replaced_only_when_asked(workspace):
    with open(os.path.join(workspace, "mine.sldcrv"), "w") as handle:
        handle.write("someone's own curve\n")
    place = dict(section="sd7037-il.csv", leading_edge=[0, 0, 0], chord_direction=[1, 0, 0],
                 up_direction=[0, 1, 0], write="mine.sldcrv")
    async with session(workspace) as client:
        text = await refused(client, "place_section", **place)
        assert "already there" in text and "overwrite=true" in text
        with open(os.path.join(workspace, "mine.sldcrv")) as handle:
            assert handle.read() == "someone's own curve\n"
        text = await refused(client, "naca4", digits="0012", save_as="sd7037.dat")
        assert "already there" in text
        replaced = await call(client, "place_section", overwrite=True, **place)
        again = await call(client, "place_section", **place)  # the same bytes: nothing replaced
        saved = await call(client, "naca4", digits="0012", save_as="sd7037.dat", overwrite=True)
        listed = await client.list_tools()
    assert replaced["written"]["changed"] and not again["written"]["changed"]
    assert saved["changed"]
    hints = {tool.name: tool.annotations.destructive_hint for tool in listed.tools}
    assert {name for name, d in hints.items() if d} == {
        "export_rib", "build_wing", "naca4", "morph_sections", "place_section", "build_law",
    }


async def test_a_fetch_replaces_only_its_own_earlier_fetch(workspace):
    opener = Opener(sd7037_bytes())
    os.makedirs(os.path.join(workspace, "mine"))
    shutil.copy(os.path.join(UIUC, "clarky.dat"), os.path.join(workspace, "mine", "sd7037.dat"))
    async with session(workspace, allow_network=True, opener=opener) as client:
        await call(client, "fetch_uiuc", name="sd7037")
        again = await call(client, "fetch_uiuc", name="sd7037")
        text = await refused(client, "fetch_uiuc", name="sd7037", folder="mine")
        assert len(opener.asked) == 2  # the refusal never reached the network
        forced = await call(client, "fetch_uiuc", name="sd7037", folder="mine", overwrite=True)
        listed = await client.list_tools()
    assert not again["changed"] and "already there" in text and forced["changed"]
    worlds = {tool.name: tool.annotations.open_world_hint for tool in listed.tools}
    assert {name for name, w in worlds.items() if w} == {"fetch_uiuc"}


@pytest.mark.parametrize("folder", ["../outside", "sd7037-il.csv"])
async def test_a_fetch_checks_where_it_would_go_before_asking(workspace, folder):
    opener = Opener(sd7037_bytes())
    async with session(workspace, allow_network=True, opener=opener) as client:
        await refused(client, "fetch_uiuc", name="sd7037", folder=folder)
    assert opener.asked == []


async def test_a_broken_answer_from_the_network_is_said_in_words(workspace):
    import http.client

    opener = Opener(error=http.client.IncompleteRead(b"abc", 10))
    async with session(workspace, allow_network=True, opener=opener) as client:
        text = await refused(client, "fetch_uiuc", name="sd7037")
    assert "Could not fetch" in text and "IncompleteRead" in text


async def test_a_fetched_title_reads_the_same_in_the_note_and_the_answer(workspace):
    body = b"Caf\xe9 foil\n" + b"".join(sd7037_bytes().splitlines(keepends=True)[1:])
    async with session(workspace, allow_network=True, opener=Opener(body)) as client:
        answer = await call(client, "fetch_uiuc", name="cafe")
    with open(os.path.join(workspace, answer["note"])) as handle:
        note = json.load(handle)
    assert note["name"] == answer["name"]


async def test_a_law_replaces_its_own_curves_and_no_one_elses(workspace):
    stations = [station(0, 175, profile="sd7037.dat"), station(50, 150, profile="sd7037.dat")]
    os.makedirs(os.path.join(workspace, "law"))
    with open(os.path.join(workspace, "law", "w_s01.sldcrv"), "w") as handle:
        handle.write("someone's own curve\n")
    async with session(workspace) as client:
        text = await refused(client, "build_law", name="w", folder="law", stations=stations)
        assert "already there" in text
        first = await call(client, "build_law", name="w", folder="law", stations=stations,
                           overwrite=True)
        # Its own curves, by its note: built again without overwrite.
        again = await call(client, "build_law", name="w", folder="law",
                           stations=[station(0, 175, profile="sd7037.dat", twist=2),
                                     station(50, 150, profile="sd7037.dat")])
    assert first["manifest"] == "law/w.law.json"
    assert any(c["changed"] for c in again["curves"])


async def test_a_law_never_takes_a_name_a_project_in_its_folder_records(workspace):
    async with session(workspace) as client:
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="root", spec=rib_spec(200))
        await call(client, "export_rib", project="part.SLDPRT", source="sd7037-il.csv",
                   name="tip", spec=rib_spec(120, x=-300, z=-20))
        await call(client, "build_wing", project="part.SLDPRT", ribs=["root", "tip"], name="w")
        before = snapshot_all(workspace)
        text = await refused(client, "build_law", name="w",
                             stations=[station(0, 175, profile="sd7037.dat"),
                                       station(50, 150, profile="sd7037.dat")], overwrite=True)
    assert "already a curve of a record in part.airfoils.json" in text
    assert snapshot_all(workspace) == before


async def test_a_laws_trailing_edge_is_one_figure_only_when_every_station_holds_it(workspace):
    async with session(workspace) as client:
        thin = await call(client, "blunt_trailing_edge", section="sd7037-il.csv",
                          thickness_mm=0.5, include_points=True)
        thick = await call(client, "blunt_trailing_edge", section="sd7037-il.csv",
                           thickness_mm=1.0, include_points=True)
        built = await call(client, "build_law", name="w", folder="law",
                           stations=[station(0, 175, profile=thin["points"]),
                                     station(50, 175, profile=thick["points"])])
    assert built["te_thickness_mm"] is None
    # Each cut profile is scaled up to the station's chord, its trailing edge with it.
    held = [s["te_thickness_mm"] for s in built["stations"]]
    assert 0.5 < held[0] < 0.55 and 1.0 < held[1] < 1.1


# -- measuring, honestly ------------------------------------------------------------------


async def test_a_loop_the_split_refuses_is_still_measured(workspace):
    from airfoil_converter import export, geometry

    data, _ = export.load_source(os.path.join(workspace, "sd7037-il.csv"))
    pts = geometry.drop_duplicate(data.airfoil)
    nose = geometry.leading_edge_index(pts)
    loop = pts[nose:] + pts[:nose] + [pts[nose]]
    rows = ["Name,rotated", "Chord(mm),175", "", "Airfoil surface,", "X(mm),Y(mm)"]
    rows += [f"{x!r},{y!r}" for x, y in loop]
    with open(os.path.join(workspace, "rotated.csv"), "w") as handle:
        handle.write("\n".join(rows) + "\n")
    async with session(workspace) as client:
        answer = await call(client, "read_section", source="rotated.csv")
    assert answer["max_thickness_percent"] == pytest.approx(9.2, abs=0.05)
    assert answer["max_thickness_mm"] == pytest.approx(16.098, abs=1e-3)
    assert "start and end at the trailing edge" in answer["thickness_note"]


async def test_the_chord_in_mm_is_the_chord_frames_in_every_profile_tool(workspace):
    async with session(workspace) as client:
        split = await call(client, "split_section", section="sd7037.dat", chord_mm=175)
        nose = await call(client, "nose_radius", section="sd7037.dat", chord_mm=175)
        blunt = await call(client, "blunt_trailing_edge", section="sd7037.dat",
                           thickness_mm=0.5, chord_mm=175)
    assert split["chord_mm"] == 175.0
    assert split["max_thickness_mm"] == pytest.approx(split["max_thickness"] * 175, abs=1e-6)
    assert nose["radius_mm"] == pytest.approx(nose["ratio"] * 175, abs=1e-6)
    assert blunt["chord_mm"] == pytest.approx(175.0, abs=1e-9)


async def test_more_stations_than_a_summary_holds_are_refused_not_cut_short(workspace):
    async with session(workspace) as client:
        text = await refused(client, "split_section", section="sd7037.dat",
                             stations=tools_profile.MAX_STATIONS, extra_fractions=[0.3],
                             include_points=True)
    assert "more than" in text and str(tools_profile.MAX_STATIONS) in text


async def test_a_section_that_spans_no_chord_is_refused_in_words(workspace):
    flat = [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0], [0.0, 3.0], [0.0, 4.0]]
    async with session(workspace) as client:
        text = await refused(client, "blunt_trailing_edge", section=flat, thickness_mm=0.1,
                             chord_mm=100)
    assert "spans no chord" in text


async def test_no_answer_carries_a_negative_zero(workspace):
    async with session(workspace) as client:
        nose = await call(client, "nose_radius", section="sd7037-il.csv", sweep_deg=-0.0)
        morph = await call(client, "morph_sections", first="sd7037-il.csv",
                           second="sd7037.dat", weight=-0.0)
        blunt = await call(client, "blunt_trailing_edge", section="sd7037-il.csv",
                           thickness_mm=-0.0)
    for value in (nose["sweep_deg"], morph["weight"], blunt["thickness_mm"]):
        assert value == 0.0 and math.copysign(1.0, value) == 1.0


async def test_the_servers_instructions_name_the_tools_it_has(workspace):
    async with session(workspace) as client:
        text = client.instructions
    assert "define_wing" not in text
    for tool in ("naca4", "build_law", "morph_sections", "place_section", ".dat"):
        assert tool in text


# -- fetch_url itself, with a stand-in for the connection ---------------------------------------


class Response:
    """What urllib hands back, reading at most ``step`` bytes a call, as a socket may."""

    def __init__(self, body, step, url):
        self.body, self.step, self.url, self.read_total = body, step, url, 0

    def read(self, size=-1):
        size = len(self.body) if size is None or size < 0 else size
        chunk = self.body[self.read_total:self.read_total + min(size, self.step)]
        self.read_total += len(chunk)
        return chunk

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def stand_in_opener(monkeypatch, response):
    seen = {}

    class Opener:
        def open(self, request, timeout):
            seen.update(url=request.full_url, timeout=timeout)
            return response

    def build_opener(*handlers):
        seen["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(tools_profile.urllib.request, "build_opener", build_opener)
    return seen


def test_fetch_url_reads_on_when_a_read_returns_less_than_asked(monkeypatch):
    body = sd7037_bytes()
    response = Response(body, 7, "https://m-selig.web.engr.illinois.edu/ads/coord/sd7037.dat")
    seen = stand_in_opener(monkeypatch, response)
    final, got = tools_profile.fetch_url(response.url, 3.0, 10_000)
    assert got == body and final == response.url
    assert seen["handlers"] == (tools_profile._StayOnHost,) and seen["timeout"] == 3.0


def test_fetch_url_stops_reading_just_past_its_cap(monkeypatch):
    response = Response(b"0 0\n" * 10_000, 1000, "https://m-selig.web.engr.illinois.edu/x.dat")
    stand_in_opener(monkeypatch, response)
    _, got = tools_profile.fetch_url(response.url, 3.0, 2500)
    assert len(got) == 2501 and response.read_total == 2501
