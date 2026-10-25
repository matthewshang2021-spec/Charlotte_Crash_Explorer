import calendar
import json
import ssl
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import numpy as np
import certifi
import pandas as pd
import pydeck as pdk
import streamlit as st


st.set_page_config(page_title="Charlotte Crash Explorer", page_icon="🚗", layout="wide")
st.markdown("""
<style>
.block-container {max-width:1600px; padding-top:2rem; padding-bottom:3rem;}
.dashboard-title {text-align:center; font-size:clamp(2rem,4vw,3rem); font-weight:750;}
.dashboard-subtitle {text-align:center; color:#64748b; margin-bottom:2rem;}
</style>
<h1 class="dashboard-title">Charlotte Crash Explorer</h1>
<p class="dashboard-subtitle">Explore reported Charlotte crashes from 2010–2025.</p>
""", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def load_crashes(csv_path, modified_time):
    required = ["LATITUDE", "LONGITUDE", "DATE_VAL_YEAR", "DATE_VAL_MONTH",
                "DATE_VAL_DAY", "MILT_TIME", "CRSH_LEVL"]
    data = pd.read_csv(csv_path, usecols=required)
    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.loc[data["DATE_VAL_YEAR"].between(2010, 2025)].copy()
    data["CrashDate"] = pd.to_datetime({"year": data["DATE_VAL_YEAR"],
        "month": data["DATE_VAL_MONTH"], "day": data["DATE_VAL_DAY"]}, errors="coerce")
    clock = data["MILT_TIME"]
    valid = clock.between(0, 2359) & (clock % 100 < 60) & (clock % 1 == 0)
    data["Hour"] = (clock // 100).where(valid).astype("Int64")
    data["MapEligible"] = (data["LATITUDE"].between(34.8, 35.6)
                           & data["LONGITUDE"].between(-81.3, -80.3))
    return data


def request_json(url):
    request = Request(url, headers={"User-Agent": "CharlotteCrashExplorer/1.0",
                                    "Accept": "application/json"})
    # Use a maintained CA bundle instead of an outdated default certificate chain.
    # Certificate and hostname verification stay enabled.
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(request, timeout=25, context=context) as response:
            return json.load(response)
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            host = urlparse(url).hostname
            raise ValueError(
                f"HTTPS certificate verification failed for {host}. "
                "Check your computer's date/time, then update certificates with "
                "'python -m pip install --upgrade certifi' and restart the app. "
                "If it persists, the service or your network's certificate may need renewal."
            ) from error
        raise


def geocode_address(address):
    parameters = urlencode({"address": address, "benchmark": "Public_AR_Current", "format": "json"})
    result = request_json("https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?" + parameters)
    matches = result["result"]["addressMatches"]
    if not matches:
        raise ValueError(f"No match for: {address}. Include city, state, and ZIP code.")
    match = matches[0]
    location = {"address": match["matchedAddress"],
                "latitude": float(match["coordinates"]["y"]),
                "longitude": float(match["coordinates"]["x"])}
    if not (34.8 <= location["latitude"] <= 35.6 and -81.3 <= location["longitude"] <= -80.3):
        raise ValueError("Use addresses within this app's Charlotte-area map bounds.")
    return location


def get_driving_route(start, destination):
    # Throttle consecutive requests from this browser session.
    elapsed = time.monotonic() - st.session_state.get("last_route_request", 0)
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    st.session_state.last_route_request = time.monotonic()
    coordinates = (f"{start['longitude']},{start['latitude']};"
                   f"{destination['longitude']},{destination['latitude']}")
    parameters = urlencode({"overview": "full", "geometries": "geojson", "steps": "false"})
    result = request_json("https://routing.openstreetmap.de/routed-car/route/v1/driving/"
                          + coordinates + "?" + parameters)
    if result.get("code") != "Ok" or not result.get("routes"):
        raise ValueError("No driving route was found between these addresses.")
    route = result["routes"][0]
    path = route["geometry"]["coordinates"]
    if len(path) < 2:
        raise ValueError("The routing service returned an incomplete route.")
    return {"path": path, "distance_km": route["distance"] / 1000,
            "duration_minutes": route["duration"] / 60}


def near_path_mask(data, path, radius_km):
    """Distance to every line segment, not just to route vertices.

    Uses a local flat-earth projection suitable for the Charlotte area.
    A single point gives a circular start-address search. Distances are
    approximate straight-line distances, not road-travel distances.
    """
    if data.empty:
        return np.zeros(0, dtype=bool)
    path = np.asarray(path, dtype=float)
    origin_lon, origin_lat = path.mean(axis=0)
    x_scale = 6371.0088 * np.cos(np.radians(origin_lat)) * np.pi / 180
    y_scale = 6371.0088 * np.pi / 180
    px = (data["LONGITUDE"].to_numpy(dtype=float) - origin_lon) * x_scale
    py = (data["LATITUDE"].to_numpy(dtype=float) - origin_lat) * y_scale
    route_xy = (path - [origin_lon, origin_lat]) * [x_scale, y_scale]
    if len(route_xy) == 1:
        return (px - route_xy[0, 0]) ** 2 + (py - route_xy[0, 1]) ** 2 <= radius_km ** 2

    included = np.zeros(len(data), dtype=bool)
    # Reduce work by checking the full route bounding box first.
    candidates = np.flatnonzero(
        (px >= route_xy[:, 0].min() - radius_km)
        & (px <= route_xy[:, 0].max() + radius_km)
        & (py >= route_xy[:, 1].min() - radius_km)
        & (py <= route_xy[:, 1].max() + radius_km))
    x, y = px[candidates], py[candidates]
    hit = np.zeros(len(candidates), dtype=bool)
    for a, b in zip(route_xy[:-1], route_xy[1:]):
        selected = np.flatnonzero(
            ~hit & (x >= min(a[0], b[0]) - radius_km)
            & (x <= max(a[0], b[0]) + radius_km)
            & (y >= min(a[1], b[1]) - radius_km)
            & (y <= max(a[1], b[1]) + radius_km))
        dx, dy = b - a
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            t = np.zeros(len(selected))
        else:
            t = np.clip(((x[selected] - a[0]) * dx
                         + (y[selected] - a[1]) * dy) / length_squared, 0, 1)
        squared_distance = ((x[selected] - a[0] - t * dx) ** 2
                            + (y[selected] - a[1] - t * dy) ** 2)
        hit[selected] = squared_distance <= radius_km ** 2
    included[candidates] = hit
    return included


def code_range_filter(data, label, column):
    codes = sorted(data[column].dropna().unique().tolist())
    if not codes:
        st.caption(f"{label}: no known codes available.")
        return pd.Series(True, index=data.index)
    if len(codes) == 1:
        lower = upper = codes[0]
        st.caption(f"{label}: {codes[0]:g}")
    else:
        lower, upper = st.select_slider(label, options=codes,
            value=(codes[0], codes[-1]), format_func=lambda code: f"{code:g}", key=column)
    matches = data[column].between(lower, upper)
    if st.checkbox(f"Include undocumented {label}", value=True, key=f"undocumented_{column}"):
        matches = matches | data[column].isna()
    return matches


csv_path = Path(__file__).resolve().parent / "Crashes.csv"
try:
    crashes = load_crashes(str(csv_path), csv_path.stat().st_mtime_ns)
except (OSError, ValueError, pd.errors.ParserError) as error:
    st.error(f"Could not load crash data: {error}")
    st.stop()
if crashes.empty:
    st.warning("No records are available for 2010–2025.")
    st.stop()

if "active_search" not in st.session_state:
    st.session_state.active_search = None

# Address inputs remain above the map and filter panels.
with st.container(border=True):
    with st.form("route_search"):
        start_col, destination_col = st.columns(2)
        start_text = start_col.text_input("Start address", placeholder="Street address, Charlotte, NC, ZIP")
        destination_text = destination_col.text_input("Destination address (optional)",
            placeholder="Leave blank to explore near the start address")
        submitted = st.form_submit_button("Get driving route", type="primary")
    st.caption("Start only: nearby crashes. Start + destination: crashes near the whole driving route. "
               "Addresses are sent to the U.S. Census Geocoder; route coordinates are sent to FOSSGIS/OSRM.")
    radius_km = st.slider("Distance from address or route (kilometers)",
                          min_value=0.1, max_value=5.0, value=1.0, step=0.1)
    cleared = st.button("Clear search / show all locations")
    if cleared:
        st.session_state.active_search = None
    if submitted:
        st.session_state.active_search = None
        if not start_text.strip():
            st.warning("Enter a start address. The destination is optional.")
        else:
            try:
                with st.spinner("Finding your address and route..."):
                    start = geocode_address(start_text.strip())
                    destination = geocode_address(destination_text.strip()) if destination_text.strip() else None
                    route = get_driving_route(start, destination) if destination else None
                    st.session_state.active_search = {"start": start, "destination": destination, "route": route}
            except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
                st.error(f"Search could not be completed: {error}")
    search = st.session_state.active_search
    if search:
        st.write(f"**Matched start:** {search['start']['address']}")
        if search["destination"]:
            st.write(f"**Matched destination:** {search['destination']['address']}")
            route = search["route"]
            st.info(f"Driving route: {route['distance_km']:.1f} km · "
                    f"Estimated time: {route['duration_minutes']:.0f} minutes. "
                    "This estimate does not include live traffic.")
        st.caption("Address locations are approximate. Change the distance slider without searching again.")

left_filters, map_column, right_filters = st.columns([1, 2.5, 1], gap="large")
with left_filters:
    with st.container(border=True):
        st.subheader("When")
        years = sorted(crashes["DATE_VAL_YEAR"].dropna().astype(int).unique().tolist())
        if len(years) == 1:
            start_year = end_year = years[0]
            st.caption(f"Year: {start_year}")
        else:
            start_year, end_year = st.select_slider("Years", options=years, value=(years[0], years[-1]))
        start_month, end_month = st.select_slider("Months", options=list(range(1, 13)),
            value=(1, 12), format_func=lambda month: calendar.month_name[month])
        weekdays = list(calendar.day_name)
        weekday = st.selectbox("Weekday", ["All weekdays"] + weekdays)
        selected_days = list(range(7)) if weekday == "All weekdays" else [weekdays.index(weekday)]
        start_hour, end_hour = st.slider("Hours of day", 0, 23, (0, 23))
        unknown_times = st.checkbox("Include unknown times", value=True)
        st.caption(f"Known times: {start_hour:02d}:00 through {end_hour:02d}:59.")
with right_filters:
    with st.container(border=True):
        st.subheader("Crash details")
        severity_matches = code_range_filter(crashes, "Crash Level", "CRSH_LEVL")
        st.markdown("""
**Crash Level explanations**

- **1:** Fatality
- **2:** Serious Injury
- **3:** Non Serious Injury
- **4:** Possible Injury
- **5:** Property Damage
- **6:** Unknown
""")
        st.caption("All filters apply together.")

hour_matches = crashes["Hour"].between(start_hour, end_hour).fillna(False)
if unknown_times:
    hour_matches |= crashes["Hour"].isna()
filtered_crashes = crashes.loc[
    crashes["DATE_VAL_YEAR"].between(start_year, end_year)
    & crashes["DATE_VAL_MONTH"].between(start_month, end_month)
    & crashes["CrashDate"].dt.dayofweek.isin(selected_days)
    & hour_matches & severity_matches].copy()
mapped_crashes = filtered_crashes.loc[filtered_crashes["MapEligible"]].copy()

if search:
    start = search["start"]
    path = search["route"]["path"] if search["route"] else [[start["longitude"], start["latitude"]]]
    # Select from ALL usable records before sampling for the map.
    mapped_crashes = mapped_crashes.loc[near_path_mask(mapped_crashes, path, radius_km)].copy()

with map_column:
    title = "Reported crash locations"
    if search:
        title = "Crashes near your route" if search["route"] else "Crashes near your start address"
    st.markdown(f'<h3 style="text-align:center">{title}</h3>', unsafe_allow_html=True)
    if mapped_crashes.empty:
        st.info("No mapped crashes match this selection. Broaden the filters or search distance.")
    if search or not mapped_crashes.empty:
        points = mapped_crashes.sample(n=min(10_000, len(mapped_crashes)), random_state=42).copy()
        points["label"] = "Reported crash"
        points["detail"] = points["CrashDate"].dt.strftime("%Y-%m-%d")
        layers = []
        if search and search["route"]:
            layers.append(pdk.Layer("PathLayer", data=[{"path": path}], get_path="path",
                get_color=[37, 99, 235], get_width=5,
                width_units=pdk.types.String("pixels"),
                width_min_pixels=4, width_max_pixels=6, pickable=False))
        layers.append(pdk.Layer("ScatterplotLayer",
            data=points[["LATITUDE", "LONGITUDE", "label", "detail"]],
            get_position="[LONGITUDE, LATITUDE]", get_fill_color=[249, 115, 22, 180],
            get_radius=10, radius_min_pixels=3, radius_max_pixels=8, pickable=True))
        if search:
            markers = []
            for name, place, color in [("Start", search["start"], [22, 163, 74]),
                                        ("Destination", search["destination"], [147, 51, 234])]:
                if place:
                    markers.append({"position": [place["longitude"], place["latitude"]],
                                    "label": name, "detail": place["address"], "color": color})
            layers.append(pdk.Layer("ScatterplotLayer", data=markers, get_position="position",
                get_fill_color="color", get_line_color=[255, 255, 255], stroked=True,
                line_width_min_pixels=2, get_radius=20, radius_min_pixels=10, pickable=True))
            # Fit the full route, its selected distance, and address markers.
            bounds = np.asarray(path + [marker["position"] for marker in markers], dtype=float)
            center = bounds.mean(axis=0)
            lat_pad = radius_km / 111.195
            lon_pad = lat_pad / np.cos(np.radians(center[1]))
            minimum, maximum = bounds.min(axis=0), bounds.max(axis=0)
            view = pdk.data_utils.compute_view([
                [minimum[0] - lon_pad, minimum[1] - lat_pad],
                [maximum[0] + lon_pad, maximum[1] + lat_pad]], view_proportion=0.8)
            view.zoom = max(5, min(16, view.zoom - 0.5))
        else:
            view = pdk.ViewState(latitude=35.2271, longitude=-80.8431, zoom=10)
        deck = pdk.Deck(layers=layers, initial_view_state=view, map_style=None,
                        tooltip={"text": "{label}\n{detail}"})
        st.pydeck_chart(deck, width="stretch", height=600)
        st.caption("Orange: crashes · Green: start · Purple: destination · Blue line: driving route")
        st.caption(f"Showing {len(points):,} sampled locations from {len(mapped_crashes):,} matching mapped records.")
        if search:
            subject = "any segment of the full route" if search["route"] else "the start address"
            st.caption(f"Crashes are within approximately {radius_km:.1f} km of {subject}. "
                       "Distance is straight-line proximity, not driving distance.")
        st.caption("Routing: OSRM / FOSSGIS · Road data: [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright) · "
                   "[Fix the map](https://www.openstreetmap.org/fixthemap)")

st.divider()
left, middle, right = st.columns(3)
left.metric("Records matching time/level filters", f"{len(filtered_crashes):,}")
middle.metric("Mapped crashes near route/address" if search else "Usable map locations", f"{len(mapped_crashes):,}")
right.metric("Unknown times in filtered records", f"{filtered_crashes['Hour'].isna().sum():,}")
st.caption("Invalid dates are excluded because their weekday cannot be determined. "
           "Counts use all matching records before display sampling. "
           "Crash proximity does not establish that a route is safer or more dangerous.")
with st.expander("About the loaded data"):
    st.write(f"Total records, 2010–2025: {len(crashes):,}")
    st.write(f"Records excluded from mapping: {(~crashes['MapEligible']).sum():,}")
    st.write(f"Invalid dates: {crashes['CrashDate'].isna().sum():,}")
    st.write(f"Invalid times: {crashes['Hour'].isna().sum():,}")
    st.caption("Address search, route calculation, and the map background require internet. "
               "Crash records are limited to the Charlotte-area bounds used by this app.")

st.divider()
st.subheader("Crash Data Source")
st.markdown(
    "Crash data: [City of Charlotte Open Data]"
    "(https://data.charlottenc.gov/datasets/e48e2174b51040dca22e9aec4fd894f6_0/explore)."
)