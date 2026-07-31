from __future__ import annotations

import argparse, base64, csv, os, re, socket, urllib.request, xmlrpc.client
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "odoo_connection.env"
REPORT = ROOT / "odoo_imports" / "product_master" / "sparex" / "run_reports"

def env():
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def call(m, db, uid, key, model, method, args, kw=None):
    return m.execute_kw(db, uid, key, model, method, args, kw or {})

def fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Southern Equipment catalog image sync"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(); typ = r.headers.get("Content-Type", "")
        return data or None
    except Exception: return None

def inferred_source(name: str, sku: str) -> str:
    base=re.sub(r"\s*-\s*Sparex\s+S\.\d+\s*$", "", name or "", flags=re.I)
    slug=re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    number=re.sub(r"\D", "", sku or "")
    return f"https://us.sparex.com/{slug}-{number}.html" if slug and number else ""

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--apply", action="store_true", help="Write exact matched image_1920 values. Default is dry-run/report only.")
    a = p.parse_args()
    socket.setdefaulttimeout(60); env(); u=os.environ["ODOO_URL"].rstrip("/"); db=os.environ["ODOO_DB"]; key=os.environ["ODOO_API_KEY"]
    uid=xmlrpc.client.ServerProxy(u+"/xmlrpc/2/common").authenticate(db, os.environ["ODOO_USERNAME"], key, {})
    m=xmlrpc.client.ServerProxy(u+"/xmlrpc/2/object")
    ids=call(m,db,uid,key,"product.template","search",[[('active','=',True),('sale_ok','=',True),('default_code','=ilike','S.%'),('image_1920','=',False)]],{"context":{"active_test":False}})
    rows=call(m,db,uid,key,"product.template","read",[ids[a.offset:a.offset+a.limit]],{"fields":["default_code","name","description_sale"],"context":{"active_test":False}})
    out=[]
    for row in rows:
        source=re.search(r"Sparex source:\s*(https?://\S+)", row.get("description_sale") or "")
        source_url=source.group(1) if source else inferred_source(row.get("name", ""), row.get("default_code", ""))
        image=None
        if source_url:
            html=fetch(source_url)
            if html:
                match=re.search(rb"https?[^\"'\\ ]*imagelibrary_(?:med|sml)[^\"'\\ ]+?\.(?:jpg|jpeg|png)",html,re.I)
                if match: image=fetch(match.group(0).decode("utf-8","ignore"))
        status="No source image"
        # Legacy duplicates often lack a source URL; an image on the exact same SKU is authoritative.
        if not image and row.get("default_code"):
            donors=call(m,db,uid,key,"product.template","search_read",[[('default_code','=',row['default_code']),('image_1920','!=',False)]],{"fields":["image_1920"],"limit":1,"context":{"active_test":False}})
            if donors: image=base64.b64decode(donors[0]["image_1920"]); status="Copied exact SKU image"
        if image:
            if a.apply:
                call(m,db,uid,key,"product.template","write",[[row["id"]],{"image_1920":base64.b64encode(image).decode()}])
                status="Loaded"
            else:
                status=f"Would load ({status})"
        out.append({"SKU":row.get("default_code",""),"Product ID":row["id"],"Status":status})
    REPORT.mkdir(parents=True,exist_ok=True); path=REPORT/f"sparex_detail_image_backfill_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with path.open("w",newline="",encoding="utf-8") as f: w=csv.DictWriter(f,fieldnames=["SKU","Product ID","Status"]); w.writeheader(); w.writerows(out)
    loaded=sum(x['Status']=='Loaded' for x in out)
    ready=sum(x['Status'].startswith('Would load') for x in out)
    print(f"Results: {path}"); print(f"Mode: {'apply' if a.apply else 'dry_run'}"); print(f"Candidates: {len(out)}"); print(f"Loaded: {loaded}"); print(f"Ready: {ready}")
if __name__ == "__main__": main()
