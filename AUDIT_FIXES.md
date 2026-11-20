data-cleaner-api fix checklist

P0: bugs, crashes, data loss

\[ ] 1. Dedupe merges rows with missing keys

Severity: P0

Where: app/services/csv\_cleaner.py::dedupe; tests/services/test\_csv\_cleaner.py::test\_dedupe\_null\_keys\_are\_handled\_and\_counted

Issue: Null and blank/whitespace keys collapse into one row, and the loss is reported as duplicates\_removed. The test asserts removed == 2, so it enshrines the bug.

Fix / Workaround: Blank the key to null, then filter with key.is\_null() | key.is\_first\_distinct(). Update the test to expect all missing-key rows to be kept.

\[ ] 2. Dedupe lowercases every key

Severity: P0

Where: csv\_cleaner.py::dedupe; app/schemas/clean.py::CleanOptions

Issue: Case-sensitive IDs (e.g. Salesforce 15-char) that differ only by case are merged, and the second record is dropped.

Fix / Workaround: Add dedupe\_ignore\_case: bool = False and only lowercase when it’s true. Note the changed default in your changelog.

\[ ] 3. rows\_in is derived from the frame, so reconciliation can’t fail; blank lines create phantom rows

Severity: P0

Where: csv\_cleaner.py::\_filter\_malformed\_rows, read\_csv\_bytes\_with\_quarantine, clean\_csv\_bytes

Issue: rows\_in = frame.height + len(quarantined) balances even if Polars drops or invents rows. a,b\\n1,2\\n\\n\\n\\n yields 3 all-null rows in the output.

Fix / Workaround: Count rows\_in from csv.reader records. Skip blank lines and report them as blank\_rows\_skipped. Raise CsvReadError if frame.height != records - quarantined - blank.

\[ ] 4. CPU-bound work blocks the event loop

Severity: P0

Where: app/routers/clean.py::clean\_csv; app/routers/validate.py::validate\_json

Issue: Both async def endpoints run sync work inline. /health took 2.85 s (vs 0.03 s idle) during a 24 MB clean.

Fix / Workaround: Wrap the call: await run\_in\_threadpool(clean\_csv\_bytes, data, options). Change validate\_json to a plain def.

\[ ] 5. Semicolon/tab-delimited CSVs parse as one column with HTTP 200

Severity: P0

Where: csv\_cleaner.py::\_filter\_malformed\_rows (csv.reader default delimiter), read\_csv\_bytes\_with\_quarantine (pl.read\_csv)

Issue: id;email;joined becomes a single column and the only signal is unknown\_columns\_ignored. The cleaning options silently do nothing.

Fix / Workaround: Add a delimiter option, or sniff over ",;\\t|". Return 400 when a one-column header contains ; or a tab.

\[ ] 6. Memory is roughly 20x the file size

Severity: P0

Where: clean.py::read\_upload\_limited, csv\_cleaner.py::\_filter\_malformed\_rows, clean.py::clean\_csv (to\_dicts() → Pydantic → JSON)

Issue: A 24 MB file peaked at about 558 MB RSS. Chunk list, joined bytes, decoded str, a Python list per row, the rewritten CSV, to\_dicts() and the Pydantic copy all coexist.

Fix / Workaround: Return Response(frame.write\_json(), media\_type="application/json") instead of to\_dicts() plus the response model. Run del on intermediates, or validate row shape with Polars (pl.col(...).str.count\_matches) instead of materialising every row in Python.

\[ ] 7. Dates are parsed per cell, so one column can mix day-first and month-first

Severity: P0

Where: app/utils/dates.py::DATE\_FORMATS; csv\_cleaner.py::\_date\_exprs, normalize\_dates

Issue: 13/05/2024 is read as d/m while 05/13/2024 is read as m/d. Ambiguous values are always day-first, then counted as dates\_normalized.

Fix / Workaround: Pick one format per column (the one that parses the most non-null cells). Add a date\_dayfirst option and an ambiguous\_dates counter.

\[ ] 8. OverflowError returns a 500 on huge JSON integers

Severity: P0

Where: app/services/json\_validator.py::\_check\_type (float(value))

Issue: {"n": 10\*\*400} with type: integer raises an unhandled OverflowError.

Fix / Workaround: Handle int values without float(), and catch (ValueError, OverflowError) around the remaining conversions.

\[ ] 9. Quadratic email regex and no input caps on /validate/json

Severity: P0

Where: json\_validator.py::EMAIL\_RE; app/schemas/validate.py::ValidateRequest.records; app/routers/validate.py::validate\_json

Issue: About 20k characters of dots took 2.9 s per record, and nothing limits record count or body size. Combined with #4, one request freezes the server.

Fix / Workaround: Return False when len(s) > 254 before the regex. Add Field(max\_length=...) on records and enforce a body limit at the proxy or in middleware.

P1: edge cases, quality, workarounds

\[ ] 10. Latin-1 fallback mis-decodes Windows (cp1252) files

Severity: P1

Where: csv\_cleaner.py::decode\_csv\_bytes

Issue: Latin-1 never fails, so smart quotes come out as \\x93hello\\x94 with no error.

