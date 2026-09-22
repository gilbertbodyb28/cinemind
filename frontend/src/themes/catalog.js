/* The spatial collection: eight themes added on top of Vision UI, Apple and
   Spatial. Each one is a separate stylesheet scoped to its own data-theme, so
   nothing here can reach the three themes that came before it.

   The picker in Sources renders this list; adding a theme means adding a row
   and a stylesheet, nothing else. */

export const SPATIAL_THEMES = [
  {
    id: "spatial-01",
    name: "Cinema Pane",
    blurb: "Warm amber pane on a dark room. Word navigation across the top, the trailer shelf on the left, posters underneath.",
    swatch: "https://image.tmdb.org/t/p/w500/zh6IdheEYinU4TPtorWsjx6qPQE.jpg",
    ring: "rgba(216,178,106,0.7)",
  },
  {
    id: "spatial-02",
    name: "Gallery",
    blurb: "A grey daylit wall with one dark pane hanging on it. Pills float above the pane, the rail floats beside it.",
    swatch: "https://image.tmdb.org/t/p/w500/7NNNXo0qG2SqH4JoG7GPvJ2hzes.jpg",
    ring: "rgba(255,255,255,0.72)",
  },
  {
    id: "spatial-03",
    name: "Screening Room",
    blurb: "A warm lit room, one wide pane, and the navigation gathered into a pill at the bottom. Landscape cards, red accent.",
    swatch: "https://image.tmdb.org/t/p/w500/neeNHeXjMF5fXoCJRsOmkNGC7q.jpg",
    ring: "rgba(229,72,60,0.75)",
  },
  {
    id: "spatial-04",
    name: "Night Lounge",
    blurb: "A dark room after hours. Rail outside the pane, Continue Watching down the right, amber buttons.",
    swatch: "https://image.tmdb.org/t/p/w500/3jDXL4Xvj3AzDOF6UH1xeyHW8MH.jpg",
    ring: "rgba(245,181,60,0.75)",
  },
  {
    id: "spatial-05",
    name: "Studio Grey",
    blurb: "Cool grey glass, a tight left column of trailers, and picture cards that carry their own play button.",
    swatch: "https://image.tmdb.org/t/p/w500/v6f9FUDDQfGIv8MLRQwlL0zvRjI.jpg",
    ring: "rgba(226,232,240,0.75)",
  },
  {
    id: "spatial-06",
    name: "Daylight",
    blurb: "The bright one. White glass in a sunlit room, dark text, and the hero as the only dark object on screen.",
    swatch: "https://image.tmdb.org/t/p/w500/7HR38hMBl23lf38MAN63y4pKsHz.jpg",
    ring: "rgba(120,130,145,0.8)",
  },
  {
    id: "spatial-07",
    name: "Amber Hall",
    blurb: "An autumn room in low orange light. A badge above the rail, a wide hero, and trending titles in a landscape row.",
    swatch: "https://image.tmdb.org/t/p/w500/pnTSOKcYnvdpQNQElAtJM1rWOxH.jpg",
    ring: "rgba(232,93,45,0.78)",
  },
  {
    id: "spatial-08",
    name: "Lamplight",
    blurb: "Warm daylight, no rail at all. Everything reaches from the top row, and the shelf below runs five across.",
    swatch: "https://image.tmdb.org/t/p/w500/747dgDfL5d8esobk7h4odaOFhUq.jpg",
    ring: "rgba(255,255,255,0.75)",
  },
];

export const SPATIAL_THEME_IDS = SPATIAL_THEMES.map((t) => t.id);
