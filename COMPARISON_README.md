# ALO extension comparison

`extension_comparison.py` visits each URL once with the actual unpacked ALO browser extension and once without it. It does not imitate the extension by blocking images or other page resources.

## Requirements

- Python packages in `requirements.txt`, including Playwright
- Chromium installed for Playwright (`python -m playwright install chromium`)
- A headed desktop session; Chromium does not load unpacked extensions in headless mode

## Run

From this directory:

```bash
python extension_comparison.py
```

The default list is `test_urls.txt` (30 sites). Supply another file with one HTTP(S) URL per line, up to 50 unique URLs:

```bash
python extension_comparison.py --urls my_sites.txt
```

The runner randomizes which condition runs first for each site, uses a fresh browser profile for each visit, and continues past site errors. Options:

```text
--extension PATH   Unpacked extension directory (default: extension/)
--output PATH      Per-site paired CSV (default: extension_comparison_results.csv)
--summary PATH     Aggregate Markdown report (default: extension_comparison_summary.md)
--max-load-time MS Stop a visit and omit the site if either condition exceeds this limit (default: 30000)
--chromium PATH    Chromium executable if Playwright cannot find one
--seed INT         Run-order random seed (default: 42)
```

The CSV includes load, DOMContentLoaded, FCP and LCP timings, response/request counts, and transferred-byte estimates from response `Content-Length` headers for both conditions, plus the per-site differences. If either visit fails or does not finish loading before the limit, that site is skipped and omitted from both result files; the Markdown summary reports how many sites were skipped. The report summarizes paired successes, mean and median savings, percentage saved, bytes saved, and every included site. A positive `load_saved_ms` means the extension run was faster. Responses without a `Content-Length` header are omitted from the byte estimate so the script never waits on a streaming response body.

## View the results in a webpage

Start the ALO API/dashboard, then open `http://localhost:8000/comparison.html`. The page loads the latest `extension_comparison_results.csv`, shows a summary and searchable per-site table, and has a refresh button. You can also use **Open CSV** to view a CSV file directly.

Each condition is a single visit, so use the results as a first comparison rather than a controlled benchmark. Network, consent dialogs, personalization, and server-side variation can affect timings. Repeating the run with a different `--seed` changes condition order; repeat measurements before drawing conclusions.
