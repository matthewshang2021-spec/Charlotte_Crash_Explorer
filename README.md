# Charlotte Crash Explorer

Charlotte Crash Explorer is a web app that helps people see where crashes have happened in Charlotte, NC. Instead of digging through a spreadsheet of public crash records, users can explore them on a map.



## Search modes

- Start address only: enter the full address, leave destination blank, and press **Get driving route**. The app shows crashes near the start address.
- Start and destination: enter both full addresses and press **Get driving route**. The app draws a driving route and shows crashes near every segment of its full geometry.
- Change the distance slider (0.1–5 km) to adjust proximity without requesting a new route.
- **Clear search / show all locations** returns to all locations matching the record filters.

The left **When** panel contains years, months, weekday, and hours. The right panel contains **Crash Level** and its explanations: 1 Fatality, 2 Serious Injury, 3 Non Serious Injury, 4 Possible Injury, 5 Property Damage, and 6 Unknown. Summary counts stay below the map and count all matching records, before the display sample of at most 10,000 crashes.

The bottom **Crash Data Source** section links to [City of Charlotte Open Data](https://data.charlottenc.gov/datasets/e48e2174b51040dca22e9aec4fd894f6_0/explore).

Address matching uses the U.S. Census Geocoder. Routes use the public FOSSGIS/OSRM driving service with OpenStreetMap road data. Address text is sent to Census; endpoint coordinates are sent to the routing service. Internet is required for those services and the basemap. This public routing service is for light use, not a high-traffic deployment.

Address coordinates and proximity distances are approximate. Distances use a local flat-earth projection for the Charlotte area and measure the shortest distance to route segments, including their endpoints. These are not driving distances from crashes to the route. Route travel time does not include live traffic. Nearby crashes need not be on the same road or grade level. Historical proximity does not establish route safety. Only records within this app's Charlotte-area bounds are available.


Sources:
- https://geocoding.geo.census.gov/geocoder/Geocoding_Services_API.html
- https://project-osrm.org/docs/v26.4.0/http
- https://routing.openstreetmap.de/about.html


Authoring/Development: 

Used ChatGPT and DeepSeek for coding help; architecture, implementation decisions, tests, and final verification are mine.
