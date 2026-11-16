"use client";

import { useEffect, useId, useRef, useState } from "react";
import styles from "./production.module.css";
import { captureDesktopToken, downloadAPI, downloadBlob, productionFetch, productionJSON } from "./api";
import { defaultJob, normalizeJob, parseProfiles, exportProfiles, convertLength, parseLength, type LengthUnit } from "./profiles.mjs";

type Job = ReturnType<typeof defaultJob>;
type Profile = { id: string; name: string; job: Job };
type Finding = { id: string; rule_id: string; status: string; message: string; object_ids: string[]; measured?: unknown; expected?: unknown; unit?: string };
type Report = {
  id: string; status: string; job: Job;
  source: { name: string; sha256: string; bytes: number; width_mm?: number; height_mm?: number; vector_objects: number; text_objects: number; has_raster: boolean; production_roles: string[]; element_counts: Record<string, number> };
  findings: Finding[]; limitations: string[]; preview_url?: string; report_url: string; original_url: string;
};
type StructureSpec = { kind: string; name: string; width_mm: number; depth_mm: number; height_mm: number; caliper_mm: number; glue_flap_mm: number };
type Structure = { id: string; svg: string; project: unknown; preview_png: string; dimensions: Record<string, number>; components: { id: string; role: string; count?: number }[]; limitations: string[]; job: Job; specification: StructureSpec };
const PROFILE_KEY = "presscheck.production.profiles.v1";
const LAST_REPORT_KEY = "presscheck.production.lastReport.v1";
const ROLES = ["artwork", "cut", "crease", "perforation", "glue", "white_ink", "foil", "varnish", "emboss", "deboss", "registration", "barcode", "dimensions", "legend"];
const initialStructure: StructureSpec = { kind: "tuck_carton", name: "Straight-tuck carton study", width_mm: 70, depth_mm: 25, height_mm: 100, caliper_mm: .4, glue_flap_mm: 12 };
const label = (value: string) => value.replaceAll("_", " ");
const display = (value: unknown) => value === undefined || value === null ? "Not specified" : typeof value === "object" ? JSON.stringify(value) : String(value);
const message = (error: unknown) => error instanceof Error ? error.message : "Something went wrong. Try again.";

function Eye() {
  return <svg viewBox="0 0 72 48" aria-hidden="true"><path d="M3 24Q36-14 69 24Q36 62 3 24Z" fill="none" stroke="currentColor" strokeWidth="5" /><circle cx="36" cy="24" r="12" fill="currentColor" /><path d="M36 0v12M36 36v12M0 24h18M54 24h18" stroke="currentColor" strokeWidth="3" /></svg>;
}

function Preview({ report }: { report: Report }) {
  const [url, setURL] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    let objectURL = "";
    setURL(""); setError("");
    if (!report.preview_url) return;
    productionFetch(report.preview_url).then(async response => {
      if (!(response.headers.get("content-type") || "").startsWith("image/png")) throw new Error("The preview was not a safe PNG image.");
      const blob = await response.blob();
      const bytes = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
      if (bytes.join(",") !== "137,80,78,71,13,10,26,10") throw new Error("Invalid PNG preview.");
      if (!cancelled) { objectURL = URL.createObjectURL(blob); setURL(objectURL); }
    }).catch(error => { if (!cancelled) setError(message(error)); });
    return () => { cancelled = true; if (objectURL) URL.revokeObjectURL(objectURL); };
  }, [report.id, report.preview_url]);
  return <div className={styles.preview}>
    {url ? <img src={url} alt={`Safe raster preview of ${report.source.name}. Use the findings and original file for inspection.`} /> : <p>{error || (report.preview_url ? "Preparing safe PNG preview…" : "No safe preview is available for this SVG. The report and original source remain available.")}</p>}
    <span>PNG PREVIEW · ORIGINAL SVG IS NEVER EMBEDDED</span>
  </div>;
}

