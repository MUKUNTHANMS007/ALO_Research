#!/usr/bin/env python3
"""Paired ALO browser extension comparison for a URL list.

Each site is visited once in a clean Chromium profile without ALO and once with
ALO loaded as an unpacked extension. Run headed because Chromium disables unpacked
extensions in headless mode. Results are paired by URL.
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

HERE = Path(__file__).resolve().parent
DEFAULT_URLS = HERE / "test_urls.txt"
DEFAULT_EXTENSION = HERE / "extension"
CSV_FIELDS = [
    "url", "run_order", "baseline_status", "baseline_error", "baseline_load_ms",
    "baseline_dcl_ms", "baseline_fcp_ms", "baseline_lcp_ms", "baseline_bytes",
    "baseline_requests", "extension_status", "extension_error", "extension_load_ms",
    "extension_dcl_ms", "extension_fcp_ms", "extension_lcp_ms", "extension_bytes",
    "extension_requests", "load_saved_ms", "load_saved_pct", "bytes_saved",
]


def load_urls(path: Path) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not line.startswith(("https://", "http://")):
            line = "https://" + line
        parsed = urlparse(line)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid URL in {path}: {raw}")
        if line not in seen:
            seen.add(line)
            urls.append(line)
    if not urls:
        raise ValueError(f"No URLs found in {path}")
    if len(urls) > 50:
        raise ValueError(f"Found {len(urls)} unique URLs; the limit is 50")
    return urls


def measure(page: Page, url: str, load_limit_ms: int) -> dict[str, Any]:
    responses: list[Any] = []
    failed: list[str] = []
    page.on("response", lambda response: responses.append(response))
    page.on("requestfailed", lambda request: failed.append(request.url))
    result: dict[str, Any] = {"status": "ok", "error": ""}
    try:
        page.goto(url, wait_until="load", timeout=load_limit_ms)
        # Let the extension's post-load report/stat collection run and allow LCP to settle.
        page.wait_for_timeout(1800)
        result.update(page.evaluate("""() => {
          const nav = performance.getEntriesByType('navigation')[0];
          const paint = name => performance.getEntriesByName(name)[0]?.startTime ?? null;
          const lcpEntries = performance.getEntriesByType('largest-contentful-paint');
          return {
            load_ms: nav?.loadEventEnd || null,
            dcl_ms: nav?.domContentLoadedEventEnd || null,
            fcp_ms: paint('first-contentful-paint'),
            lcp_ms: lcpEntries.at(-1)?.startTime ?? null
          };
        }"""))
    except PlaywrightTimeoutError as exc:
        result.update(status="timeout", error=str(exc).splitlines()[0][:300])
    except Exception as exc:  # retain a result row and continue to other sites
        result.update(status="error", error=str(exc).splitlines()[0][:300])
    total_bytes = 0
    for response in responses:
        try:
            length = response.headers.get("content-length")
            if length:
                total_bytes += int(length)
        except Exception:
            # Response bodies may be streamed indefinitely or never finish.
            # Do not read them here: page navigation timeouts do not cover body().
            pass
    result.update(bytes=total_bytes, requests=len(responses), failed_requests=len(failed))
    return result


def run_condition(playwright: Any, url: str, extension: bool, extension_dir: Path,
                  load_limit_ms: int, chromium: str | None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="alo-compare-") as profile:
        args: list[str] = []
        if extension:
            args += [f"--disable-extensions-except={extension_dir}", f"--load-extension={extension_dir}"]
        context: BrowserContext | None = None
        try:
            launch_args: dict[str, Any] = {
                "user_data_dir": profile, "headless": False, "args": args,
                "viewport": {"width": 1365, "height": 768},
            }
            if chromium:
                launch_args["executable_path"] = chromium
            context = playwright.chromium.launch_persistent_context(**launch_args)
            page = context.pages[0] if context.pages else context.new_page()
            return measure(page, url, load_limit_ms)
        except Exception as exc:
            return {"status": "error", "error": str(exc).splitlines()[0][:300],
                    "load_ms": None, "dcl_ms": None, "fcp_ms": None, "lcp_ms": None,
                    "bytes": 0, "requests": 0}
        finally:
            if context:
                context.close()


def run(urls: list[str], extension_dir: Path, timeout_ms: int,
        output_csv: Path, summary_path: Path, chromium: str | None, seed: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    rng = random.Random(seed)
    with sync_playwright() as p:
        for index, url in enumerate(urls, start=1):
            extension_first = bool(rng.getrandbits(1))
            print(f"[{index}/{len(urls)}] {url}", flush=True)
            conditions = (["extension", "baseline"] if extension_first else ["baseline", "extension"])
            measured: dict[str, dict[str, Any]] = {}
            for condition in conditions:
                print(f"  {condition}...", flush=True)
                measured[condition] = run_condition(
                    p, url, condition == "extension", extension_dir, timeout_ms, chromium)
                m = measured[condition]
                print(f"  {m['status']}: load={m.get('load_ms')} ms, bytes={m.get('bytes', 0)}", flush=True)
                time.sleep(0.5)
            base, alo = measured["baseline"], measured["extension"]
            bload, aload = base.get("load_ms"), alo.get("load_ms")
            bad_condition = next((name for name, result in measured.items()
                                  if result["status"] != "ok" or result.get("load_ms") is None
                                  or result["load_ms"] > timeout_ms), None)
            if bad_condition:
                skipped += 1
                print(f"  Skipped: {bad_condition} did not finish within {timeout_ms / 1000:g}s; excluded from results.",
                      flush=True)
                write_csv(rows, output_csv)
                continue
            saved = (bload - aload) if bload is not None and aload is not None else None
            row = {
                "url": url, "run_order": ">".join(conditions),
                "baseline_status": base["status"], "baseline_error": base.get("error", ""),
                "baseline_load_ms": bload, "baseline_dcl_ms": base.get("dcl_ms"),
                "baseline_fcp_ms": base.get("fcp_ms"), "baseline_lcp_ms": base.get("lcp_ms"),
                "baseline_bytes": base.get("bytes", 0), "baseline_requests": base.get("requests", 0),
                "extension_status": alo["status"], "extension_error": alo.get("error", ""),
                "extension_load_ms": aload, "extension_dcl_ms": alo.get("dcl_ms"),
                "extension_fcp_ms": alo.get("fcp_ms"), "extension_lcp_ms": alo.get("lcp_ms"),
                "extension_bytes": alo.get("bytes", 0), "extension_requests": alo.get("requests", 0),
                "load_saved_ms": saved,
                "load_saved_pct": (saved / bload * 100) if saved is not None and bload else None,
                "bytes_saved": base.get("bytes", 0) - alo.get("bytes", 0),
            }
            rows.append(row)
            # Keep partial results safe if the run is interrupted.
            write_csv(rows, output_csv)
    write_summary(rows, summary_path, seed, skipped)
    return rows


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def fmt_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.0f} ms"


def write_summary(rows: list[dict[str, Any]], path: Path, seed: int, skipped: int = 0) -> None:
    paired = [r for r in rows if r["load_saved_ms"] is not None]
    wins = sum(r["load_saved_ms"] > 0 for r in paired)
    deltas = [r["load_saved_ms"] for r in paired]
    pct = [r["load_saved_pct"] for r in paired if r["load_saved_pct"] is not None]
    byte_delta = [r["bytes_saved"] for r in paired]
    avg = statistics.mean(deltas) if deltas else None
    median = statistics.median(deltas) if deltas else None
    avg_pct = statistics.mean(pct) if pct else None
    avg_bytes = statistics.mean(byte_delta) if byte_delta else None
    lines = [
        "# ALO extension comparison", "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"Sites included: {len(rows)}; sites skipped for errors or exceeding the load limit: {skipped}; order seed: {seed}", "",
        "Each site was visited once in a fresh Chromium profile with the unpacked ALO extension and once without it.",
        "Positive savings means the extension run was faster. This is a single-run comparison; timings vary with network and site state.", "",
        "## Summary", "",
        "| Measure | Result |", "|---|---:|",
        f"| Sites faster with ALO | {wins}/{len(paired)} |",
        f"| Mean load time saved | {fmt_ms(avg)} |",
        f"| Median load time saved | {fmt_ms(median)} |",
        f"| Mean percentage saved | {('n/a' if avg_pct is None else f'{avg_pct:.1f}%')} |",
        f"| Mean transferred bytes saved | {('n/a' if avg_bytes is None else f'{avg_bytes / 1024:,.1f} KB')} |", "",
        "## Per-site results", "",
        "| Site | Baseline load | ALO load | Saved | Saved % | Bytes saved | Status |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        site = urlparse(row["url"]).netloc + urlparse(row["url"]).path
        status = "paired" if row["load_saved_ms"] is not None else f"{row['baseline_status']}/{row['extension_status']}"
        saved_pct = row["load_saved_pct"]
        lines.append(
            f"| {site} | {fmt_ms(row['baseline_load_ms'])} | {fmt_ms(row['extension_load_ms'])} | "
            f"{fmt_ms(row['load_saved_ms'])} | {('n/a' if saved_pct is None else f'{saved_pct:.1f}%')} | "
            f"{row['bytes_saved'] / 1024:,.1f} KB | {status} |"
        )
    lines += ["", "## Measurement notes", "",
              "- The extension is loaded from the `extension/` folder using Chromium's unpacked-extension flags.",
              "- Fresh profiles keep cookies and cache from one condition out of the other.",
              "- Requests and transferred bytes are estimated from response Content-Length (or response bodies when available).",
              "- The comparison measures the extension's actual page behavior; it does not simulate or force-block resources.",
              "- Compare FCP/LCP alongside full load time. A single visit per condition is directional, not a controlled benchmark.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", type=Path, default=DEFAULT_URLS, help="Text file with up to 50 URLs, one per line")
    parser.add_argument("--extension", type=Path, default=DEFAULT_EXTENSION, help="Unpacked ALO extension folder")
    parser.add_argument("--output", type=Path, default=HERE / "extension_comparison_results.csv")
    parser.add_argument("--summary", type=Path, default=HERE / "extension_comparison_summary.md")
    parser.add_argument("--max-load-time", "--timeout", dest="timeout", type=int, default=30000,
                        help="Stop a visit and skip its site if either condition takes longer (milliseconds; default: 30000)")
    parser.add_argument("--chromium", help="Optional Chromium executable path")
    parser.add_argument("--seed", type=int, default=42, help="Seed for balanced randomized run order")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--max-load-time must be greater than 0")
    urls = load_urls(args.urls)
    if not (args.extension / "manifest.json").is_file():
        parser.error(f"No manifest.json in extension folder: {args.extension}")
    print(f"Loaded {len(urls)} unique URLs. Each will be visited in both conditions (headed Chromium).")
    rows = run(urls, args.extension.resolve(), args.timeout, args.output, args.summary, args.chromium, args.seed)
    paired = [r for r in rows if r["load_saved_ms"] is not None]
    print(f"Completed: {len(paired)}/{len(rows)} paired loads. CSV: {args.output}; summary: {args.summary}")


if __name__ == "__main__":
    main()
