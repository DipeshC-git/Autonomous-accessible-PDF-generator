
"""
PROMPT_0: MULTI-MODAL OCR EXTRACTION
Edge Case Fix: Forces literal transcription and mathematical alignment to mitigate broken font encodings.
"""
PROMPT_0 = """You are an advanced multi-modal OCR engine. Process the provided raw document image page. 
Extract all visible text with 100% literal accuracy, preserving specialized characters, mathematical equations, and numeric structures.
For every block, line, or explicit word, calculate and output its normalized bounding box coordinates (Xmin, Ymin, Xmax, Ymax) scaled from 0 to 1000.
If text is distorted, vertically aligned, or displays broken encodings, reconstruct the correct characters contextually.
Output format: Strict raw JSON stream containing an array of tokens with text and bounding_box parameters. Do not wrap in markdown code blocks."""

"""
PROMPT_1: STRUCTURAL LAYOUT ANALYSIS
Edge Case Fix: Forces explicit handling of multi-column layouts, sidebars, and callouts to preserve reading order.
"""
PROMPT_1 = """Analyze the provided PDF page image alongside the raw OCR coordinates from Step 0.
Identify and map the physical layout zones. Classify each zone into: Header, Footer, Paragraph, Heading (1-6), List, Table, Image, Sidebar, or Decorative.
CRITICAL FOR READING ORDER: If a page has two columns, ensure the zones are ordered down the first column completely before starting the second column. Do not read horizontally across columns.
Output a structured JSON blueprint mapping zone IDs to their type and sequential reading position indices."""

"""
PROMPT_2: TEXT TO ZONE ANCHORING & UNICODE REPAIR
Edge Case Fix: Structural recovery of text streams within multi-column grids.
"""
PROMPT_2 = """Using the layout blueprint from Step 1, map the raw text fragments into their assigned structural zone IDs.
Verify that sentences breaking across columns or lines are stitched together natively without artificial hyphenations or line breaks.
Repair all corrupted unicode artifacts so text strings are fully searchable and readable by text-to-speech software.
Output format: JSON mapping zone IDs to clean, compiled string text."""

"""
PROMPT_3: SEMANTIC TAG TREE ARCHITECTURE
Edge Case Fix: Rejects invalid heading loops or skipped hierarchy paths using tracking context.
"""
PROMPT_3 = """Act as a certified PDF/UA compliance engineer. Take the ordered zone text from Step 2.
Review the global heading history context: {global_context}. 
Generate a valid PDF Tag Tree structure applying semantic tags: <H1> through <H6>, <P>, <L>, <LI>, and <Link>.
CRITICAL: Do not skip heading levels (e.g., if the previous page ended on H2, do not start this page with H4 unless an H3 is established). If an orphan title is found, logically group it based on the document layout history.
Mark repeating running headers and footers explicitly as <Artifact> so assistive tools bypass them."""

"""
PROMPT_4: CONTEXTUAL IMAGE TAGGING (ALT-TEXT)
Edge Case Fix: Prevents generic descriptions by reading adjacent text elements.
"""
PROMPT_4 = """Analyze all image assets on this page. Review the surrounding textual content from Step 2 to establish deep contextual relevance.
Write highly descriptive alternative text (<Alt-Text>) optimized for accessibility. If an image contains a chart or graph, summarize the key data trends within the description.
If an image is a spacer, logo background, or line decoration, mark it explicitly as Artifact=True.
Append these structural properties directly into the existing Tag Tree JSON."""

"""
PROMPT_5: COMPLEX TABLE MATRIX PARSING
Edge Case Fix: Handles spanning cells, merged columns, and table multi-page overflows.
"""
PROMPT_5 = """Isolate elements tagged as a Table. Examine the historical context: {global_context}.
Check if this table is a continuation of a table from a previous page. If yes, map this table's structure using the same column configurations.
Accurately parse complex multi-dimensional table headers (<TH>) and map data cells (<TD>) to their parent headers.
Handle ColSpan and RowSpan properties for split or merged cells explicitly. Never leave an isolated, unmapped cell.
Merge this comprehensive data grid directly into the master Tag Tree structure."""

"""
PROMPT_6: QUALITY ASSURANCE & PDF/UA VALIDATION
Edge Case Fix: Self-correcting programmatic validation pass to guarantee a legal compliance layout.
"""
PROMPT_6 = """Act as an automated accessibility auditor. Run a final compliance pass over the completed Tag Tree against WCAG 2.2 and PDF/UA specifications.
Check and fix the following programmatic violations:
1. Are there any empty tags or images missing Alt-Text attributes?
2. Are tables missing headers or scope declarations?
3. Is the sequence of headings structurally fractured?
Correct any anomalies natively within the tree object and output the absolute finalized, compliant JSON structure."""
