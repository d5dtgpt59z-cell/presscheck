import type { Metadata } from "next";
import ProductionWorkspace from "./ProductionWorkspace";

export const metadata: Metadata = {
  title: "Production Studio — PRESSCHECK™",
  description: "SVG artwork preflight, explicit printer specifications and editable structural prototypes. Built by Magnus.",
};

export default function ProductionPage() {
  return <ProductionWorkspace />;
}
