# Report PDF visual review

On October 5, 2026, a clearly labeled synthetic standings report was generated through the application's production `render_report` function. It contains eight rows and twenty columns across four landscape A4 pages. No customer data or database reads were used. [Measured review details](report-pdf-review.json) retain the output checksum, exact dimensions and preview paths.

All four pages were rendered with macOS CoreGraphics at 1684 x 1191 pixels and visually inspected. Metadata/ranking policy, wrapped long names, literal escaped markup, São Paulo/Łódź accents, the infinity quotient, table borders, page numbers and exact application credit were readable. No clipping, overlaps or missing-glyph boxes remained in the reviewed output.

The review caught and fixed two defects: additional column pages repeated a nonunique division name; the former font lacked Polish ź. Wide tables now repeat their selected stable entry/match/assignment/audit/tournament identifier. A saved selection excluding IDs uses a consistent numbered Row column. Bundled unmodified Noto Sans regular/bold fonts include their complete OFL license and source/checksum provenance; the PDF standard Symbol font supplies infinity after user text is escaped. Pure regressions cover grouping/reordering/omitted IDs, text-glyph coverage and the safe symbol fallback.

The private local sample is `instance/validation/report-layout-validation.pdf`; all page previews are `instance/screenshots/report-layout-01.png` through `report-layout-04.png`. These are local validation artifacts. The review does not establish universal script/shaping support, all possible report volumes, printer/mobile PDF viewer behavior or assistive-technology accessibility.
