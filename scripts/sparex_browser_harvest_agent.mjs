import fs from "node:fs/promises";
import path from "node:path";

const DEFAULT_BASE_URL = "https://us.sparex.com";
const DEFAULT_HARVEST_DATE = "2026-07-25";

function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function slug(value) {
  return cleanText(value)
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function skuFromUrl(url) {
  const match = String(url || "").match(/-(\d+)\.html/i);
  return match ? `S.${match[1]}` : "";
}

function uniqueByHref(links) {
  const seen = new Set();
  const values = [];
  for (const link of links) {
    if (!link.href || seen.has(link.href)) continue;
    seen.add(link.href);
    values.push(link);
  }
  return values;
}

function detailSlug(value) {
  return cleanText(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function isCategoryLink(basePath, link) {
  return (
    link.text &&
    link.href.startsWith(`${DEFAULT_BASE_URL}/${basePath}`) &&
    link.href.endsWith(".html") &&
    !/-\d+\.html/i.test(link.href)
  );
}

function leafCategoryLinks(links) {
  return links.filter(
    (link) => !links.some((candidate) => candidate.href !== link.href && candidate.href.startsWith(`${link.href.replace(/\.html$/, "")}/`)),
  );
}

export function mapOdooCategory(url, text) {
  const href = String(url || "").toLowerCase();
  const label = cleanText(text).toLowerCase();

  if (href.includes("/hydraulics/hydraulic-hoses/")) return "Parts / Hydraulic / Hydraulic Hoses";
  if (href.includes("/hydraulics/hydraulic-cylinders/")) return "Parts / Hydraulic / Hydraulic Cylinders";
  if (href.includes("/hydraulics/hydraulic-couplings/")) return "Parts / Hydraulic / Hydraulic Couplers";
  if (href.includes("/hydraulics/fluid-connectors/") || href.includes("/hydraulics/metal-pipes/") || href.includes("/hydraulics/cutting-ring-")) {
    return "Parts / Hydraulic / Hydraulic Adapters";
  }
  if (href.includes("/hydraulics/hydraulic-pumps")) return "Parts / Hydraulic / Hydraulic Pumps";
  if (href.includes("/hydraulics/hydraulic-valves")) return "Parts / Hydraulic / Hydraulic Valves";
  if (href.includes("/hydraulics/")) return "Parts / Hydraulic";

  if (href.includes("/engine-filters/filters/engine-oil")) return "Parts / Filters / Engine Oil Filters";
  if (href.includes("/engine-filters/filters/engine-air")) return "Parts / Filters / Air Filters";
  if (href.includes("/engine-filters/filters/fuel")) return "Parts / Filters / Fuel Filters";
  if (href.includes("/engine-filters/filters/hydraulic")) return "Parts / Filters / Hydraulic Filters";
  if (href.includes("/engine-filters/filters/cab")) return "Parts / Filters / Cab Filters";
  if (href.includes("/engine-filters/filters/")) return "Parts / Filters";
  if (href.includes("/engine-filters/cooling-parts/")) return "Parts / Cooling";
  if (href.includes("/engine-filters/drive-belts")) return "Parts / Belts";
  if (href.includes("/engine-filters/engine-electrics") || href.includes("/engine-filters/starters-alternators") || href.includes("/electrics/")) {
    return "Parts / Electrical";
  }
  if (href.includes("/engine-filters/engine-parts/gaskets")) return "Parts / Engine / Gaskets";
  if (href.includes("/engine-filters/engine-parts/")) return "Parts / Engine";
  if (href.includes("/engine-filters/fuel-delivery")) return "Parts / Fuel System";
  if (href.includes("/engine-filters/seals/")) return "Parts / Seals";
  if (href.includes("/engine-filters/exhaust-parts/")) return "Parts / Exhaust";

  if (href.includes("/linkage/")) return "Parts / Linkage";
  if (href.includes("/pto-driveline-components/")) return "Parts / PTO";
  if (href.includes("/power-transmission/bearings")) return "Parts / Bearings";
  if (href.includes("/power-transmission/double-single-lip-oil-seals")) return "Parts / Seals";
  if (href.includes("/power-transmission/drive-belts")) return "Parts / Belts";
  if (href.includes("/power-transmission/roller-chain")) return "Parts / Driveline";
  if (href.includes("/power-transmission/")) return "Parts / Driveline";
  if (href.includes("/fasteners-hardware/")) return "Parts / Hardware";
  if (href.includes("/implements-parts/")) return "Parts / Implements";
  if (href.includes("/axles-transmission/")) return "Parts / Driveline";
  if (href.includes("/cab-sheet-metal/")) return "Parts / Cab";
  if (href.includes("/paint/") || label.includes("paint")) return "Parts / Paint";
  if (href.includes("/workshop-merchandising/")) return "Parts / Shop Supplies";

  return "Parts / Miscellaneous";
}

export async function discoverLeafCategories(browser, sectionUrl) {
  const tab = await browser.tabs.new();
  try {
    await tab.goto(sectionUrl).catch(() => {});
    await tab.playwright.waitForTimeout(3000);
    const basePath = new URL(sectionUrl).pathname.replace(/^\//, "").replace(/\.html$/, "");
    const links = await tab.playwright.evaluate(
      ({ baseUrl, basePathValue }) =>
        [...document.querySelectorAll("a[href]")]
          .map((a) => ({
            text: (a.innerText || a.title || "").replace(/\s+/g, " ").trim(),
            href: a.href.split("#")[0],
          }))
          .filter((link) => link.text && link.href.startsWith(`${baseUrl}/${basePathValue}`) && link.href.endsWith(".html") && !/-\d+\.html/i.test(link.href)),
      { baseUrl: DEFAULT_BASE_URL, basePathValue: basePath },
    );
    return leafCategoryLinks(uniqueByHref(links));
  } finally {
    await tab.close().catch(() => {});
  }
}

async function harvestListingPage(tab, category, pageUrl, harvestDate) {
  await tab.goto(pageUrl).catch(() => {});
  await tab.playwright.waitForTimeout(750);
  return tab.playwright.evaluate(
    ({ categoryName, odooCategory, harvestDateValue }) =>
      [...document.querySelectorAll("li.item.pm-listitem")]
        .map((li) => {
          const href = li.querySelector("h2.product-name a[href], a.product-image[href]")?.href || "";
          const sku =
            (li.querySelector(".thesku")?.innerText || "").replace(/\s+/g, " ").trim() ||
            (String(href || "").match(/-(\d+)\.html/i)?.[1] ? `S.${String(href).match(/-(\d+)\.html/i)[1]}` : "");
          const name =
            (li.querySelector("h2.product-name a")?.innerText || "").replace(/\s+/g, " ").trim() ||
            (li.querySelector("a.product-image")?.getAttribute("title") || "").replace(/\s+/g, " ").trim() ||
            sku;
          const note = (li.querySelector(".listscript")?.innerText || "").replace(/\s+/g, " ").trim();
          const image = li.querySelector("img.product-image-photo")?.src || li.querySelector("a.product-image")?.getAttribute("data-cdnimg") || "";
          return {
            source: {
              vendor: "Sparex",
              url: href,
              harvested_at: harvestDateValue,
              harvest_mode: "listing_skeleton",
            },
            product: {
              internal_reference: sku,
              name,
              category: odooCategory,
              manufacturer: "Sparex",
              vendor_code: sku,
              vendor_price: 0.0,
              lead_time_days: 1,
            },
            category_name: categoryName,
            short_description: note,
            image_url: image,
            enrichment_status: "Pending detail enrichment",
          };
        })
        .filter((record) => record.product.internal_reference && record.source.url),
    { categoryName: category.name, odooCategory: category.odooCategory, harvestDateValue: harvestDate },
  );
}

export async function harvestCategory(browser, category, options = {}) {
  const maxPages = options.maxPages || category.maxPages || 20;
  const harvestDate = options.harvestDate || DEFAULT_HARVEST_DATE;
  const tab = await browser.tabs.new();
  const records = [];
  const seen = new Set();
  try {
    for (let page = 1; page <= maxPages; page += 1) {
      const pageUrl = page === 1 ? category.url : `${category.url}${category.url.includes("?") ? "&" : "?"}p=${page}`;
      const pageRecords = await harvestListingPage(tab, category, pageUrl, harvestDate);
      let added = 0;
      for (const record of pageRecords) {
        const sku = record.product.internal_reference || skuFromUrl(record.source.url);
        if (!sku || seen.has(sku)) continue;
        seen.add(sku);
        records.push(record);
        added += 1;
      }
      if (pageRecords.length === 0 || added === 0) break;
    }
  } finally {
    await tab.close().catch(() => {});
  }
  return records;
}

export async function harvestProductDetail(browser, recordOrUrl, options = {}) {
  const url = typeof recordOrUrl === "string" ? recordOrUrl : recordOrUrl?.source?.url;
  if (!url) throw new Error("harvestProductDetail requires a product URL.");
  const harvestDate = options.harvestDate || DEFAULT_HARVEST_DATE;
  const tab = await browser.tabs.new();
  try {
    await tab.goto(url).catch(() => {});
    await tab.playwright.waitForTimeout(options.waitMs || 1500);
    return tab.playwright.evaluate(
      ({ sourceUrl, harvestDateValue }) => {
        const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
        const skuFromUrl = (value) => {
          const match = String(value || "").match(/-(\d+)\.html/i);
          return match ? `S.${match[1]}` : "";
        };
        const splitValues = (cell) => {
          const anchors = [...cell.querySelectorAll("a")].map((a) => clean(a.textContent)).filter(Boolean);
          if (anchors.length) return anchors;
          return clean(cell.textContent)
            .split(/\s*,\s*/)
            .map((item) => clean(item))
            .filter(Boolean);
        };
        const headingFor = (terms) =>
          [...document.querySelectorAll("h1,h2,h3,h4,legend,.std-title,.section-title")]
            .find((el) => terms.some((term) => clean(el.textContent).toLowerCase().includes(term)));
        const rowsAfterHeading = (terms) => {
          const heading = headingFor(terms);
          if (!heading) return [];
          const containers = [];
          let node = heading.parentElement;
          for (let depth = 0; node && depth < 4; depth += 1, node = node.parentElement) {
            containers.push(node);
          }
          for (const container of containers) {
            const rows = [...container.querySelectorAll("tr")];
            if (rows.length) return rows;
          }
          const rows = [];
          let sibling = heading.nextElementSibling;
          while (sibling && rows.length === 0) {
            rows.push(...sibling.querySelectorAll("tr"));
            sibling = sibling.nextElementSibling;
          }
          return rows;
        };
        const rowPairs = (rows) =>
          rows
            .map((row) => {
              const cells = [...row.querySelectorAll("th,td")];
              if (cells.length < 2) return null;
              return { key: clean(cells[0].textContent), cell: cells[1], value: clean(cells[1].textContent) };
            })
            .filter(Boolean);

        const productName =
          clean(document.querySelector("h1")?.textContent) ||
          clean(document.querySelector(".product-name")?.textContent) ||
          document.title;
        const sku =
          clean(document.querySelector(".thesku")?.textContent) ||
          skuFromUrl(sourceUrl);
        const manufacturer = "Sparex";

        const specPairs = rowPairs(rowsAfterHeading(["specification"]));
        const specifications = [];
        const alternate_barcodes = [];
        const related_parts = [];
        for (const pair of specPairs) {
          if (!pair.key || !pair.value) continue;
          specifications.push({
            group: "Specifications",
            name: pair.key,
            value: pair.value,
            source_name: "Sparex",
            source_url: sourceUrl,
          });
          if (pair.key.toLowerCase().includes("barcode")) {
            alternate_barcodes.push({
              barcode_type: pair.key.toLowerCase().includes("ean") ? "ean13" : "code128",
              barcode: pair.value,
              source_name: "Sparex",
              source_url: sourceUrl,
            });
          }
          if (pair.key.toLowerCase().includes("related product")) {
            for (const relatedSku of pair.value.match(/S\.\d+/gi) || []) {
              related_parts.push({
                internal_reference: relatedSku.toUpperCase(),
                relationship_type: "related",
                source_name: "Sparex",
                source_url: sourceUrl,
                confidence: 0.85,
              });
            }
          }
        }

        const fitments = [];
        for (const pair of rowPairs(rowsAfterHeading(["suitable for make", "suitable for"]))) {
          for (const model of splitValues(pair.cell)) {
            fitments.push({
              make: pair.key,
              model,
              source_name: "Sparex",
              source_url: sourceUrl,
              confidence: 0.9,
            });
          }
        }

        const oem_references = [];
        for (const pair of rowPairs(rowsAfterHeading(["oem part number", "oem part numbers"]))) {
          for (const partNumber of splitValues(pair.cell)) {
            oem_references.push({
              manufacturer: pair.key,
              oem_part_number: partNumber,
              reference_type: "oem",
              source_name: "Sparex",
              source_url: sourceUrl,
              confidence: 0.95,
            });
          }
        }

        const catalog_pages = [];
        for (const pair of rowPairs(rowsAfterHeading(["catalog page", "catalog pages"]))) {
          for (const pageNumber of splitValues(pair.cell)) {
            catalog_pages.push({
              catalog_name: pair.key,
              page_number: pageNumber,
              source_name: "Sparex",
              source_url: sourceUrl,
            });
          }
        }

        return {
          source: {
            vendor: "Sparex",
            url: sourceUrl,
            harvested_at: harvestDateValue,
            harvest_mode: "product_detail",
          },
          product: {
            internal_reference: sku,
            name: productName,
            manufacturer,
            vendor_code: sku,
          },
          specifications,
          fitments,
          oem_references,
          catalog_pages,
          alternate_barcodes,
          related_parts,
          enrichment_status: "Product detail harvested",
        };
      },
      { sourceUrl: url, harvestDateValue: harvestDate },
    );
  } finally {
    await tab.close().catch(() => {});
  }
}

export async function harvestProductDetails(browser, records, options = {}) {
  const concurrency = Math.max(1, Math.min(options.concurrency || 1, 4));
  const results = [];
  let nextIndex = 0;
  async function worker() {
    while (nextIndex < records.length) {
      const index = nextIndex;
      nextIndex += 1;
      const record = records[index];
      try {
        results[index] = await harvestProductDetail(browser, record, options);
      } catch (error) {
        results[index] = {
          source: record.source || { url: String(record) },
          product: record.product || {},
          error: error.message,
          enrichment_status: "Detail harvest failed",
        };
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, records.length) }, () => worker()));
  return results.filter(Boolean);
}

async function harvestCategoriesConcurrently(browser, categories, options) {
  const concurrency = Math.max(1, Math.min(options.concurrency || 1, 6));
  const results = [];
  let nextIndex = 0;

  async function worker() {
    while (nextIndex < categories.length) {
      const categoryIndex = nextIndex;
      nextIndex += 1;
      const category = categories[categoryIndex];
      const records = await harvestCategory(browser, category, {
        maxPages: category.maxPages || options.maxPages,
        harvestDate: options.harvestDate,
      });
      results[categoryIndex] = { category, records };
      options.log(`${category.name}: ${records.length}`);
    }
  }

  await Promise.all(Array.from({ length: Math.min(concurrency, categories.length) }, () => worker()));
  return results.filter(Boolean);
}

export async function runSparexHarvestAgent({ browser, targets, outDir, runName, maxPages = 20, harvestDate = DEFAULT_HARVEST_DATE, concurrency = 1, log = () => {} }) {
  if (!browser) throw new Error("runSparexHarvestAgent requires a browser binding.");
  if (!Array.isArray(targets) || targets.length === 0) throw new Error("At least one target is required.");

  await fs.mkdir(outDir, { recursive: true });
  const runSlug = slug(runName || `sparex_harvest_${Date.now()}`);
  const allRecords = [];
  const categorySummary = [];

  for (const target of targets) {
    const leaves = target.categories?.length
      ? target.categories
      : (await discoverLeafCategories(browser, target.url)).map((leaf) => ({
          name: leaf.text,
          url: leaf.href,
          odooCategory: target.odooCategory || mapOdooCategory(leaf.href, leaf.text),
          maxPages: target.maxPages || maxPages,
        }));

    log(`Discovered ${leaves.length} leaf categories for ${target.name || target.url}`);
    const normalizedCategories = leaves.map((category) => ({
        name: category.name,
        url: category.url,
        odooCategory: category.odooCategory || target.odooCategory || mapOdooCategory(category.url, category.name),
        maxPages: category.maxPages || target.maxPages || maxPages,
      }));
    const harvestedCategories = await harvestCategoriesConcurrently(browser, normalizedCategories, {
      concurrency,
      maxPages,
      harvestDate,
      log,
    });
    for (const { category: normalizedCategory, records } of harvestedCategories) {
      allRecords.push(...records);
      categorySummary.push({
        section: target.name || target.url,
        category: normalizedCategory.name,
        url: normalizedCategory.url,
        odooCategory: normalizedCategory.odooCategory,
        records: records.length,
      });
    }
  }

  const deduped = [];
  const seenSku = new Set();
  for (const record of allRecords) {
    const sku = record.product.internal_reference;
    if (!sku || seenSku.has(sku)) continue;
    seenSku.add(sku);
    deduped.push(record);
  }

  const jsonPath = path.join(outDir, `${runSlug}_listing_skeleton.json`);
  const summaryPath = path.join(outDir, `${runSlug}_summary.json`);
  await fs.writeFile(jsonPath, JSON.stringify(deduped, null, 2), "utf8");
  await fs.writeFile(
    summaryPath,
    JSON.stringify(
      {
        runName,
        harvestedAt: new Date().toISOString(),
        targetCount: targets.length,
        rawRows: allRecords.length,
        uniqueSkus: deduped.length,
        categories: categorySummary,
        jsonPath,
      },
      null,
      2,
    ),
    "utf8",
  );

  return {
    jsonPath,
    summaryPath,
    rawRows: allRecords.length,
    uniqueSkus: deduped.length,
    categorySummary,
  };
}
