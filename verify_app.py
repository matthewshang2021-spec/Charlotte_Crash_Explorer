"""Offline checks: route geometry and Streamlit flows with mocked web services."""
import ast
import io
import json
import shutil
import ssl
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pydeck as pdk
from streamlit.testing.v1 import AppTest

APP = Path(__file__).with_name("app.py")
namespace = {"np": np}
tree = ast.parse(APP.read_text(encoding="utf-8"))
function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "near_path_mask")
exec(compile(ast.Module(body=[function], type_ignores=[]), str(APP), "exec"), namespace)
near_path_mask = namespace["near_path_mask"]

points = pd.DataFrame({"LONGITUDE": [-80.84, -80.82, -80.80, -80.82, -80.85],
                       "LATITUDE": [35.22, 35.22, 35.22, 35.25, 35.22]})
path = [[-80.84, 35.22], [-80.80, 35.22]]
# Check the actual route layer's wire format: units must be a literal,
# not Pydeck's @@= expression syntax, and pixel width must be bounded.
route_call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == "Layer"
                  and node.args and isinstance(node.args[0], ast.Constant)
                  and node.args[0].value == "PathLayer")
route_layer = eval(compile(ast.Expression(route_call), str(APP), "eval"), {"pdk": pdk, "path": path})
route_json = json.loads(route_layer.to_json())
assert route_json["widthUnits"] == "pixels"
assert route_json["widthMinPixels"] == 4
assert route_json["widthMaxPixels"] == 6
assert near_path_mask(points, path, 0.1).tolist() == [True, True, True, False, False]
assert near_path_mask(points, [path[0]], 0.1).tolist() == [True, False, False, False, False]
assert near_path_mask(points.iloc[:0], path, 1).size == 0
assert np.array_equal(near_path_mask(points, [path[0], path[0], path[1]], 0.1),
                      near_path_mask(points, path, 0.1))
# Bend: points near the nonexistent diagonal must not be included.
bend = [[-80.84, 35.22], [-80.80, 35.22], [-80.80, 35.26]]
diagonal = pd.DataFrame({"LONGITUDE": [-80.82], "LATITUDE": [35.24]})
assert not near_path_mask(diagonal, bend, 0.1)[0]

calls = []
def fake_urlopen(request, timeout, context):
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    url = request.full_url
    calls.append(url)
    if "onelineaddress" in url:
        address = parse_qs(urlparse(url).query)["address"][0]
        if address == "Missing":
            result = {"result": {"addressMatches": []}}
        else:
            longitude = -80.80 if address == "Destination" else -80.84
            result = {"result": {"addressMatches": [{"matchedAddress": address,
                        "coordinates": {"x": longitude, "y": 35.22}}]}}
    else:
        result = {"code": "Ok", "routes": [{"geometry": {"coordinates": path},
                                             "distance": 4000, "duration": 600}]}
    return io.BytesIO(json.dumps(result).encode())

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    shutil.copy2(APP, root / "app.py")
    fixture = pd.DataFrame({"LATITUDE": [35.22, 35.22, 35.22, 35.25],
                           "LONGITUDE": [-80.84, -80.8405, -80.82, -80.82],
                           "DATE_VAL_YEAR": [2020] * 4, "DATE_VAL_MONTH": [1] * 4,
                           "DATE_VAL_DAY": [1] * 4, "MILT_TIME": [930] * 4,
                           "CRSH_LEVL": [1, 2, 1, 2]})
    fixture.to_csv(root / "Crashes.csv", index=False)
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        app = AppTest.from_file(str(root / "app.py"), default_timeout=30).run()
        assert not app.exception, app.exception
        assert app.metric[1].value == "4"
        app.text_input[0].set_value("Start")
        app.button[0].click().run()
        assert not app.exception, app.exception
        assert app.metric[1].value == "2"
        assert app.session_state["active_search"]["route"] is None
        app.text_input[1].set_value("Destination")
        app.button[0].click().run()
        assert not app.exception, app.exception
        assert app.metric[1].value == "3", app.metric[1].value
        assert app.session_state["active_search"]["route"]["distance_km"] == 4
        previous_calls = len(calls)
        app.slider[0].set_value(5.0).run()
        assert not app.exception, app.exception
        assert app.metric[1].value == "4"
        assert len(calls) == previous_calls
        app.selectbox[0].select("Monday").run()
        assert not app.exception, app.exception
        assert app.metric[1].value == "0"
        app.selectbox[0].select("All weekdays").run()
        app.text_input[0].set_value("Missing")
        app.button[0].click().run()
        assert not app.exception, app.exception
        assert app.session_state["active_search"] is None
        assert len(app.error) == 1
        app.button[1].click().run()
        assert not app.exception, app.exception
        assert app.metric[1].value == "4"
print("PASS: whole-segment distance, bend, duplicate segment, empty data, start-only search,")
print("route search, radius changes without web calls, weekday filter, failed search, and clear.")
