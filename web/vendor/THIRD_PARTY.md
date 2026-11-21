# Third-party assets (vendored so the app runs without a build step or CDN)

| File | Source | License |
|---|---|---|
| `deck.gl-9.4.1.min.js` | [deck.gl](https://deck.gl) 9.4.1 (`deck.gl/dist.min.js` from npm) | MIT |
| `../data/region.geojson` | Bangladesh (1:10m) and neighbours (1:50m) from [Natural Earth](https://www.naturalearthdata.com/) via the `world-atlas` npm package, coordinates rounded to 3 decimals | Natural Earth: public domain; world-atlas: ISC |

Fonts (Inter, Space Grotesk, Hind Siliguri, Exo 2) load from Google Fonts (SIL Open Font License); the app falls back to system fonts offline.
