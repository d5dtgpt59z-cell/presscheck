# Demo walkthrough

1. Start the application. Open `http://127.0.0.1:3000`.
2. In **Try an example**, choose **Card pack**. The app ingests both source files; no result is precomputed.
3. Press **2 · Run inspection**. The API queues the job, the worker measures it, and the console polls persisted state.
4. Choose a finding. Inspect its highlighted bounds, switch A/B or Overlay, and adjust the split slider. The card pack has intentionally removed legal copy, a shifted M78 mark and a missing spacecraft element.
5. Download PDF and JSON. Review the original hashes, measured color/geometry, registration support and uncertainty notes. Refresh the browser to confirm the same completed result returns.
6. Try **Product label** for substituted product copy, a changed planetary color and a different encoded barcode. Try **Toy package** for clipping, text outside the safe zone and removed artwork.
7. To test a clean pair, check **Use matching files (no defects)** in Example artwork, load a sample and run again. A PASS is limited to supported checks, never production approval.
8. Upload `dataset/multipage.pdf` and `dataset/cmyk.tiff` to explore page selection and color warnings. Trim and color sample coordinates refer to the normalized preview, so edit them when using unrelated files.

No AI key is required. Advanced inspection settings expose optional vision interpretation; do not enable it for private artwork unless you intend transmission to the configured provider.