Fix / Workaround: Try cp1252 before latin-1.

\[ ] 11. Header whitespace silently defeats options

Severity: P1

Where: csv\_cleaner.py::read\_csv\_bytes\_with\_quarantine; normalize\_emails/normalize\_dates/dedupe column lookups

Issue: A header of " email" doesn’t match email, so the option is ignored with only a count to show for it.

Fix / Workaround: Strip header names right after reading with frame.rename({c: c.strip() for c in frame.columns}).

\[ ] 12. Duplicate headers are silently renamed

Severity: P1

Where: csv\_cleaner.py::read\_csv\_bytes\_with\_quarantine

Issue: a,a,b becomes a, a\_duplicated\_0, b with no warning.

Fix / Workaround: Detect duplicate headers in \_filter\_malformed\_rows and return 400, or report them in the summary.

\[ ] 13. Repeated query params keep only the last value

Severity: P1

Where: app/routers/clean.py::\_pick, \_resolve\_options (query.get)

Issue: ?date\_columns=dob\&date\_columns=signup normalizes only signup, and dob is dropped without any warning.

Fix / Workaround: Use query.getlist(name) for list options, or remove query-string input entirely and accept form fields only.

\[ ] 14. FieldRule.nullable is never read

Severity: P1

Where: app/schemas/validate.py::FieldRule; json\_validator.py::validate\_records; tests/services/test\_json\_validator.py::test\_validate\_nullable\_allows\_null\_and\_empty

Issue: required=True, nullable=True still errors on null. The test passes only because required defaults to False.

Fix / Workaround: In the empty-value branch, skip the required error when rule.nullable and the value is None. Or delete the field. Make the test use required=True.

\[ ] 15. Integer/number check accepts "1\_000" and "1e3"

Severity: P1

Where: json\_validator.py::\_check\_type

Issue: Python float() accepts underscores and exponent notation, so these strings pass as integer. Records are also returned uncoerced.

Fix / Workaround: Pre-check strings with a regex like ^\[+-]?\\d+(\\.\\d+)?$. Document that the validator doesn’t coerce values.

\[ ] 16. Validator and cleaner disagree on dates

Severity: P1

Where: dates.py::parse\_date\_string (fromisoformat fallback) vs csv\_cleaner.py::\_date\_exprs

Issue: 2024-01-15T10:30:00+05:30 and fractional-second timestamps are valid in /validate/json but “unparseable” in /clean/csv.

Fix / Workaround: Share one format list, or drop the fromisoformat fallback. Remove the unreachable date.fromisoformat block while you’re there.

\[ ] 17. CSV mode hides which rows were quarantined

Severity: P1

Where: clean.py::clean\_csv (output\_format == "csv" branch)

Issue: The caller gets only an X-Quarantined-Rows count, so bad rows can’t be recovered.

Fix / Workaround: Add a GET/second-part response or a zip with quarantined.csv, or tell CSV users to request JSON mode to see quarantined rows.

\[ ] 18. Inconsistent summary contract across endpoints

Severity: P1

Where: app/schemas/common.py::SummaryReport; app/schemas/validate.py::ValidateSummary; json\_validator.py::validate\_records; csv\_cleaner.py::clean\_csv\_bytes

Issue: rows\_dropped excludes quarantined rows in /clean but includes them in /validate. Ignored column names are discarded in JSON mode, and fixes\_applied mixes fixes with warnings and conditional keys.

Fix / Workaround: Define one invariant for both endpoints. Move malformed\_rows and unknown\_columns\_ignored into a separate warnings field with column names, and always emit the same keys.

\[ ] 19. rows\_in from a Content-Length-less cap check is misleading; the 413 guard doesn’t prevent buffering

Severity: P1

Where: clean.py::read\_upload\_limited (docstring claims “before buffering”)

Issue: Starlette has already parsed and spooled the whole multipart body before your handler runs, so the cap protects only handler memory.

Fix / Workaround: Check file.size first, and enforce the real limit at the proxy or in middleware on Content-Length. Fix the docstring.

\[ ] 20. CSV formula injection in output

Severity: P1

Where: csv\_cleaner.py::render\_csv

Issue: Cells starting with =, +, - or @ run as formulas when the output is opened in Excel.

Fix / Workaround: Prefix such cells with ' in CSV output only, ideally behind an option so JSON mode and data fidelity are unchanged.

\[ ] 21. Test environment drift and an unnecessary workaround

Severity: P1

Where: requirements.txt (httpx==0.28.1); HANDOFF.md (“Known bugs” #1 cites httpx2); tests/routers/test\_clean.py::test\_clean\_list\_options\_via\_repeated\_form\_fields

Issue: The Starlette TestClient warns that httpx is deprecated in favour of httpx2, while the handoff claims httpx2 is installed. The raw-multipart test is unnecessary: data={"date\_columns": \[...]} returns 200 with the pinned stack.

Fix / Workaround: Pick one client (pin httpx2, or stay on httpx and ignore the warning), then replace the raw-multipart test with the dict-of-lists form. Correct HANDOFF.md