function LengthField({ title, value, onChange, unit, optional = false }: { title: string; value: number | null; onChange: (value: number | null) => void; unit: LengthUnit; optional?: boolean }) {
  const id = useId();
  const [raw, setRaw] = useState("");
  const [focused, setFocused] = useState(false);
  useEffect(() => { if (!focused) setRaw(value === null || value === undefined ? "" : String(convertLength(value, unit))); }, [value, unit, focused]);
  return <label className={styles.field} htmlFor={id}><span>{title} <small>{unit}</small></span><input id={id} type="number" inputMode="decimal" step="any" min="0" value={raw} placeholder={optional ? "Unknown" : "Not specified"} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)} onChange={event => { setRaw(event.target.value); onChange(parseLength(event.target.value, unit)); }} /></label>;
}

export default function ProductionWorkspace() {
  const [tab, setTab] = useState("preflight");
  const [desktop, setDesktop] = useState<boolean | null>(null);
  const [job, setJob] = useState<Job>(defaultJob);
  const [unit, setUnit] = useState<LengthUnit>("mm");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedProfile, setSelectedProfile] = useState("");
  const [profilesLoaded, setProfilesLoaded] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [reportID, setReportID] = useState("");
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [structureSpec, setStructureSpec] = useState<StructureSpec>(initialStructure);
  const [structure, setStructure] = useState<Structure | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const tabNames = ["preflight", "structure", "report"];

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      let installed = false;
      try { installed = captureDesktopToken(); setDesktop(installed); }
      catch (error) { setError(message(error)); return; }
      try {
        const stored = installed
          ? JSON.stringify(await productionJSON("/api/profiles"))
          : localStorage.getItem(PROFILE_KEY);
        if (stored && !cancelled) setProfiles(parseProfiles(stored));
      } catch (error) { if (!cancelled) setError(`Saved printer profiles could not be loaded: ${message(error)} Import a JSON export to restore them.`); }
      finally { if (!cancelled) setProfilesLoaded(true); }
      try {
        const last = localStorage.getItem(LAST_REPORT_KEY);
        if (!cancelled && last && /^[a-f0-9]{32}$/.test(last)) setReportID(last);
      } catch { /* Opening a report by its ID remains available. */ }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  const changeJob = (key: string, value: unknown) => { setJob((current: Job) => ({ ...current, [key]: value, supplier_confirmed: key === "supplier_confirmed" ? Boolean(value) : false })); setSelectedProfile(""); };
  const switchTab = (next: string) => { setTab(next); window.requestAnimationFrame(() => tabRefs.current[tabNames.indexOf(next)]?.focus()); };
  const resetMessages = () => { setError(""); setNotice(""); };
  const saveProfiles = async (next: Profile[]) => {
    if (desktop === null || !profilesLoaded) throw new Error("Wait for printer profiles to finish loading.");
    const json = exportProfiles(next);
    if (desktop) await productionJSON("/api/profiles", { method: "PUT", headers: { "Content-Type": "application/json" }, body: json });
    else localStorage.setItem(PROFILE_KEY, json);
    setProfiles(parseProfiles(json));
  };
  const saveProfile = async () => {
    resetMessages(); setBusy("Saving printer profile…");
    try {
      const normalized = normalizeJob(job);
      const name = (normalized.profile_name || normalized.name).trim();
      const existing = selectedProfile && profiles.find(item => item.id === selectedProfile);
      const profile = { id: existing ? existing.id : crypto.randomUUID(), name, job: normalized };
      const next = existing ? profiles.map(item => item.id === existing.id ? profile : item) : [...profiles, profile];
      await saveProfiles(next); setJob({ ...normalized, supplier_confirmed: false }); setSelectedProfile(profile.id); setNotice(`Saved “${name}” on this device.`);
    } catch (error) { setError(message(error)); } finally { setBusy(""); }
  };
  const importProfiles = async (selected: File | undefined) => {
    if (!selected) return;
    resetMessages(); setBusy("Importing printer profiles…");
    try {
      if (selected.size > 1024 * 1024) throw new Error("Profile files must be smaller than 1 MB.");
      const imported = parseProfiles(await selected.text());
      const known = new Set(profiles.map(item => item.id));
      const additions = imported.map(item => ({ ...item, id: known.has(item.id) ? crypto.randomUUID() : item.id }));
      await saveProfiles([...profiles, ...additions]); setNotice(`Imported ${additions.length} printer profile${additions.length === 1 ? "" : "s"}.`);
    } catch (error) { setError(message(error)); }
    finally { setBusy(""); }
    if (importRef.current) importRef.current.value = "";
  };
  const openReport = async (id: string) => {
    resetMessages();
    if (!/^[a-f0-9]{32}$/.test(id.trim())) { setError("Enter the 32-character report ID from a saved report."); return; }
    setBusy("Opening report…");
    try { const loaded = await productionJSON<Report>(`/api/preflights/${id.trim()}`); setReport(loaded); setReportID(loaded.id); setFilter("all"); switchTab("report"); setNotice("Saved report opened. Review its findings and inspection limits."); }
    catch (error) { setError(message(error)); } finally { setBusy(""); }
  };
  const runPreflight = async (event: React.FormEvent) => {
    event.preventDefault(); resetMessages();
    if (!file) { setError("Choose an SVG artwork file first."); return; }
    setBusy("Inspecting SVG geometry and job requirements…");
    try {
      const normalized = normalizeJob(job);
      const data = new FormData(); data.set("file", file); data.set("job", JSON.stringify(normalized));
      const result = await productionJSON<Report>("/api/preflights", { method: "POST", body: data });
      setReport(result); setReportID(result.id); switchTab("report"); setFilter("all"); setNotice("Preflight complete. Review the findings and any unsupported checks before production.");
      try { localStorage.setItem(LAST_REPORT_KEY, result.id); } catch { setNotice("Report created. Download its JSON because this browser cannot remember its ID."); }
    } catch (error) { setError(message(error)); } finally { setBusy(""); }
  };
  const chooseSVG = (chosen: File | undefined) => {
    resetMessages();
    if (!chosen) return;
    if (chosen.size > 40 * 1024 * 1024) { setError("SVG files must be no larger than 40 MB."); return; }
    if (!chosen.name.toLowerCase().endsWith(".svg")) { setError("Choose an SVG file. PDF and image artwork belong in Master Proof."); return; }
    setFile(chosen);
  };
  const generateStructure = async (event: React.FormEvent) => {
    event.preventDefault(); resetMessages(); setBusy("Building editable carton geometry…");
    try { setStructure(await productionJSON<Structure>("/api/structures", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(structureSpec) })); }
    catch (error) { setError(message(error)); } finally { setBusy(""); }
  };
  const download = async (path: string, name: string) => {
    resetMessages(); setBusy("Preparing download…");
    try { await downloadAPI(path, name); } catch (error) { setError(message(error)); } finally { setBusy(""); }
  };
  const localJSON = (value: unknown, name: string) => downloadBlob(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }), name);
  const applyStructure = () => {
    if (!structure) return;
    setJob(normalizeJob(structure.job));
    setFile(new File([structure.svg], "presscheck-carton-prototype.svg", { type: "image/svg+xml" }));
    setSelectedProfile(""); switchTab("preflight"); resetMessages();
    setNotice("Prototype loaded. The job dimensions describe its annotated drawing canvas, not a finished carton. Review the requirements before checking.");
  };

  return <main className={styles.main}>
    <header className="masthead">
      <a className="wordmark" href="/production/" aria-label="PRESSCHECK Production Studio home"><Eye /><span>PRESSCHECK<sup>™</sup></span></a>
      <div className="maker">BUILT BY MAGNUS<br />PRODUCTION SYSTEMS DIVISION<span>STUDIO PC–78 <b>●</b> EST. 2026</span></div>
    </header>
    <div className={styles.intro}>
      <div><p className={styles.eyebrow}>MEASURE TWICE. MAKE SOMETHING.</p><h1>Production Studio</h1><p>Editable structure. Explicit specifications. Artwork checks you can inspect.</p></div>
      {desktop === false && <a className={styles.textLink} href="/">Open Master Proof ↗<span>Existing artwork comparison workspace</span></a>}
    </div>
    <div className={styles.boundary}><strong>YOUR PRINTER SETS THE LIMITS.</strong><span>Start with their specification. Unknown requirements stay unknown; a digital check does not approve a press run or physical structure.</span></div>
    <div role="tablist" aria-label="Production workspaces" className={styles.tabs}>
      {[ ["preflight", "01", "Card Preflight"], ["structure", "02", "Structure"], ["report", "03", "Report"] ].map(([id, number, title], index) => <button key={id} ref={element => { tabRefs.current[index] = element; }} id={`tab-${id}`} role="tab" aria-selected={tab === id} aria-controls={`panel-${id}`} tabIndex={tab === id ? 0 : -1} onClick={() => { setTab(id); resetMessages(); }} onKeyDown={event => { let next = index; if (event.key === "ArrowRight") next = (index + 1) % 3; else if (event.key === "ArrowLeft") next = (index + 2) % 3; else if (event.key === "Home") next = 0; else if (event.key === "End") next = 2; else return; event.preventDefault(); setTab(tabNames[next]); tabRefs.current[next]?.focus(); }}><small>{number}</small>{title}{id === "report" && report && <i aria-label="Report available" />}</button>)}
    </div>
    {error && <div role="alert" className={styles.error}>{error}</div>}
    {notice && <div role="status" className={styles.notice}>{notice}</div>}
    {busy && <div role="status" aria-live="polite" className={styles.busy}><span />{busy}</div>}

    <section id="panel-preflight" role="tabpanel" aria-labelledby="tab-preflight" hidden={tab !== "preflight"}>
      <form onSubmit={runPreflight} className={styles.grid} aria-busy={Boolean(busy)}>
        <div className={styles.panel}>
          <div className={styles.panelHeader}><span>INPUT / SVG</span><b>01</b></div>
          <h2>Bring the artwork.</h2><p className={styles.muted}>Upload an SVG with physical dimensions. The source is preserved; checks never rewrite your artwork.</p>
          <label className={styles.upload}><span>{file ? file.name : "Choose SVG artwork"}</span><small>{file ? `${(file.size / 1024).toLocaleString(undefined, { maximumFractionDigits: 1 })} KB · ready to inspect` : "Native vector geometry · up to 40 MB"}</small><input ref={fileRef} type="file" accept=".svg,image/svg+xml" onChange={event => chooseSVG(event.target.files?.[0])} /></label>
          <div className={styles.cardNote}><strong>What this checks</strong><p>Document size, supported geometry, configured margins, text and stroke limits, raster resolution and declared production roles.</p><strong>What still needs people</strong><p>Colour separations, trapping, overprint, grain, foil registration, tooling and physical fit need the supplier and a proof.</p></div>
          <button type="submit" className={styles.primary} disabled={Boolean(busy) || !file}>Run artwork preflight <span>→</span></button>
          <p className={styles.micro}>The report names affected object IDs and distinguishes failures, missing specifications and unsupported checks.</p>
          <hr />
          <h3>Return to a report</h3><label className={styles.field}><span>Report ID</span><input value={reportID} onChange={event => setReportID(event.target.value)} placeholder="32-character ID" autoCapitalize="none" spellCheck={false} maxLength={32} /></label><button type="button" className={styles.secondary} onClick={() => openReport(reportID)} disabled={Boolean(busy) || !reportID}>Open saved report</button>
        </div>
        <div className={styles.panel}>
          <div className={styles.panelHeader}><span>PRINTER / JOB SPECIFICATION</span><b>02</b></div>
          <div className={styles.profileBar}><label className={styles.field}><span>Saved printer profiles</span><select value={selectedProfile} disabled={!profilesLoaded || Boolean(busy)} onChange={event => { const selected = profiles.find(item => item.id === event.target.value); setSelectedProfile(event.target.value); if (selected) { setJob({ ...normalizeJob(selected.job), supplier_confirmed: false }); resetMessages(); } }}><option value="">Custom / unsaved</option>{profiles.map(profile => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</select></label><div className={styles.actions}><button type="button" disabled={!profilesLoaded || Boolean(busy)} onClick={saveProfile}>Save profile</button>{selectedProfile && <button type="button" disabled={Boolean(busy)} onClick={async () => { resetMessages(); setBusy("Removing printer profile…"); try { await saveProfiles(profiles.filter(item => item.id !== selectedProfile)); setSelectedProfile(""); setNotice("Profile removed from this device."); } catch (error) { setError(message(error)); } finally { setBusy(""); } }}>Delete</button>}<button type="button" disabled={!profilesLoaded || Boolean(busy)} onClick={() => importRef.current?.click()}>Import JSON</button><button type="button" disabled={!profiles.length || Boolean(busy)} onClick={() => { try { downloadBlob(new Blob([exportProfiles(profiles)], { type: "application/json" }), "presscheck-printer-profiles.json"); } catch (error) { setError(message(error)); } }}>Export JSON</button><input ref={importRef} type="file" accept=".json,application/json" hidden onChange={event => importProfiles(event.target.files?.[0])} /></div></div>
          <div className={styles.fields}><label className={styles.field}><span>Job name</span><input value={job.name} maxLength={160} onChange={event => changeJob("name", event.target.value)} /></label><label className={styles.field}><span>Profile / printer name</span><input value={job.profile_name || ""} maxLength={160} placeholder="Your printer’s specification" onChange={event => changeJob("profile_name", event.target.value)} /></label><label className={styles.field}><span>Workflow</span><select value={job.workflow} onChange={event => changeJob("workflow", event.target.value)}><option value="trading_card">Trading card artwork</option><option value="packaging">Packaging / structure</option></select></label><label className={styles.field}><span>Display units</span><select value={unit} onChange={event => setUnit(event.target.value as LengthUnit)}><option value="mm">Millimetres (mm)</option><option value="in">Inches (in)</option></select></label></div>
          <fieldset className={styles.fieldset}><legend>Physical dimensions <small>Leave unknown values blank</small></legend><div className={styles.fields}><LengthField title="Trim width" value={job.trim_width_mm} unit={unit} onChange={value => changeJob("trim_width_mm", value)} /><LengthField title="Trim height" value={job.trim_height_mm} unit={unit} onChange={value => changeJob("trim_height_mm", value)} /><LengthField title="Bleed per edge" value={job.bleed_mm} unit={unit} optional onChange={value => changeJob("bleed_mm", value)} /><LengthField title="Safe margin" value={job.safe_mm} unit={unit} optional onChange={value => changeJob("safe_mm", value)} /></div></fieldset>
          <div className={styles.fields}><label className={styles.field}><span>Bleed intent</span><select value={job.bleed_intent} onChange={event => changeJob("bleed_intent", event.target.value)}><option value="full_bleed">Artwork to bleed edge</option><option value="white_border">Intentional white border / reference</option></select></label><label className={styles.field}><span>Colour policy</span><select value={job.color_policy} onChange={event => changeJob("color_policy", event.target.value)}><option value="review_rgb">Require colour review</option><option value="rgb">RGB accepted by supplier</option><option value="cmyk">CMYK required (SVG cannot certify)</option></select></label><label className={styles.field}><span>Magdobe export placement</span><select value={job.placement} onChange={event => changeJob("placement", event.target.value)}><option value="fit_trim">Fit inside trim</option><option value="fill_bleed">Fill bleed area</option></select></label></div><p className={styles.micro}>Placement is metadata for Magdobe’s production export. This preflight inspects the uploaded SVG coordinates without resizing them.</p>
          <fieldset className={styles.fieldset}><legend>Supplier limits <small>Optional, never assumed</small></legend><div className={styles.fieldsThree}>{[["min_text_pt", "Minimum text", "pt"], ["min_stroke_mm", "Minimum stroke", "mm"], ["min_raster_ppi", "Minimum raster", "ppi"]].map(([key, title, suffix]) => <label className={styles.field} key={key}><span>{title} <small>{suffix}</small></span><input type="number" step="any" min="0" value={job[key] ?? ""} placeholder="Unknown" onChange={event => changeJob(key, event.target.value === "" ? null : Number(event.target.value))} /></label>)}</div></fieldset>
          <fieldset className={styles.fieldset}><legend>Required production roles</legend><p className={styles.micro}>Checks declared roles, not whether a physical finishing process will succeed.</p><div className={styles.roles}>{ROLES.map(role => <label key={role}><input type="checkbox" checked={job.expected_roles.includes(role)} onChange={event => changeJob("expected_roles", event.target.checked ? [...job.expected_roles, role] : job.expected_roles.filter(item => item !== role))} />{label(role)}</label>)}</div></fieldset>
          <label className={styles.field}><span>Specification source</span><input value={job.spec_source || ""} maxLength={2000} placeholder="Supplier, document revision or reference URL" onChange={event => changeJob("spec_source", event.target.value)} /></label><label className={styles.field}><span>Printer notes / stock / finishing</span><textarea value={job.printer_notes || ""} maxLength={10000} rows={3} placeholder="Record material, grain, finishing and any requirements still to confirm." onChange={event => changeJob("printer_notes", event.target.value)} /></label><label className={styles.check}><input type="checkbox" checked={job.supplier_confirmed} onChange={event => changeJob("supplier_confirmed", event.target.checked)} /><span>I have confirmed this specification with the supplier. <small>This is a recorded declaration, not automated supplier approval.</small></span></label>
        </div>
      </form>
    </section>

    <section id="panel-structure" role="tabpanel" aria-labelledby="tab-structure" hidden={tab !== "structure"}>
      <div className={styles.structureGrid}><form onSubmit={generateStructure} className={styles.panel} aria-busy={Boolean(busy)}><div className={styles.panelHeader}><span>STRUCTURAL STUDY / 01</span><b>↗</b></div><h2>A carton you can edit.</h2><p className={styles.muted}>Generate a straight-tuck carton study from nominal panel dimensions. Start a discussion with your converter, then verify it in stock.</p><label className={styles.field}><span>Study name</span><input value={structureSpec.name} maxLength={120} onChange={event => setStructureSpec({ ...structureSpec, name: event.target.value })} required /></label><div className={styles.fields}>{[["width_mm", "Width", 20, 400], ["depth_mm", "Depth", 8, 200], ["height_mm", "Height", 20, 600], ["caliper_mm", "Board caliper", .1, 2], ["glue_flap_mm", "Glue flap", 5, 50]].map(([key, title, min, max]) => <label className={styles.field} key={key}><span>{title} <small>mm</small></span><input type="number" step="any" min={min} max={max} required value={structureSpec[key]} onChange={event => setStructureSpec({ ...structureSpec, [key]: event.target.value === "" ? "" : Number(event.target.value) })} /></label>)}</div><p className={styles.micro}>W / D / H are fold-line panel sizes, not certified internal capacity. Caliper affects an experimental flap-relief rule only.</p><button type="submit" className={styles.primary} disabled={Boolean(busy)}>Generate structure <span>→</span></button><div className={styles.cardNote}><strong>STUDY, NOT TOOLING</strong><p>Supplier CAD, bend allowance, grain, glue and actual card-stack fit remain unverified. Display colours are not press spot separations. Cut and crease geometry is editable; this is not a machine-ready die.</p></div></form>
      <div className={styles.panel}>{structure ? <><div className={styles.panelHeader}><span>PROTOTYPE / UNVERIFIED</span><b>{structure.components.find(component => component.role === "crease")?.count} FOLDS</b></div><div className={styles.structurePreview}><img src={structure.preview_png} alt={`Generated flat carton prototype with a red closed cut outline, blue crease lines and green glue flap. Nominal dimensions ${structure.dimensions.width_mm} by ${structure.dimensions.depth_mm} by ${structure.dimensions.height_mm} millimetres.`} /></div><dl className={styles.metrics}><div><dt>Nominal W × D × H</dt><dd>{structure.dimensions.width_mm} × {structure.dimensions.depth_mm} × {structure.dimensions.height_mm} mm</dd></div><div><dt>Flat blank</dt><dd>{structure.dimensions.blank_width_mm.toFixed(2)} × {structure.dimensions.blank_height_mm.toFixed(2)} mm</dd></div><div><dt>Annotated canvas</dt><dd>{structure.dimensions.canvas_width_mm.toFixed(2)} × {structure.dimensions.canvas_height_mm.toFixed(2)} mm</dd></div></dl><div className={styles.actions}><button type="button" className={styles.primary} onClick={() => localJSON(structure.project, "presscheck-carton-prototype.mvector")}>Download editable Magdobe project ↓</button><button type="button" onClick={() => downloadBlob(new Blob([structure.svg], { type: "image/svg+xml" }), "presscheck-carton-prototype.svg")}>Download SVG</button><button type="button" onClick={() => localJSON({ schema_version: 1, specification: structure.specification, dimensions: structure.dimensions, components: structure.components, limitations: structure.limitations, job: structure.job }, "presscheck-carton-handoff.json")}>Handoff JSON</button><button type="button" onClick={applyStructure}>Use for packaging preflight →</button></div><ul className={styles.limitations}>{structure.limitations.map(item => <li key={item}>{item}</li>)}</ul></> : <div className={styles.empty}><span aria-hidden="true">▱</span><h3>Start with the dimensions.</h3><p>Your generated drawing includes one closed cut outline, individual crease paths, a glue guide and separate reference annotations.</p><p>Download editable Magdobe geometry, SVG and a handoff manifest.</p></div>}</div></div>
    </section>

    <section id="panel-report" role="tabpanel" aria-labelledby="tab-report" hidden={tab !== "report"}>
      {report ? <><div className={styles.reportHeading}><div><p className={styles.eyebrow}>INSPECTION / {report.id}</p><h2>{report.source.name}</h2><span className={`${styles.badge} ${styles[report.status] || ""}`}>{label(report.status)}</span></div><div className={styles.actions}><button type="button" disabled={Boolean(busy)} onClick={() => download(report.report_url, `presscheck-${report.id}.json`)}>Download full report ↓</button><button type="button" disabled={Boolean(busy)} onClick={() => download(report.original_url, report.source.name)}>Download preserved SVG ↓</button></div></div><div className={styles.reportGrid}><div className={styles.panel}><Preview report={report} /><dl className={styles.metrics}><div><dt>Document</dt><dd>{report.source.width_mm ?? "?"} × {report.source.height_mm ?? "?"} mm</dd></div><div><dt>Vector objects</dt><dd>{report.source.vector_objects.toLocaleString()}</dd></div><div><dt>Text objects / raster</dt><dd>{report.source.text_objects} / {report.source.has_raster ? "Present" : "None"}</dd></div><div><dt>Declared roles</dt><dd>{report.source.production_roles.length ? report.source.production_roles.map(label).join(", ") : "None declared"}</dd></div></dl><details className={styles.details}><summary>Source integrity & job specification</summary><p className={styles.hash}>SHA-256 {report.source.sha256}</p><p className={styles.micro}>Recorded supplier confirmation: {report.job.supplier_confirmed ? "Declared by user" : "Unconfirmed"}</p><pre>{JSON.stringify(report.job, null, 2)}</pre></details></div><div className={styles.panel}><div className={styles.panelHeader}><span>FINDINGS / {report.findings.length}</span><b>INSPECT</b></div><label className={styles.field}><span>Show findings</span><select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">All findings</option>{[...new Set(report.findings.map(item => item.status))].map(status => <option key={status} value={status}>{label(status)}</option>)}</select></label><div className={styles.findings}>{report.findings.filter(item => filter === "all" || item.status === filter).map(finding => <article className={styles.finding} key={finding.id}><div><span className={`${styles.badge} ${styles[finding.status] || ""}`}>{label(finding.status)}</span><code>{finding.id} / {finding.rule_id}</code></div><p>{finding.message}</p>{(finding.measured !== undefined || finding.expected !== undefined) && <dl><div><dt>Measured</dt><dd>{display(finding.measured)} {finding.unit || ""}</dd></div><div><dt>Expected</dt><dd>{display(finding.expected)} {finding.unit || ""}</dd></div></dl>}{finding.object_ids?.length > 0 && <details><summary>{finding.object_ids.length} affected object{finding.object_ids.length === 1 ? "" : "s"}</summary><p className={styles.objectIDs}>{finding.object_ids.slice(0, 100).join(", ")}{finding.object_ids.length > 100 ? " … Full object list is in the JSON report." : ""}</p></details>}</article>)}</div></div></div><div className={`${styles.panel} ${styles.reportLimits}`}><h3>Limits of this inspection</h3><ul className={styles.limitations}>{report.limitations.map(item => <li key={item}>{item}</li>)}</ul><p className={styles.micro}>A supported-check result is not print certification. Confirm the supplier’s requirements and review a physical proof before production.</p></div></> : <div className={`${styles.panel} ${styles.empty}`}><span aria-hidden="true">◎</span><h2>Every finding needs evidence.</h2><p>Run a preflight or open a saved report to see measurements, affected object IDs and the checks that still need a specification or human review.</p><button type="button" className={styles.primary} onClick={() => switchTab("preflight")}>Choose artwork →</button></div>}
    </section>
    <footer className={styles.footer}><span>PRESSCHECK™ · BUILT BY MAGNUS</span><span>CREATE → MEASURE → PROOF → PRODUCE</span></footer>
  </main>;
}
