**The discipline, and where it leaked.** Only tune documents were opened, dumped and debugged. The first full run
(the baseline) printed the class and reason codes of the holdout documents along with the tune ones, so I did see
that level of detail for them. No holdout document was opened or debugged, and no change was made in order to fix
one. The two holdout documents that still fail were left exactly as they are.

Every change below fixes something a tune document showed, and is written as a general rule, not as a patch for
that document. Each has tests in `tests/test_robustness_fixes.py` or `tests/test_straighten.py`.

| What the reader does now | Tune documents that showed the problem |
|---|---|
| A tax line printed as a ledger row inside the item grid ("Output CGST 9%", "Add : IGST @ 18%") is a tax line, not an item. | all Tally-style documents |
| The amount in words is found under its label as well as beside it, and a sentence that wraps is followed to its end. | Tally-style, Zoho-style |
| Characters the layout library cannot decode (Chrome embeds some web-font glyphs with no usable character map, so "INV-000482" arrived as "INV?000482") are read again from the PDF engine's own text for the same rectangle. A word that still cannot be decoded is marked and never trusted. | all Zoho-style documents |
| An invoice number printed under the title with only "#" in front of it is read. | all Zoho-style documents |
| When a page prints two totals ("Total", the taxes, then "G. Total"), the arithmetic picks the invoice total: the lines plus the taxes. A total under a label the reader does not know is accepted only on that arithmetic. A known total label that does not add up is still reported as printed, so a vendor's mistake stays visible. | spreadsheet-made bills |
| A label that runs into its value because its cell was too narrow ("NoPO-2026-0512") is split. An identifier after a colon is no longer reported as "corrected". | spreadsheet-made bills |
| On a text page, short tokens at the start of a description ("A4", "75") stay in the description. | spreadsheet-made bills |
| Two invoice lines may bill the same PO line (one item, two deliveries) on a near-exact wording match (`matching.line_sharing_min`), and their quantities are added up before the PO check, in the pipeline and again at approval. | `tally_06` |
| A photographed page is straightened before OCR (`invoice_desk/straighten.py`): the picture is cropped out of the PDF page, the sheet is found on its background and its perspective undone, uneven light is divided out, a page on its side is turned, ruled lines are whitened and white-on-black table headings are inverted. A flatbed scan gets none of this and reads exactly as before. | all seven tune photographs |
| A photograph is read twice in two different ways (the engine's own page layout; then enlarged 1.4x in sparse-text mode), so the two reads share fewer mistakes. The first read is used when both pass the same checks. | tune photographs |
| On a scan, when the printed total cannot be used as read (written by hand, or misread) but the lines plus the taxes and the amount in words give the same amount, that amount is the total. A "Total" row that only repeats the tax (the foot of an HSN summary) is not taken for the invoice total. | `photo_hand_total`, Tally-style photographs |
| A quantity that runs into its unit ("25Nos"), a rupee sign the OCR engine printed as another character ("%1,02,338.45"), and a vendor name split in two where the engine dropped the "&" are read correctly. | tune photographs |
| Stricter than before: an invoice number or an invoice date found in only one of the two reads of a scan is no longer trusted. Nothing else on an invoice can confirm either. | a design decision, not a failure |
| The font warnings of the PDF library are no longer printed. | Chrome-made PDFs |

**What it cost elsewhere.** The older scorecard (`docs/extraction_scorecard.md`, 296 layouts from the samples'
own generator) was run again after these changes: text PDFs 200 of 200 straight through, as before; scanned
layouts 86 of 96, with no misread approved, against 88 of 96 before. The two that moved are correct reads now held
because their invoice number or date was found in only one of the two reads (the stricter rule in the last-but-one
row). That is the price of the rule, and I think it is the right way round.

**Tune documents that still do not reach the right answer.** `photo_tally_02`: one read of the invoice number
"SSI/2026/0463" came out as "SS1/2026/0463"; the two reads differ, so the invoice is held for a person to confirm
the number. `photo_zoho_01`: one read of the date "12/11/2026" came out as "12/1/2026"; the reads differ and the
invoice is held. Both are the two-read check doing its job: the page was not misread silently. Neither can be
settled from the invoice alone, and I did not add a rule to guess.
