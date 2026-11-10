import "./globals.css";
export const metadata = {
  title: "PRESSCHECK™ — Optical Systems Division",
  description: "Packaging preflight and visual inspection. Built by Magnus.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
