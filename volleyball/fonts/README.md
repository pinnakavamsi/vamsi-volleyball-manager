# PDF export fonts

Unmodified Noto Sans Regular and Bold static TrueType fonts are bundled for server-side PDF exports. They come from the Noto project's [official font builds](https://github.com/notofonts/notofonts.github.io/tree/main/fonts/NotoSans/hinted/ttf) and are distributed under the [SIL Open Font License](https://github.com/notofonts/latin-greek-cyrillic/blob/main/OFL.txt); the complete license is retained in `OFL.txt`.

`provenance.json` records the exact source URLs, downloaded byte counts and SHA256 checksums. The application embeds subsets into generated PDFs. Coverage regressions include extended Latin, Greek and Cyrillic. The ranking infinity symbol uses the PDF standard Symbol font through trusted markup inserted after escaping user text. This does not establish universal script coverage or complex-script shaping; additional scripts require a suitable font/shaping review.
