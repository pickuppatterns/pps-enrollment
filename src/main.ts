import * as maplibregl from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import "maplibre-gl/dist/maplibre-gl.css";
import { csvParse } from "d3-dsv";
import "./style.css";

maplibregl.setWorkerUrl(workerUrl);

type Cam = {
  lng: number;
  lat: number;
  zoom: number;
  pitch: number;
  bearing: number;
};
type Point = { label: string; year: number; value: number | null };
type Stop = Cam & {
  id: string;
  name: string;
  description: string;
  order: number;
  series: Point[];
};

const OVERVIEW: Cam = {
  lng: -122.65,
  lat: 45.53,
  zoom: 11,
  pitch: 0,
  bearing: 0,
};
const ORBIT = 30; // degrees the camera turns while the years count down

// Share of each stop's scroll given to each animation (0 to 1)
const PHASES = {
  fly: [0, 0.3],
  name: [0.22, 0.32],
  desc: [0.32, 0.5],
  count: [0.5, 0.9], // 0.9 to 1.0 holds the final year on screen
} as const;

const num = (s: string | undefined, d: number) =>
  s && s.trim() !== "" ? Number(s) : d;
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const ease = (t: number) =>
  t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
const phase = (p: number, [a, b]: readonly [number, number]) =>
  Math.min(1, Math.max(0, (p - a) / (b - a)));

// Write-on: the full text sits in place but hidden, so lines don't
// reflow while letters appear
function writer(el: HTMLElement) {
  const shown = document.createElement("span");
  const ghost = document.createElement("span");
  ghost.className = "ghost";
  el.replaceChildren(shown, ghost);
  return (text: string, t: number) => {
    const n = Math.round(t * text.length);
    shown.textContent = text.slice(0, n);
    ghost.textContent = text.slice(n);
  };
}

// Aerial photos under the map's text labels
function addImagery(map: maplibregl.Map) {
  map.addSource("imagery", {
    type: "raster",
    tiles: [
      "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
    ],
    tileSize: 256,
    maxzoom: 16,
    attribution: "Imagery: USGS The National Map",
  });

  const layers = map.getStyle().layers;
  map.addLayer(
    { id: "imagery", type: "raster", source: "imagery" },
    layers[0].id,
  );

  // Keep only text and shields; hide the illustrated fills, roads and buildings
  for (const l of layers) {
    if (l.type !== "symbol") map.setLayoutProperty(l.id, "visibility", "none");
  }
}

async function load(): Promise<Stop[]> {
  const [schools, enrollment] = await Promise.all([
    fetch("/data/schools.csv")
      .then((r) => r.text())
      .then((t) => csvParse(t)),
    fetch("/data/enrollment.csv")
      .then((r) => r.text())
      .then((t) => csvParse(t)),
  ]);

  const byId = new Map<string, Point[]>();
  for (const r of enrollment) {
    const list = byId.get(r.school_id!) ?? [];
    list.push({
      label: r.school_year!,
      year: +r.year!,
      value: r.enrollment ? +r.enrollment : null,
    });
    byId.set(r.school_id!, list);
  }

  return schools
    .filter((s) => s.description?.trim() && s.lat && s.lon)
    .map((s) => ({
      id: s.school_id!,
      name: s.name!,
      lng: +s.lon!,
      lat: +s.lat!,
      zoom: num(s.zoom, 16),
      pitch: num(s.pitch, 60),
      bearing: num(s.bearing, 0),
      order: num(s.order, Infinity),
      description: s.description!.trim(),
      series: (byId.get(s.school_id!) ?? []).sort((a, b) => b.year - a.year), // newest first
    }))
    .sort((a, b) => a.order - b.order);
}

async function main() {
  const stops = await load();
  if (!stops.length) {
    console.warn(
      "No stops: add a description to at least one school with coordinates.",
    );
    return;
  }

  const map = new maplibregl.Map({
    container: "map",
    style: "https://tiles.openfreemap.org/styles/liberty",
    center: [OVERVIEW.lng, OVERVIEW.lat],
    zoom: OVERVIEW.zoom,
    interactive: false,
    attributionControl: { compact: true },
  });

  // The style references some icons it doesn't ship; give them a blank placeholder
  map.on("styleimagemissing", (e) => {
    if (!map.hasImage(e.id))
      map.addImage(e.id, { width: 1, height: 1, data: new Uint8Array(4) });
  });

  const nameEl = document.getElementById("name")!;
  const descEl = document.getElementById("desc")!;
  const writeName = writer(nameEl);
  const writeDesc = writer(descEl);

  const stepsEl = document.getElementById("steps")!;
  stops.forEach(() => {
    const d = document.createElement("div");
    d.className = "step";
    stepsEl.appendChild(d);
  });

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.innerHTML = `<div class="b-count"></div><div class="b-year"></div>`;
  const countEl = bubble.querySelector<HTMLElement>(".b-count")!;
  const yearEl = bubble.querySelector<HTMLElement>(".b-year")!;
  const marker = new maplibregl.Marker({
    element: bubble,
    anchor: "bottom",
    offset: [0, -16],
  })
    .setLngLat([stops[0].lng, stops[0].lat])
    .addTo(map);

  function render(i: number, p: number) {
    const s = stops[i];
    const prev: Cam =
      i === 0
        ? OVERVIEW
        : { ...stops[i - 1], bearing: stops[i - 1].bearing + ORBIT };
    const fly = ease(phase(p, PHASES.fly));
    const count = phase(p, PHASES.count);

    map.jumpTo({
      center: [lerp(prev.lng, s.lng, fly), lerp(prev.lat, s.lat, fly)],
      zoom: lerp(prev.zoom, s.zoom, fly) - 2.5 * Math.sin(Math.PI * fly), // arc up and over
      pitch: lerp(prev.pitch, s.pitch, fly),
      bearing: lerp(prev.bearing, s.bearing, fly) + count * ORBIT,
    });

    // Name, then description, each written on over a fixed slice of scroll
    writeName(s.name, phase(p, PHASES.name));
    writeDesc(s.description, phase(p, PHASES.desc));

    // Bubble: current year -> 15 years back as you scroll down, reverse on the way up
    marker.setLngLat([s.lng, s.lat]);
    bubble.classList.toggle(
      "visible",
      p >= PHASES.desc[0] && s.series.length > 0,
    );
    if (!s.series.length) return;
    const n = s.series.length - 1;
    const pos = count * n;
    const k = Math.min(n, Math.floor(pos));
    const a = s.series[k];
    const b = s.series[Math.min(n, k + 1)];
    const v =
      a.value == null
        ? null
        : b.value == null
          ? a.value
          : Math.round(lerp(a.value, b.value, pos - k));
    countEl.textContent = v == null ? "Not open" : v.toLocaleString();
    yearEl.textContent = s.series[Math.round(pos)].label;
  }

  // Progress across the whole pinned section, so the last stop reaches 100%
  // exactly when the map un-pins
  const scrollyEl = document.getElementById("scrolly")!;
  let queued = false;
  function onScroll() {
    queued = false;
    const r = scrollyEl.getBoundingClientRect();
    const pinned = r.height - window.innerHeight;
    const g = Math.min(1, Math.max(0, -r.top / pinned)) * stops.length;
    const i = Math.min(stops.length - 1, Math.floor(g));
    render(i, g - i);
  }
  const schedule = () => {
    if (!queued) {
      queued = true;
      requestAnimationFrame(onScroll);
    }
  };

  map.on("load", () => {
    addImagery(map);
    onScroll();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
  });
}

main();
