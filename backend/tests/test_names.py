import numpy as np
import pytest
from shapely.geometry import LineString, Point

from backend.cai.graph import get_net
from backend.cai.names import Namer, translit


@pytest.mark.parametrize("bg,latin", [
    ("бул. Ботевградско шосе", "Blvd Botevgradsko shose"),
    ("Цариградско шосе", "Tsarigradsko shose"),
    ("бул. България", "Blvd Bulgaria"),  # official exception to ъ -> a
    ("Мими Балканска", "Mimi Balkanska"),
    ("ул. Житница", "St Zhitnitsa"),
    ("Околовръстен път", "Okolovrasten pat"),
    ("Щерьо Влайков", "Shteryo Vlaykov"),
])
def test_streamlined_transliteration(bg, latin):
    assert translit(bg) == latin


def test_translit_none_and_latin_passthrough():
    assert translit(None) is None and translit("") == ""
    assert translit("Main St 5") == "Main St 5"


@pytest.fixture(scope="module")
def namer():
    from backend import main
    return Namer(get_net(), main.district_at)


def test_unnamed_segments_borrow_the_nearest_named_street(namer):
    net = get_net()
    unnamed = np.flatnonzero(net.e_name < 0)[:2000]
    labelled = [namer.edge(int(e)) for e in unnamed[::20]]
    near = [x for x in labelled if x["near_street"]]
    assert near and all(x["street_bg"] and x["street"] for x in near)  # latin + cyrillic both present
    assert all(not x["near_street"] for x in (namer.edge(int(e)) for e in np.flatnonzero(net.e_name >= 0)[:50]))


def test_point_label_names_street_and_district(namer):
    label = namer.point(42.6934, 23.3331)  # next to Sofia University
    assert label.startswith("near ") and "Sredets" in label or "Oborishte" in label or "Vazrazhdane" in label
    far = namer.point(42.70, 23.32)
    assert isinstance(far, str) and far
    assert namer.point(43.5, 23.3) == "43.5000, 23.3000"  # outside everything: coordinates, not a made-up place


def test_district_names_are_standard_english():
    from backend import main
    names = {d["name"] for d in main.DISTRICTS}
    assert {"Nadezhda", "Kremikovtsi", "Sredets", "Vazrazhdane", "Ovcha Kupel", "Krasna Polyana"} <= names
    assert not names & {"Nadejda", "Kremikovci", "Sredec", "Vazrajdane"}


def test_chokepoint_marker_is_on_its_street(namer):
    """The marker used to be an average of positions, which can fall off a long road."""
    from backend.cai.network import compute_network
    from backend.cai.features import compute_features
    from backend.cai.routing import Router
    from backend.cai.score import CaiModel

    net = get_net()
    feats = compute_features(net)
    hours = [{"time": f"2026-01-15T{h:02d}:00", "score": 80.0, "pm25": 60.0, "apparent": -4.0, "radiation": 0.0, "t2m": -3.0} for h in range(24)]
    model = CaiModel(net, feats, hours, 18, [])
    h = model.hour(18)
    out = compute_network(net, Router(net), h["cr"], h["pm"], h["cai"], feats)
    for c in out["chokepoints"]:
        d = min(Point(c["lon"], c["lat"]).distance(LineString(seg)) for seg in c["segments"] if len(seg) > 1)
        assert d * 111_000 < 3, f"{c['street']} marker is {d * 111_000:.0f} m off its street"
