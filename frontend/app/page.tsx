"use client";
import { useEffect, useRef, useState } from "react";
type PageInfo = {
  preview: string;
  preview_width: number;
  preview_height: number;
  mode: string;
  dpi: number[];
  icc: { name: string } | null;
};
type Asset = {
  id: string;
  name: string;
  sha256: string;
  warnings: string[];
  pages: PageInfo[];
};
type Finding = {
  id: string;
  kind: string;
  title: string;
  severity: string;
  bbox: number[] | null;
  mean_delta_e?: number;
  recommendation: string;
  evidence?: unknown;
};
type Result = {
  id: string;
  status: string;
  findings: Finding[];
  metrics: {
    changed_area_percent: number;
    mean_delta_e: number;
    processing_seconds: number;
  };
  registration: { confidence: number; method: string };
  text: { available: boolean; changes: unknown[] };
  barcode: { status?: string };
  geometry: { check: string; status: string }[];
  warnings: string[];
  ai: { available: boolean; text?: string; reason?: string };
  color_samples: unknown[];
};
type Config = {
  delta_e: number;
  min_area: number;
  fail_area_percent: number;
  min_registration: number;
  trim: number[] | null;
  bleed_px: number;
  safe_px: number;
  color_regions: number[][];
  ocr: boolean;
  barcode: boolean;
  ai: boolean;
};
type History = {
  id: string;
  state: string;
  status: string | null;
  created: string;
};
const initial: Config = {
  delta_e: 6,
  min_area: 45,
  fail_area_percent: 8,
  min_registration: 0.55,
  trim: null,
  bleed_px: 12,
  safe_px: 24,
  color_regions: [],
  ocr: true,
  barcode: true,
  ai: false,
};
async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const r = await fetch(url, options);
  if (!r.ok) {
    let detail;
    try {
      detail = (await r.json()).detail;
    } catch {
      detail = r.statusText;
    }
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail),
    );
  }
  return r.json();
}
function Eye() {
  return (
    <svg viewBox="0 0 72 48" aria-hidden="true">
      <path
        d="M3 24Q36-14 69 24Q36 62 3 24Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="5"
      />
      <circle cx="36" cy="24" r="12" fill="currentColor" />
      <path
        d="M36 0v12M36 36v12M0 24h18M54 24h18"
        stroke="currentColor"
        strokeWidth="3"
      />
    </svg>
  );
}
function Viewer({
  reference,
  candidate,
  result,
  selected,
  onSelect,
  config,
}: {
  reference: string | null;
  candidate: string | null;
  result: Result | null;
  selected: string | null;
  onSelect: (id: string) => void;
  config: Config;
}) {
  const canvas = useRef<HTMLCanvasElement>(null),
    host = useRef<HTMLDivElement>(null),
    drag = useRef<{ x: number; y: number; px: number; py: number } | null>(
      null,
    );
  const [mode, setMode] = useState("split"),
    [split, setSplit] = useState(50),
    [zoom, setZoom] = useState(1),
    [pan, setPan] = useState({ x: 0, y: 0 }),
    [boxes, setBoxes] = useState(true),
    [guides, setGuides] = useState(true),
    [images, setImages] = useState<Record<string, HTMLImageElement>>({});
  const base = result ? `/api/inspections/${result.id}/files/` : null;
  useEffect(() => {
    let alive = true;
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setImages({});
    const sources: Record<string, string | null> = {
      reference: reference,
      candidate: base ? base + "aligned.png" : candidate,
      overlay: base ? base + "overlay.png" : null,
      heatmap: base ? base + "heatmap.png" : null,
    };
    for (const [key, src] of Object.entries(sources)) {
      if (!src) continue;
      const img = new Image();
      img.onload = () => {
        if (alive) setImages((old) => ({ ...old, [key]: img }));
      };
      img.src = src;
    }
    return () => {
      alive = false;
    };
  }, [reference, candidate, base]);
  useEffect(() => {
    const el = canvas.current,
      frame = host.current;
    if (!el || !frame) return;
    function paint() {
      const c = el!,
        parent = frame!,
        ctx = c.getContext("2d")!;
      const dpr = devicePixelRatio || 1;
      const width = parent.clientWidth,
        height = parent.clientHeight;
      c.width = width * dpr;
      c.height = height * dpr;
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, width, height);
      const a = images.reference,
        b = images.candidate;
      if (!a) return;
      const scale =
        Math.min((width - 64) / a.width, (height - 48) / a.height) * zoom;
      const x = (width - a.width * scale) / 2 + pan.x,
        y = (height - a.height * scale) / 2 + pan.y;
      ctx.save();
      ctx.translate(x, y);
      ctx.scale(scale, scale);
      if (mode === "split" && b) {
        ctx.drawImage(a, 0, 0, a.width, a.height);
        ctx.save();
        ctx.beginPath();
        ctx.rect((a.width * split) / 100, 0, a.width, a.height);
        ctx.clip();
        ctx.drawImage(b, 0, 0, a.width, a.height);
        ctx.restore();
        ctx.strokeStyle = "#F4E7C8";
        ctx.lineWidth = 2 / scale;
        ctx.beginPath();
        ctx.moveTo((a.width * split) / 100, 0);
        ctx.lineTo((a.width * split) / 100, a.height);
        ctx.stroke();
      } else {
        const img = images[mode] || a;
        ctx.drawImage(img, 0, 0, a.width, a.height);
      }
      if (guides) {
        ctx.lineWidth = 1 / scale;
        ctx.strokeStyle = "#59B5AE";
        ctx.setLineDash([8 / scale, 5 / scale]);
        const trim = config.trim || [30, 30, a.width - 30, a.height - 30];
        ctx.strokeRect(trim[0], trim[1], trim[2] - trim[0], trim[3] - trim[1]);
        if (config.trim) {
          const s = config.safe_px;
          ctx.strokeStyle = "#E8B83D";
          ctx.strokeRect(
            trim[0] + s,
            trim[1] + s,
            trim[2] - trim[0] - 2 * s,
            trim[3] - trim[1] - 2 * s,
          );
        }
        ctx.setLineDash([]);
      }
      if (boxes && result) {
        for (const f of result.findings) {
          if (!f.bbox) continue;
          const [l, t, r, b] = f.bbox;
          ctx.lineWidth = (f.id === selected ? 4 : 1.5) / scale;
          ctx.strokeStyle = f.id === selected ? "#E8B83D" : "#F47B36";
          ctx.strokeRect(l, t, r - l, b - t);
          if (f.id === selected) {
            ctx.fillStyle = "#E8B83D";
            ctx.font = `${14 / scale}px monospace`;
            ctx.fillText(f.id, l, t - 5 / scale);
          }
        }
      }
      ctx.restore();
    }
    paint();
    const obs = new ResizeObserver(paint);
    obs.observe(frame);
    return () => obs.disconnect();
  }, [images, mode, split, zoom, pan, boxes, guides, result, selected, config]);
  return (
    <section className="chamber">
      <div className="chamber-head">
        <span>ARTWORK COMPARISON</span>
        <span className="live-dot">
          {reference ? "SIGNAL ACQUIRED" : "AWAITING INPUT"}
        </span>
      </div>
      <div className="view-tabs" role="group" aria-label="View mode">
        {["reference", "candidate", "split", "overlay", "heatmap"].map((m) => (
          <button
            key={m}
            aria-pressed={mode === m}
            onClick={() => setMode(m)}
            disabled={["overlay", "heatmap"].includes(m) && !result}
          >
            {m === "split" ? "A / B" : m}
          </button>
        ))}
      </div>
      <div className="screen" ref={host}>
        <span className="screen-label left">A · APPROVED MASTER</span>
        <span className="screen-label right">B · PRODUCTION PROOF</span>
        <canvas
          ref={canvas}
          aria-label="Artwork inspection viewer. Use finding list to select evidence."
          onPointerDown={(e) => {
            drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (drag.current)
              setPan({
                x: drag.current.px + e.clientX - drag.current.x,
                y: drag.current.py + e.clientY - drag.current.y,
              });
          }}
          onPointerUp={(e) => {
            if (
              drag.current &&
              Math.abs(e.clientX - drag.current.x) < 3 &&
              Math.abs(e.clientY - drag.current.y) < 3 &&
              images.reference &&
              host.current
            ) {
              const a = images.reference,
                frame = host.current,
                rect = frame.getBoundingClientRect(),
                scale =
                  Math.min(
                    (frame.clientWidth - 64) / a.width,
                    (frame.clientHeight - 48) / a.height,
                  ) * zoom;
              const x =
                  (e.clientX -
                    rect.left -
                    (frame.clientWidth - a.width * scale) / 2 -
                    pan.x) /
                  scale,
                y =
                  (e.clientY -
                    rect.top -
                    (frame.clientHeight - a.height * scale) / 2 -
                    pan.y) /
                  scale;
              const f = result?.findings.find(
                (f) =>
                  f.bbox &&
                  x >= f.bbox[0] &&
                  x <= f.bbox[2] &&
                  y >= f.bbox[1] &&
                  y <= f.bbox[3],
              );
              if (f) onSelect(f.id);
            }
            drag.current = null;
          }}
        />
        {!reference && (
          <div className="empty-screen">
            <div className="scope">
              <Eye />
            </div>
            <h2>
              Let’s take
              <br />a closer look.
            </h2>
            <p>
              LOAD A MASTER + PROOF
              <br />
              OR SELECT A SAMPLE BELOW
            </p>
            <span className="crosshair">＋</span>
          </div>
        )}
        <span className="screen-corner">PC–78 / {Math.round(zoom * 100)}%</span>
      </div>
      <div className="viewer-tools">
        <div className="zoom">
          <button
            aria-label="Zoom out"
            onClick={() => setZoom(Math.max(0.5, zoom - 0.25))}
          >
            −
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button
            aria-label="Zoom in"
            onClick={() => setZoom(Math.min(5, zoom + 0.25))}
          >
            +
          </button>
          <button
            onClick={() => {
              setZoom(1);
              setPan({ x: 0, y: 0 });
            }}
          >
            FIT
          </button>
        </div>
        <label>
          <input
            type="checkbox"
            checked={boxes}
            onChange={(e) => setBoxes(e.target.checked)}
          />{" "}
          FINDINGS
        </label>
        <label>
          <input
            type="checkbox"
            checked={guides}
            onChange={(e) => setGuides(e.target.checked)}
          />{" "}
          GUIDES
        </label>
      </div>
      {mode === "split" && (
        <label className="split-control">
          MASTER{" "}
          <input
            aria-label="Before after comparison"
            type="range"
            min="0"
            max="100"
            value={split}
            onChange={(e) => setSplit(+e.target.value)}
          />{" "}
          PROOF
        </label>
      )}
      <div className="chamber-foot">
        <span>DRAG TO PAN · SELECT A FINDING TO LOCATE</span>
        <span>RGB / NORMALIZED PREVIEW</span>
      </div>
    </section>
  );
}
export default function Home() {
  const [reference, setReference] = useState<Asset | null>(null),
    [candidate, setCandidate] = useState<Asset | null>(null),
    [rp, setRp] = useState(0),
    [cp, setCp] = useState(0),
    [config, setConfig] = useState<Config>(initial),
    [result, setResult] = useState<Result | null>(null),
    [job, setJob] = useState<string | null>(null),
    [state, setState] = useState("idle"),
    [error, setError] = useState(""),
    [selected, setSelected] = useState<string | null>(null),
    [health, setHealth] = useState<{ status: string; ocr: boolean } | null>(
      null,
    ),
    [history, setHistory] = useState<History[]>([]),
    [busy, setBusy] = useState(false),
    [clean, setClean] = useState(false),
    [trim, setTrim] = useState(""),
    [regions, setRegions] = useState("");
  const refresh = () =>
    api<History[]>("/api/inspections")
      .then(setHistory)
      .catch(() => {});
  useEffect(() => {
    api<{ status: string; ocr: boolean }>("/api/health")
      .then(setHealth)
      .catch(() =>
        setError("Backend offline. Start the inspection API on port 8000."),
      );
    refresh();
    const id = localStorage.getItem("presscheck-last");
    if (id) setJob(id);
  }, []);
  useEffect(() => {
    if (!job) return;
    let cancel = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const data = await api<{
          state: string;
          result: Result | null;
          error: string;
          request: {
            reference: string;
            candidate: string;
            reference_page: number;
            candidate_page: number;
            config: Config;
          };
        }>(`/api/inspections/${job}`);
        if (cancel) return;
        setState(data.state);
        if (data.state === "complete" && data.result) {
          setResult(data.result);
          setSelected(data.result.findings[0]?.id || null);
          const req = data.request;
          const [a, b] = await Promise.all([
            api<Asset>(`/api/assets/${req.reference}`),
            api<Asset>(`/api/assets/${req.candidate}`),
          ]);
          if (cancel) return;
          setReference(a);
          setCandidate(b);
          setRp(req.reference_page);
          setCp(req.candidate_page);
          setConfig(req.config);
          setTrim(req.config.trim?.join(", ") || "");
          setRegions(
            req.config.color_regions.map((r) => r.join(",")).join(";"),
          );
          refresh();
        } else if (data.state === "error") setError(data.error);
        else timer = setTimeout(poll, 700);
      } catch (e) {
        if (!cancel) {
          setError((e as Error).message);
          setState("error");
        }
      }
    };
    poll();
    return () => {
      cancel = true;
      clearTimeout(timer);
    };
  }, [job]);
  const processing = ["queued", "running"].includes(state);
  const reset = () => {
    setJob(null);
    setResult(null);
    setSelected(null);
    setState("idle");
    localStorage.removeItem("presscheck-last");
  };
  async function upload(file: File | undefined, role: string) {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const asset = await api<Asset>("/api/assets", {
        method: "POST",
        body: form,
      });
      reset();
      if (role === "reference") {
        setReference(asset);
        setRp(0);
      } else {
        setCandidate(asset);
        setCp(0);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function demo(id: string) {
    setBusy(true);
    setError("");
    try {
      const d = await api<{
        reference: Asset;
        candidate: Asset;
        config: Partial<Config>;
      }>(`/api/demos/${id}?clean=${clean}`, { method: "POST" });
      reset();
      setReference(d.reference);
      setCandidate(d.candidate);
      setRp(0);
      setCp(0);
      const cfg = { ...initial, ...d.config };
      setConfig(cfg);
      setTrim(cfg.trim?.join(", ") || "");
      setRegions(cfg.color_regions.map((r) => r.join(",")).join(";"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function parseRect(value: string): number[] {
    const rect = value.split(",").map(Number);
    if (
      rect.length !== 4 ||
      rect.some((x) => !Number.isFinite(x)) ||
      rect[2] <= rect[0] ||
      rect[3] <= rect[1]
    )
      throw Error("Use x0,y0,x1,y1 for each rectangle.");
    return rect;
  }
  async function run() {
    if (!reference || !candidate) return;
    setError("");
    try {
      const cfg = {
        ...config,
        trim: trim.trim() ? parseRect(trim) : null,
        color_regions: regions.trim() ? regions.split(";").map(parseRect) : [],
      };
      const data = await api<{ id: string }>("/api/inspections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reference: reference.id,
          candidate: candidate.id,
          reference_page: rp,
          candidate_page: cp,
          config: cfg,
        }),
      });
      setResult(null);
      setState("queued");
      setJob(data.id);
      localStorage.setItem("presscheck-last", data.id);
      setConfig(cfg);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const finding = result?.findings.find((f) => f.id === selected);
  function slot(
    role: string,
    asset: Asset | null,
    page: number,
    setPage: (n: number) => void,
  ) {
    return (
      <div className={"upload-slot " + (asset ? "loaded" : "")}>
        <div className="slot-label">
          <span className="letter">{role === "reference" ? "A" : "B"}</span>
          <span>
            {role === "reference" ? "APPROVED MASTER" : "CANDIDATE PROOF"}
          </span>
          <span>{asset ? "●" : "○"}</span>
        </div>
        <label className="file-input">
          <input
            aria-label={
              role === "reference"
                ? "Upload approved master"
                : "Upload candidate artwork"
            }
            type="file"
            accept=".png,.jpg,.jpeg,.tif,.tiff,.pdf"
            disabled={processing || busy}
            onChange={(e) => upload(e.target.files?.[0], role)}
          />
          {asset ? (
            <>
              <strong>{asset.name}</strong>
              <small>
                {asset.pages[page].preview_width} ×{" "}
                {asset.pages[page].preview_height} · {asset.pages[page].mode}
              </small>
            </>
          ) : (
            <>
              <strong>＋ Insert artwork</strong>
              <small>PNG / JPEG / TIFF / PDF · 40 MiB MAX</small>
            </>
          )}
        </label>
        {asset && (
          <>
            <select
              aria-label={role + " page"}
              disabled={processing}
              value={page}
              onChange={(e) => {
                reset();
                setPage(+e.target.value);
              }}
            >
              {asset.pages.map((_, i) => (
                <option key={i} value={i}>
                  PAGE {i + 1} / {asset.pages.length}
                </option>
              ))}
            </select>
            <details className="metadata">
              <summary>SOURCE RECORD</summary>
              <p>SHA-256 {asset.sha256}</p>
              {asset.warnings.map((w, i) => (
                <p key={i}>{w}</p>
              ))}
              <a href={`/api/assets/${asset.id}/original`}>
                Download preserved source{" "}
              </a>
            </details>
          </>
        )}
      </div>
    );
  }
  return (
    <main>
      <header className="masthead">
        <a className="wordmark" href="/" aria-label="PRESSCHECK home">
          <Eye />
          <span>
            PRESSCHECK<sup>™</sup>
          </span>
        </a>
        <div className="maker">
          BUILT BY MAGNUS
          <br />
          OPTICAL SYSTEMS DIVISION
          <span>
            MODEL PC–78 <b>●</b> EST. 2026
          </span>
        </div>
      </header>
      <div className="intro">
        <p>THE PACKAGING INTELLIGENCE & INSPECTION MACHINE</p>
        <p>
          PERFECTION IS A MOVING TARGET. <b>WE BROUGHT A SCOPE.</b>
        </p>
      </div>
      <section
        className="quick-start"
        aria-label="Three steps to inspect artwork"
      >
        <div
          className={
            "workflow-step " + (!reference || !candidate ? "active" : "done")
          }
        >
          <span>1</span>
          <div>
            <strong>Add your artwork</strong>
            <p>Upload a master and proof, or try an example.</p>
          </div>
        </div>
        <div
          className={
            "workflow-step " +
            (reference && candidate && !result ? "active" : "")
          }
        >
          <span>2</span>
          <div>
            <strong>Run the inspection</strong>
            <p>Compare artwork, text, color, and barcode data.</p>
          </div>
        </div>
        <div className={"workflow-step " + (result ? "active" : "")}>
          <span>3</span>
          <div>
            <strong>Review & export</strong>
            <p>Select a finding. Download your PDF or JSON report.</p>
          </div>
        </div>
      </section>
      <div className="quick-demos">
        <span>TRY AN EXAMPLE</span>
        {[
          { id: "cards", name: "Card pack" },
          { id: "label", name: "Product label" },
          { id: "toy", name: "Toy package" },
        ].map((d) => (
          <button
            key={d.id}
            disabled={processing || busy}
            onClick={() => demo(d.id)}
          >
            {d.name}
          </button>
        ))}
        <span className="demo-hint">
          Loads both files. You run the inspection.
        </span>
      </div>
      <section className={"console " + (processing ? "processing" : "")}>
        <span className="screw s1" />
        <span className="screw s2" />
        <span className="screw s3" />
        <span className="screw s4" />
        <aside className="inputs">
          <div className="panel-heading">
            <span>1 / ADD YOUR ARTWORK</span>
          </div>
          {slot("reference", reference, rp, setRp)}
          {slot("candidate", candidate, cp, setCp)}
          <div className="settings">
            <label className="control-label" htmlFor="profile">
              Comparison preset
            </label>
            <select
              id="profile"
              disabled={processing}
              onChange={(e) => {
                const v = e.target.value;
                setConfig((c) => ({
                  ...c,
                  delta_e: v === "strict" ? 3 : v === "scan" ? 10 : 6,
                  min_area: v === "strict" ? 20 : v === "scan" ? 100 : 45,
                }));
              }}
            >
              <option value="standard">Standard artwork proof</option>
              <option value="strict">Strict digital revision</option>
              <option value="scan">Scan / photograph screening</option>
            </select>
            <div className="tolerance-label">
              <label htmlFor="tolerance">COLOR TOLERANCE</label>
              <b>ΔE {config.delta_e.toFixed(1)}</b>
            </div>
            <input
              id="tolerance"
              type="range"
              min="1"
              max="20"
              step=".5"
              value={config.delta_e}
              disabled={processing}
              onChange={(e) =>
                setConfig({ ...config, delta_e: +e.target.value })
              }
            />
            <div className="range-labels">
              <span>PRECISE</span>
              <span>PERMISSIVE</span>
            </div>
            <details className="advanced">
              <summary>Advanced inspection settings</summary>
              <label>
                Trim · x0,y0,x1,y1
                <input
                  placeholder="30,30,770,1030"
                  value={trim}
                  disabled={processing}
                  onChange={(e) => setTrim(e.target.value)}
                />
              </label>
              <label>
                Bleed (preview px)
                <input
                  type="number"
                  min="0"
                  max="300"
                  value={config.bleed_px}
                  onChange={(e) =>
                    setConfig({ ...config, bleed_px: +e.target.value })
                  }
                />
              </label>
              <label>
                Safe inset (preview px)
                <input
                  type="number"
                  min="0"
                  max="300"
                  value={config.safe_px}
                  onChange={(e) =>
                    setConfig({ ...config, safe_px: +e.target.value })
                  }
                />
              </label>
              <label>
                Color sample rectangles · separate with ;
                <input
                  value={regions}
                  placeholder="240,520,290,570"
                  onChange={(e) => setRegions(e.target.value)}
                />
              </label>
              <label>
                Minimum changed region (px)
                <input
                  type="number"
                  min="4"
                  max="10000"
                  value={config.min_area}
                  onChange={(e) =>
                    setConfig({ ...config, min_area: +e.target.value })
                  }
                />
              </label>
              <label>
                FAIL threshold (% changed area)
                <input
                  type="number"
                  min=".1"
                  max="100"
                  step=".1"
                  value={config.fail_area_percent}
                  onChange={(e) =>
                    setConfig({ ...config, fail_area_percent: +e.target.value })
                  }
                />
              </label>
              <label>
                Registration confidence floor
                <input
                  type="number"
                  min="0"
                  max="1"
                  step=".05"
                  value={config.min_registration}
                  onChange={(e) =>
                    setConfig({ ...config, min_registration: +e.target.value })
                  }
                />
              </label>
              {(["ocr", "barcode", "ai"] as const).map((key) => (
                <label className="check-label" key={key}>
                  <input
                    type="checkbox"
                    checked={config[key]}
                    onChange={(e) =>
                      setConfig({ ...config, [key]: e.target.checked })
                    }
                  />
                  {key === "ai"
                    ? "Optional AI interpretation"
                    : key === "ocr"
                      ? "OCR text inspection"
                      : "Barcode inspection"}
                </label>
              ))}
              <p>
                Geometry uses normalized preview pixels. AI sends both previews
                to the configured provider when enabled.
              </p>
            </details>
          </div>
          <button
            className="start"
            disabled={!reference || !candidate || processing || busy}
            onClick={run}
          >
            <span>{processing ? "INSPECTING…" : "2 · RUN INSPECTION"}</span>
            <b>{processing ? "◌" : ""}</b>
          </button>
          <p className="local-note">
            DETERMINISTIC ENGINE
            <br />
            NO PAID AI REQUIRED
          </p>
        </aside>
        <Viewer
          reference={reference?.pages[rp]?.preview || null}
          candidate={candidate?.pages[cp]?.preview || null}
          result={result}
          selected={selected}
          onSelect={setSelected}
          config={config}
        />
        <aside className="analysis">
          <div className="panel-heading">
            <span>3 / REVIEW RESULTS</span>
            <span>◎</span>
          </div>
          <div
            className={
              "status-card " +
              (result?.status === "PASS" ? "pass" : result ? "review" : "")
            }
          >
            <div className="status-top">
              <span>INSPECTION STATUS</span>
              <span className="tiny-led" />
            </div>
            <h2 aria-live="polite">
              {processing ? "IN PROGRESS" : result?.status || "STANDING BY"}
            </h2>
            <p>
              {processing
                ? "OPTICAL ARRAY IS WORKING"
                : result
                  ? "SCREENING RESULT / HUMAN REVIEW ADVISED"
                  : "ADD BOTH FILES, THEN RUN INSPECTION"}
            </p>
          </div>
          <div className="gauge">
            <div className="gauge-arc">
              <div
                className="needle"
                style={{
                  transform: `rotate(${-80 + (result?.registration.confidence || 0) * 160}deg)`,
                }}
              />
            </div>
            <div className="gauge-value">
              {result ? Math.round(result.registration.confidence * 100) : "—"}
              <small>{result ? "%" : ""}</small>
            </div>
            <p>REGISTRATION SUPPORT</p>
            <span>{result?.registration.method || "NO MEASUREMENT"}</span>
          </div>
          <div className="readout">
            <span>POTENTIAL FINDINGS</span>
            <b>
              {result ? String(result.findings.length).padStart(2, "0") : "—"}
            </b>
          </div>
          {result && (
            <p className="severity-count">
              {result.findings.filter((f) => f.severity === "major").length}{" "}
              major ·{" "}
              {result.findings.filter((f) => f.severity === "minor").length}{" "}
              minor ·{" "}
              {result.findings.filter((f) => f.severity === "review").length}{" "}
              need review
            </p>
          )}
          <div className="metric-grid">
            <div>
              <small>MEAN ΔE₀₀</small>
              <strong>{result?.metrics.mean_delta_e?.toFixed(2) ?? "—"}</strong>
            </div>
            <div>
              <small>CHANGED AREA</small>
              <strong>
                {result
                  ? result.metrics.changed_area_percent.toFixed(2) + "%"
                  : "—"}
              </strong>
            </div>
            <div>
              <small>TEXT CHANGES</small>
              <strong>
                {result
                  ? result.text.available
                    ? result.text.changes.length
                    : "N/A"
                  : "—"}
              </strong>
            </div>
            <div>
              <small>BARCODE</small>
              <strong className="small-value">
                {result?.barcode.status?.toUpperCase() || "—"}
              </strong>
            </div>
          </div>
          <div className="findings" aria-label="Inspection findings">
            {result?.findings.map((f) => (
              <button
                key={f.id}
                className={selected === f.id ? "selected" : ""}
                onClick={() => setSelected(f.id)}
              >
                <span>
                  {f.id} · {f.kind.toUpperCase()}
                </span>
                <strong>{f.title}</strong>
              </button>
            ))}
            {result && !result.findings.length && (
              <p>No differences above configured thresholds.</p>
            )}
          </div>
          {finding && (
            <div className="finding-detail">
              <h3>
                {finding.id} / {finding.title}
              </h3>
              <p>{finding.recommendation}</p>
              {finding.mean_delta_e !== undefined && (
                <p>Mean ΔE₀₀: {finding.mean_delta_e}</p>
              )}
              {finding.evidence !== undefined && (
                <details>
                  <summary>MEASURED EVIDENCE</summary>
                  <pre>{JSON.stringify(finding.evidence, null, 2)}</pre>
                </details>
              )}
            </div>
          )}
          <div className="exports">
            <a
              className={!result ? "disabled" : ""}
              href={
                result
                  ? `/api/inspections/${result.id}/files/report.pdf`
                  : undefined
              }
            >
              PDF REPORT
            </a>
            <a
              className={!result ? "disabled" : ""}
              href={
                result
                  ? `/api/inspections/${result.id}/files/report.json`
                  : undefined
              }
            >
              JSON
            </a>
          </div>
        </aside>
        <div className="status-strip">
          <span>
            <i className={health ? "on" : ""} /> OPTICAL ARRAY{" "}
            {health ? "ONLINE" : "OFFLINE"}
          </span>
          <span>
            <i className={reference ? "on" : ""} /> REFERENCE{" "}
            {reference ? "LOCKED" : "EMPTY"}
          </span>
          <span>
            <i className={result ? "on" : ""} />{" "}
            {processing
              ? "ANALYSIS RUNNING"
              : result
                ? "ANALYSIS COMPLETE"
                : "AWAITING ARTWORK"}
          </span>
          <span>PC–78 / v0.1.0</span>
        </div>
      </section>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      <section className="demo-bench">
        <div className="bench-title">
          <div>
            <span className="eyebrow">EXAMPLE ARTWORK</span>
            <h2>Or start with an example.</h2>
          </div>
          <label className="clean-switch">
            <input
              type="checkbox"
              checked={clean}
              onChange={(e) => setClean(e.target.checked)}
            />{" "}
            Use matching files (no defects)
          </label>
        </div>
        <div className="demo-grid">
          {[
            {
              id: "cards",
              n: "A",
              name: "Orbit Club",
              sub: "COLLECTIBLE CARD PACK",
              color: "turquoise",
              desc: "Legal copy, logo position & artwork changes.",
            },
            {
              id: "label",
              n: "B",
              name: "Solar Soda",
              sub: "CONSUMER PRODUCT LABEL",
              color: "yellow",
              desc: "Product copy, color variation & barcode data.",
            },
            {
              id: "toy",
              n: "C",
              name: "Moon Rover",
              sub: "TOY PACKAGE",
              color: "orange",
              desc: "Clipping, safe-zone text & missing artwork.",
            },
          ].map((d) => (
            <button
              className={"demo-card " + d.color}
              key={d.id}
              disabled={processing || busy}
              onClick={() => demo(d.id)}
            >
              <span className="demo-letter">{d.n}</span>
              <div>
                <span className="eyebrow">{d.sub}</span>
                <h3>{d.name}</h3>
                <p>{d.desc}</p>
              </div>
            </button>
          ))}
        </div>
        <p className="sample-note">
          ORIGINAL FICTIONAL ARTWORK. REAL INSPECTIONS. NO PRECOMPUTED FINDINGS.
        </p>
      </section>
      {result && (
        <section className="evidence-notes">
          <h2>Inspection notes</h2>
          <div className="note-grid">
            <div>
              <h3>Supported checks</h3>
              {result.geometry.map((g, i) => (
                <p key={i}>
                  {g.check}: <b>{g.status.toUpperCase()}</b>
                </p>
              ))}
              <p>Elapsed: {result.metrics.processing_seconds}s</p>
            </div>
            <div>
              <h3>Color sample evidence</h3>
              <pre>{JSON.stringify(result.color_samples, null, 2)}</pre>
              <h3>AI interpretation</h3>
              <p>
                {result.ai.available
                  ? "AI-GENERATED / UNVERIFIED: " + result.ai.text
                  : result.ai.reason}
              </p>
            </div>
            <div>
              <h3>Measurement limits</h3>
              {result.warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </div>
          </div>
        </section>
      )}
      <details className="history">
        <summary>INSPECTION LOG / {history.length} RECENT RUNS</summary>
        {history.map((h) => (
          <button
            key={h.id}
            onClick={() => {
              setError("");
              setJob(h.id);
              localStorage.setItem("presscheck-last", h.id);
            }}
          >
            {h.id.slice(0, 8)} · {h.status || h.state} ·{" "}
            {new Date(h.created).toLocaleString()}
          </button>
        ))}
      </details>
      <footer>
        <div className="footer-brand">
          BUILT BY MAGNUS <span>OPTICAL SYSTEMS DIVISION</span>
        </div>
        <p>A LITTLE OBSESSIVE. BY DESIGN.</p>
        <span>AUTOMATED SCREENING ≠ PRODUCTION APPROVAL</span>
      </footer>
    </main>
  );
}
